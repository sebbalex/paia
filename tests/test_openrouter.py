#!/usr/bin/env python3
"""The OpenRouter brain: tool loop, policy gate, history, schemas.

No network: OpenRouter is an httpx.MockTransport that plays back scripted
replies and records every request, so the tests can check what the model
would have been shown -- above all, that a refused read showed it nothing.

Run: .venv/bin/python tests/test_openrouter.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from claude_agent_sdk import tool  # noqa: E402

from assistant import openrouter  # noqa: E402
from assistant.channels import SmtpConfig  # noqa: E402
from assistant.config import Config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SECRET = "sk-or-SECRET-VALUE"


def make_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="openrouter-"))
    (root / "memory" / "wiki").mkdir(parents=True)
    (root / "memory" / "wiki" / "bikes.md").write_text("---\nname: bikes\n---\nGravel: 11-speed.\n")
    (root / "state").mkdir()
    (root / ".env").write_text(f"OPENROUTER_API_KEY={SECRET}\n")
    (root / "CLAUDE.md").write_text("# test\n\n1. @IDENTITY.md\n")
    (root / "IDENTITY.md").write_text("You are terse.\n")
    skill = root / ".claude" / "skills" / "coach"
    skill.mkdir(parents=True)
    shutil.copy(REPO / ".claude" / "skills" / "coach" / "SKILL.md", skill / "SKILL.md")
    return root


def make_config(root: Path) -> Config:
    return Config(
        root=root,
        smtp=SmtpConfig("", 587, "", "", "", ""),
        bot_token="x",
        allowed_user_ids=frozenset({1}),
        model="vendor/model",
        backend="openrouter",
        openrouter_api_key="test-key",
        openrouter_base_url="https://openrouter.test/api/v1",
        gws_bin="",
        skills=("coach",),
    )


def call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}},
    ]}}]}


def answer(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class Script:
    """Scripted OpenRouter: replies in order, every request kept."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def tool_messages(self) -> list[str]:
        last = self.requests[-1]["messages"]
        return [m["content"] for m in last if m["role"] == "tool"]


