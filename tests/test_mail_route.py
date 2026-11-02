#!/usr/bin/env python3
"""Which way `channel: email` goes: SMTP when configured, else gws, else none.

Run: .venv/bin/python tests/test_mail_route.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import config as C  # noqa: E402
from assistant.main import _mailer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


BASE = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_IDS": "7",
        "ANTHROPIC_API_KEY": "k"}
SMTP = {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "bot@example.com"}


def load(extra: dict):
    keep = {k: v for k, v in os.environ.items()
            if not k.startswith(("SMTP_", "MAIL_TO"))}
    with mock.patch.dict(os.environ, {**keep, **BASE, **extra}, clear=True):
        return C.load()


def main() -> int:
    # The case that broke: the form's Mail to plus an SMTP server, no legacy SMTP to.
    cfg = load({"MAIL_TO": "me@example.com", **SMTP})
    check("smtp configured from MAIL_TO", cfg.smtp.configured, repr(cfg.smtp))
    check("smtp recipient is MAIL_TO", cfg.smtp.recipient == "me@example.com")
    check("no gws mailer when smtp is set", _mailer(cfg) is None)

    cfg = load({"SMTP_TO": "old@example.com", **SMTP})
    check("legacy SMTP_TO still works", cfg.smtp.configured and _mailer(cfg) is None)

    cfg = load({"MAIL_TO": "me@example.com", "SMTP_TO": "old@example.com", **SMTP})
    check("MAIL_TO wins over SMTP_TO", cfg.smtp.recipient == "me@example.com")

    cfg = load({"MAIL_TO": "me@example.com"})
    m = _mailer(cfg)
    check("gws without smtp", m is not None and m.recipient == "me@example.com")

    cfg = load({})
    check("nothing set, no mail", _mailer(cfg) is None and not cfg.smtp.configured)

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
