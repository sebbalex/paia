#!/usr/bin/env python3
"""Onboarding: filling briefs, rendering .env, the add-on options.

Run: .venv/bin/python tests/test_onboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from onboard import (  # noqa: E402
    CALENDAR_BULLET,
    REPO,
    addon_options,
    calendar_wiki,
    fill,
    folder_id,
    render_env,
    unfilled,
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def brief(name: str) -> str:
    return (REPO / "defaults" / "context" / f"{name}.md").read_text()


def test_fill() -> None:
    print("fill")
    text = brief("shopping-rules")
    out = fill(text, "Ship to", "Italy, EU sellers")
    check("hint wrapping a line goes", "- Ship to: Italy, EU sellers.\n" in out, out[:600])
    check("rest untouched", out.count("\n") == text.count("\n") - 1)
    check("filled is no longer open", not unfilled(out, "Ship to"))
    check("second fill is a no-op", fill(out, "Ship to", "France") == out)

    out = fill(brief("adventures-rules"), "folder", "AbC_1")
    check("backticked token stays code", "- folder: `AbC_1` — the Drive folder id" in out)

    out = fill(brief("calendar-rules"), "Timezone is", "`Europe/Rome`")
    check("value mid-sentence", "- Timezone is `Europe/Rome` for everything." in out)
    check("calendar bullet is still in the template", CALENDAR_BULLET in brief("calendar-rules"))

    text = brief("training-rules")
    check("blockquote is not a question", not unfilled(text, "Lines marked"))
    out = fill(text, "injuries and niggles", "left knee")
    check("label prefix matches", "- injuries and niggles to train around: left knee\n" in out)
    check("unknown label unchanged", fill(text, "nonsense", "x") == text)


def test_env() -> None:
    print("env")
    template = (REPO / ".env.example").read_text()
    out = render_env(
        template,
        {"ANTHROPIC_API_KEY": "sk-ant-api03-k", "CLAUDE_CODE_OAUTH_TOKEN": "", "OWNER_NAME": "Ada", "NEW_KEY": "1"},
    )
    lines = out.splitlines()
    check("commented key set", "ANTHROPIC_API_KEY=sk-ant-api03-k" in lines)
    check("emptied key commented", "# CLAUDE_CODE_OAUTH_TOKEN=" in lines)
    check("unanswered placeholder commented", "TELEGRAM_BOT_TOKEN=123456:ABC..." not in lines)
    check("indented examples kept", "#   SCRAPER_URL=http://127.0.0.1:8099" in lines)
    check("unknown key appended", lines[-1] == "NEW_KEY=1")
    check("comments kept", out.count("\n#") >= template.count("\n#") - 2)


def test_rest() -> None:
    print("rest")
    check("folder from URL", folder_id("https://drive.google.com/drive/folders/1Ab_c-D?usp=x") == "1Ab_c-D")
    check("folder id passes", folder_id(" 1Ab ") == "1Ab")
    rows = [
        {"summary": "Home", "id": "a@group.calendar.google.com", "timeZone": "Europe/Rome", "surface": "always"},
        {"summary": "Typed", "id": "", "timeZone": "", "surface": "always"},
    ]
    wiki = calendar_wiki((REPO / "defaults" / "memory" / "wiki" / "calendar.md").read_text(), rows)
    check("calendar row written", "| Home | `a@group.calendar.google.com` | Europe/Rome | always |" in wiki)
    check("row without id skipped", "Typed" not in wiki)
    check("table not written twice", calendar_wiki(wiki, rows) == wiki)
    opts = addon_options({"TELEGRAM_ALLOWED_USER_IDS": "1,2", "ASSISTANT_SKILLS": "coach", "OWNER_NAME": "Ada"})
    check("ids are ints", "telegram_allowed_user_ids: [1, 2]" in opts)
    check("skills are strings", 'skills: ["coach"]' in opts)
    check("empty options left out", "mail_to" not in opts)


if __name__ == "__main__":
    test_fill()
    test_env()
    test_rest()
    if FAILURES:
        print("\n".join(["", "FAILED:", *FAILURES]))
        sys.exit(1)
    print("\nall ok")