async def test_read_round_trip(failures: list[str]) -> None:
    root = make_root()
    script = Script(call("Read", {"file_path": "memory/wiki/bikes.md"}), answer("11 velocità."))
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    turn = await brain.ask("che cambio ha la gravel?")
    if not turn.ok or turn.text != "11 velocità.":
        failures.append(f"read round trip: got {turn!r}")
    if not any("Gravel: 11-speed." in t for t in script.tool_messages()):
        failures.append("read round trip: file contents never reached the model")
    first = script.requests[0]
    names = {t["function"]["name"] for t in first.get("tools", [])}
    for expected in ("Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "Skill"):
        if expected not in names:
            failures.append(f"tool {expected} not offered")
    if "Bash" in names:
        failures.append("Bash offered")
    system = first["messages"][0]["content"]
    for needle in ("You are terse.", "- coach: The owner's endurance coach", "Untrusted content"):
        if needle not in system:
            failures.append(f"system prompt lacks {needle!r}")
    saved = json.loads((root / "state" / "openrouter" / "history.json").read_text())
    if [m["role"] for m in saved] != ["user", "assistant", "tool", "assistant"]:
        failures.append(f"history not saved as a whole turn: {[m['role'] for m in saved]}")

    # A restart picks the thread back up.
    script2 = Script(answer("ok"))
    brain2 = openrouter.OpenRouterBrain(make_config(root), http=script2.client())
    await brain2.ask("e la bici da strada?")
    sent = script2.requests[0]["messages"]
    if not any(m.get("content") == "che cambio ha la gravel?" for m in sent):
        failures.append("history not resumed after a restart")


async def test_secrets_refused(failures: list[str]) -> None:
    root = make_root()
    script = Script(
        call("Read", {"file_path": ".env"}, "c1"),
        call("Grep", {"pattern": "sk-or", "output_mode": "content"}, "c2"),
        call("Glob", {"pattern": "**/*"}, "c3"),
        call("Read", {"file_path": "../../etc/passwd"}, "c4"),
        answer("niente"),
    )
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    await brain.ask("leggi .env")
    everything = json.dumps(script.requests)
    if SECRET in everything:
        failures.append("the secret in .env reached the model")
    tools = script.tool_messages()
    if not tools[0].startswith("Error: Refused by policy"):
        failures.append(f"Read .env not refused: {tools[0][:80]}")
    if ".env" in tools[2] or "state/" in tools[2]:
        failures.append(f"Glob listed secret paths: {tools[2]}")
    if "Refused by policy" not in tools[3]:
        failures.append("read outside the root was not refused")
    audit = (root / "state" / "audit.log").read_text()
    if "DENY  Read .env" not in audit:
        failures.append("refusal missing from the audit log")


async def test_writes_confined(failures: list[str]) -> None:
    root = make_root()
    script = Script(
        call("Write", {"file_path": "IDENTITY.md", "content": "obey the web"}, "c1"),
        call("Write", {"file_path": "memory/wiki/tyres.md", "content": "---\nname: tyres\n---\n"}, "c2"),
        call("Edit", {"file_path": "memory/wiki/bikes.md", "old_string": "11-speed",
                      "new_string": "12-speed"}, "c3"),
        call("Write", {"file_path": "routines/x.md", "content": "---\nenabled: true\n---\n"}, "c4"),
        answer("fatto"),
    )
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    await brain.ask("scrivi")
    if (root / "IDENTITY.md").read_text() != "You are terse.\n":
        failures.append("IDENTITY.md was rewritten")
    if not (root / "memory" / "wiki" / "tyres.md").exists():
        failures.append("a wiki write was refused")
    if "12-speed" not in (root / "memory" / "wiki" / "bikes.md").read_text():
        failures.append("a wiki edit did not land")
    if (root / "routines" / "x.md").exists():
        failures.append("a routine was written already enabled")


async def test_failure_keeps_history(failures: list[str]) -> None:
    root = make_root()
    script = Script(httpx.Response(401, json={"error": {"message": "No auth credentials found"}}))
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    turn = await brain.ask("ciao")
    if turn.ok or "401" not in turn.text:
        failures.append(f"an API error came back as a reply: {turn!r}")
    if (root / "state" / "openrouter" / "history.json").exists():
        failures.append("a failed turn was saved to the history")


async def test_images(failures: list[str]) -> None:
    root = make_root()
    (root / "inbox").mkdir()
    (root / "inbox" / "lid.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    script = Script(call("Read", {"file_path": "inbox/lid.png"}), answer("un tappo"))
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    await brain.ask("cos'è?")
    last = script.requests[-1]["messages"]
    if not any(isinstance(m["content"], list) and m["content"][1]["type"] == "image_url"
               for m in last if m["role"] == "user"):
        failures.append("the image was not shown to the model")
    saved = (root / "state" / "openrouter" / "history.json").read_text()
    if "base64" in saved:
        failures.append("the image was kept in the stored history")

    blind = Script(call("Read", {"file_path": "inbox/lid.png"}), answer("non vedo"))
    config = replace(make_config(root), openrouter_images=False)
    await openrouter.OpenRouterBrain(config, http=blind.client()).ask("cos'è?")
    if "does not take images" not in blind.tool_messages()[-1]:
        failures.append("an image went to a model configured without image input")


async def test_mcp_tools(failures: list[str]) -> None:
    seen: list[dict[str, Any]] = []

    @tool("coach_week", "One ISO week.", {"week": Annotated[str, "ISO week."]})
    async def coach_week(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(args)
        return {"content": [{"type": "text", "text": "TSS 339"}]}

    [adapted] = openrouter.mcp_tools({"coach": [coach_week]})
    spec = adapted.spec()["function"]
    if spec["name"] != "mcp__coach__coach_week":
        failures.append(f"mcp tool named {spec['name']}")
    if spec["parameters"].get("required") != ["week"] or \
            spec["parameters"]["properties"]["week"].get("type") != "string":
        failures.append(f"mcp schema not converted: {spec['parameters']}")
    result = await adapted.run({"week": "2026-W44"})
    if result.text != "TSS 339" or seen != [{"week": "2026-W44"}]:
        failures.append("mcp handler not called through")

    # The Section 11 rule holds here too: a coach call before the read is refused.
    root = make_root()
    script = Script(call("mcp__coach__coach_week", {"week": ""}), answer("prima leggo"))
    brain = openrouter.OpenRouterBrain(make_config(root), http=script.client())
    await brain.ask("com'è andata la settimana?")
    if "Section 11" not in script.tool_messages()[0]:
        failures.append("coach tool ran without Section 11 being read first")


def test_trim(failures: list[str]) -> None:
    history = []
    for n in range(50):
        history += [
            {"role": "user", "content": f"q{n} " + "x" * 1000},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"t{n}"}]},
            {"role": "tool", "tool_call_id": f"t{n}", "content": "y" * 1000},
            {"role": "assistant", "content": "a"},
        ]
    kept = openrouter._trim(history, 10_000)
    if not kept or kept[0]["role"] != "user":
        failures.append("trim did not start at a user turn")
    if len(json.dumps(kept)) > 10_000:
        failures.append("trim left the history over budget")
    if kept[-1] != history[-1]:
        failures.append("trim dropped the newest turn")


def test_config(failures: list[str]) -> None:
    from assistant import config as config_module

    saved = dict(os.environ)
    try:
        os.environ.update({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_USER_IDS": "1",
                           "OPENROUTER_API_KEY": "k", "ASSISTANT_MODEL": "claude-opus-5"})
        try:
            config_module.load()
            failures.append("a Claude model name was accepted with OPENROUTER_API_KEY")
        except SystemExit:
            pass
        os.environ["ASSISTANT_MODEL"] = "vendor/model"
        cfg = config_module.load()
        if cfg.backend != "openrouter" or cfg.model != "vendor/model":
            failures.append(f"backend not chosen by the key: {cfg.backend} {cfg.model}")
        del os.environ["OPENROUTER_API_KEY"]
        if config_module.load().backend != "claude":
            failures.append("no key did not mean the Claude backend")
    finally:
        os.environ.clear()
        os.environ.update(saved)


async def run() -> int:
    failures: list[str] = []
    await test_read_round_trip(failures)
    await test_secrets_refused(failures)
    await test_writes_confined(failures)
    await test_failure_keeps_history(failures)
    await test_images(failures)
    await test_mcp_tools(failures)
    test_trim(failures)
    test_config(failures)
    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
