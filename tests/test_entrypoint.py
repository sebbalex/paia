#!/usr/bin/env python3
"""Add-on entrypoint: options to environment, and the one-shot import from the share.

Run: .venv/bin/python tests/test_entrypoint.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker"))

import entrypoint as E  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def _setup(tmp: Path) -> tuple[Path, Path]:
    root, config = tmp / "data" / "paia", tmp / "config"
    (root / "memory" / "wiki").mkdir(parents=True)
    (root / "routines").mkdir()
    (root / "memory" / "wiki" / "index.md").write_text("skeleton\n")
    E.ROOT, E.CONFIG = root, config
    return root, config


def test_import() -> None:
    print("import_state")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        root, config = _setup(tmp)
        inbox = config / "import"
        (inbox / "memory" / "wiki").mkdir(parents=True)
        (inbox / "memory" / "raw").mkdir(parents=True)
        (inbox / "routines").mkdir()
        (inbox / "context").mkdir()
        (inbox / "memory" / "wiki" / "index.md").write_text("mine\n")
        (inbox / "memory" / "wiki" / "training.md").write_text("ftp\n")
        (inbox / "memory" / "wiki" / ".DS_Store").write_text("junk")
        (inbox / "memory" / "raw" / "2026-10-01.md").write_text("said\n")
        (inbox / "routines" / "coach-weekly.md").write_text("---\n")
        (inbox / "context" / "shopping-rules.md").write_text("brief\n")
        secret = tmp / "options.json"
        secret.write_text('{"token": "x"}')
        (inbox / "memory" / "wiki" / "leak.md").symlink_to(secret)

        E.import_state()

        wiki = root / "memory" / "wiki"
        check("overwrites skeleton", (wiki / "index.md").read_text() == "mine\n")
        check("adds new page", (wiki / "training.md").is_file())
        check("raw imported", (root / "memory" / "raw" / "2026-10-01.md").is_file())
        check("routines imported", (root / "routines" / "coach-weekly.md").is_file())
        check("dotfile skipped", not (wiki / ".DS_Store").exists())
        check("symlink skipped", not (wiki / "leak.md").exists())
        check("context not imported", not (root / "context").exists())
        check("inbox removed", not inbox.exists())

        # A second start with nothing to import touches nothing.
        (wiki / "index.md").write_text("newer\n")
        E.import_state()
        check("no inbox, no change", (wiki / "index.md").read_text() == "newer\n")


_PASSTHROUGH = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENROUTER_API_KEY",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "ASSISTANT_MODEL",
                "ASSISTANT_SKILLS")
_BASE = {"telegram_bot_token": "123:abc", "telegram_allowed_user_ids": ["7"],
         "model": "claude-opus-5", "llm_provider": "claude_subscription"}


def _env(tmp: Path, **options) -> dict[str, str] | str:
    """build_env() over these options, or the SystemExit message."""
    path = tmp / "options.json"
    path.write_text(json.dumps({**_BASE, **options}))
    E.OPTIONS = path
    try:
        return E.build_env()
    except SystemExit as exc:
        return str(exc)


def test_llm() -> None:
    print("build_env: llm_provider + llm_credential")
    for name in _PASSTHROUGH:
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for provider, (variable, prefix) in E.LLM_PROVIDERS.items():
            model = "deepseek/deepseek-chat" if provider == "openrouter" else "claude-opus-5"
            env = _env(tmp, llm_provider=provider, llm_credential=f"{prefix}xyz", model=model)
            others = [v for v, _ in E.LLM_PROVIDERS.values() if v != variable]
            check(f"{provider} -> {variable}",
                  isinstance(env, dict) and env.get(variable) == f"{prefix}xyz"
                  and not any(env.get(o) for o in others), str(env))

        out = _env(tmp, llm_provider="claude_subscription", llm_credential="sk-ant-api03-x")
        check("api key under subscription refused",
              isinstance(out, str) and "set llm_provider to anthropic_api" in out, str(out))
        out = _env(tmp, llm_provider="anthropic_api", llm_credential="hello")
        check("unknown prefix names the expected one",
              isinstance(out, str) and "starts with sk-ant-api" in out, str(out))
        out = _env(tmp, llm_credential="sk-ant-oat-x", anthropic_api_key="sk-ant-api-y")
        check("new pair beside legacy field refused",
              isinstance(out, str) and "anthropic_api_key" in out, str(out))
        out = _env(tmp, llm_provider="", llm_credential="sk-ant-oat-x")
        check("credential without provider refused",
              isinstance(out, str) and "Pick one of" in out, str(out))
        out = _env(tmp, llm_provider="openrouter", llm_credential="sk-or-x")
        check("openrouter with a non-slug model refused at the entrypoint",
              isinstance(out, str) and "not an OpenRouter slug" in out, str(out))

        # An install from before the pair: the Supervisor merged the default
        # provider in, the credential is empty, the old field does the work.
        env = _env(tmp, claude_oauth_token="sk-ant-oat-old")
        check("legacy field still works",
              isinstance(env, dict) and env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat-old",
              str(env))
        env = _env(tmp, openrouter_api_key="sk-or-old", model="vendor/m")
        check("legacy openrouter still works",
              isinstance(env, dict) and env.get("OPENROUTER_API_KEY") == "sk-or-old", str(env))
        out = _env(tmp)
        check("no credential at all refused",
              isinstance(out, str) and "llm_credential" in out, str(out))

        env = _env(tmp, llm_credential="sk-ant-oat-x", telegram_allowed_user_ids=[])
        check("empty allowlist starts: pairing takes over",
              isinstance(env, dict) and not env.get("TELEGRAM_ALLOWED_USER_IDS"), str(env))
        out = _env(tmp, llm_credential="sk-ant-oat-x", telegram_allowed_user_ids=["@me"])
        check("a non-numeric id still refused",
              isinstance(out, str) and "not a numeric" in out, str(out))
        out = _env(tmp, llm_credential="sk-ant-oat-x", telegram_bot_token="")
        check("an empty bot token still refused",
              isinstance(out, str) and "telegram_bot_token" in out, str(out))

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-from-env"
        env = _env(tmp, llm_credential="sk-ant-oat-x")
        check("options beat a stale credential in the environment",
              isinstance(env, dict) and "ANTHROPIC_API_KEY" not in env
              and env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat-x", str(env))
        os.environ.pop("ANTHROPIC_API_KEY")


def test_brief_sync() -> None:
    print("\nbriefs: share -> context/ while running")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        root, config = _setup(tmp)
        share = config / "context"
        share.mkdir(parents=True)
        (share / "training-rules.md").write_text("old\n")
        walked: list[Path] = []
        real_walk = E._walk
        E._walk = lambda path, *a, **k: walked.append(path)  # chown to root needs root
        try:
            last = E.sync_briefs_if_changed(0.0)
            brief = root / "context" / "training-rules.md"
            check("first sync copies", brief.read_text() == "old\n")
            check("modes put back on context/", walked == [root / "context"], str(walked))
            check("no change, no sync", E.sync_briefs_if_changed(last) == last)

            os.utime(share / "training-rules.md", (last + 5, last + 5))
            (share / "training-rules.md").write_text("accepted\n")
            os.utime(share / "training-rules.md", (last + 5, last + 5))
            last = E.sync_briefs_if_changed(last)
            check("a changed brief arrives without a restart", brief.read_text() == "accepted\n")

            (share / "training-rules.md").unlink()
            os.utime(share, (last + 10, last + 10))
            E.sync_briefs_if_changed(last)
            check("a brief removed from the share goes", not brief.exists())
        finally:
            E._walk = real_walk

        E.OPTIONS = tmp / "none.json"
        for name in _PASSTHROUGH:
            os.environ.pop(name, None)
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat-x"
        os.environ["TELEGRAM_BOT_TOKEN"] = "1:a"
        env = E.build_env()
        check("accept target points at the share",
              env.get("ASSISTANT_CONTEXT_TARGET") == str(share), str(env.get("ASSISTANT_CONTEXT_TARGET")))
        for name in _PASSTHROUGH:
            os.environ.pop(name, None)


def main() -> int:
    test_import()
    test_llm()
    test_brief_sync()
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
