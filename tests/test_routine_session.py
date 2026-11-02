#!/usr/bin/env python3
"""A routine runs in a session of its own, never in the chat's.

In the chat session the weekly coach routine saw its own run from half an hour
earlier, judged a second report a duplicate and filed none. Both backends must
start a routine from nothing and leave the chat where it was.

Run: .venv/bin/python tests/test_routine_session.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from claude_agent_sdk import ResultMessage  # noqa: E402

from assistant import openrouter  # noqa: E402
from assistant.agent import Brain  # noqa: E402
from assistant.channels import SmtpConfig  # noqa: E402
from assistant.config import Config  # noqa: E402


def make_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="routine-session-"))
    (root / "memory" / "wiki").mkdir(parents=True)
    (root / "state").mkdir()
    return root


class FakeClient:
    """Stands in for ClaudeSDKClient: records what it was asked and how opened."""

    made: list["FakeClient"] = []

    def __init__(self, options) -> None:
        self.resume = options.resume
        self.prompts: list[str] = []
        self.connected = False
        self.closed = False
        FakeClient.made.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.closed = True

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self):
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=f"session-{len(FakeClient.made)}",
        )


async def test_claude(failures: list[str]) -> None:
    root = make_root()
    config = Config(
        root=root, smtp=SmtpConfig("", 587, "", "", "", ""), bot_token="x",
        allowed_user_ids=frozenset({1}), model="claude-test", gws_bin="",
    )
    brain = Brain(config)
    FakeClient.made = []
    brain._client_factory = FakeClient
    # The chat session goes through _ensure_client, which builds its client
    # directly: plant one so the chat turn stays on it.
    chat = FakeClient(brain._options(None))
    brain._client = chat
    await brain.ask("ciao")
    chat_session = (root / "state" / "session_id").read_text()

    turn = await brain.ask("weekly report", routine=True)
    if not turn.ok:
        failures.append(f"routine turn failed: {turn!r}")
    routine = FakeClient.made[-1]
    if routine is chat:
        failures.append("the routine ran in the chat session")
    if "weekly report" in chat.prompts:
        failures.append("the routine prompt reached the chat session")
    if routine.resume is not None:
        failures.append(f"the routine resumed a session: {routine.resume}")
    if not (routine.connected and routine.closed):
        failures.append("the routine session was not opened and closed")
    if brain._client is not chat or chat.closed:
        failures.append("the routine closed or replaced the chat session")
    if (root / "state" / "session_id").read_text() != chat_session:
        failures.append("the routine overwrote the chat session id")
    if brain._policy._routine is not True:
        failures.append("the routine turn was not marked as one for the policy")

    await brain.ask("e adesso?")
    if chat.prompts != ["ciao", "e adesso?"] or brain._policy._routine:
        failures.append(f"the chat did not resume as a chat turn: {chat.prompts}")


async def test_openrouter(failures: list[str]) -> None:
    root = make_root()
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    config = Config(
        root=root, smtp=SmtpConfig("", 587, "", "", "", ""), bot_token="x",
        allowed_user_ids=frozenset({1}), model="vendor/model", backend="openrouter",
        openrouter_api_key="k", openrouter_base_url="https://openrouter.test/api/v1",
        gws_bin="",
    )
    brain = openrouter.OpenRouterBrain(
        config, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await brain.ask("ciao")
    await brain.ask("weekly report", routine=True)
    seen = [m.get("content").split("]\n", 1)[-1] for m in requests[-1]["messages"] if m["role"] == "user"]
    if seen != ["weekly report"]:
        failures.append(f"the routine saw the chat history: {seen}")
    await brain.ask("e adesso?")
    seen = [m.get("content").split("]\n", 1)[-1] for m in requests[-1]["messages"] if m["role"] == "user"]
    if seen != ["ciao", "e adesso?"]:
        failures.append(f"the routine was kept in the chat history: {seen}")


def test_usage_line(failures: list[str]) -> None:
    from assistant.agent import usage_line
    result = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=3, session_id="s", total_cost_usd=0.01234,
        usage={"input_tokens": 12, "cache_read_input_tokens": 9000,
               "cache_creation_input_tokens": 400, "output_tokens": 50},
    )
    want = "3 calls, 12 in, 9000 cache read, 400 cache write, 50 out, $0.0123"
    if usage_line(result) != want:
        failures.append(f"usage line: {usage_line(result)!r}")
    bare = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=False, num_turns=1, session_id="s")
    if usage_line(bare) != "1 calls, 0 in, 0 cache read, 0 cache write, 0 out":
        failures.append(f"usage line without usage: {usage_line(bare)!r}")


async def run() -> int:
    failures: list[str] = []
    test_usage_line(failures)
    await test_claude(failures)
    await test_openrouter(failures)
    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
