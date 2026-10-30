"""First contact: who the owner is, proved by a code only an admin can read.

Before this, the owner had to find their numeric Telegram id through a third
party bot and type it into the add-on options before the bot would see them.
Now an install with no allowlist opens pairing: the process prints a
six-digit code in its own log -- the add-on Log tab, the journal on a VPS --
and the first private chat that sends that code becomes the owner.

Why that is as strong as the allowlist it replaces: reading the log takes
the same access as editing the options, so pairing grants nobody a power
they did not already have. The code never goes to Telegram, never into
memory/raw, never anywhere the model can read; the paired id lands in
state/owner.json, which policy keeps out of every tool.

Pairing is single-use. The first success closes it for the life of the
install; a second person is added through the options, by someone who can
edit them. Five wrong codes close it until the next restart, so the million
codes cannot be walked from a chat.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

OWNER_FILE = "owner.json"


class Pairing:
    def __init__(self, state_dir: Path, *, configured: frozenset[int] = frozenset(),
                 ttl_seconds: int = 600, max_attempts: int = 5,
                 clock=time.monotonic) -> None:
        self._path = state_dir / OWNER_FILE
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._clock = clock
        self._attempts = 0
        self._code = ""
        self._expires = 0.0
        # Open only for an install nobody owns yet: no id in the options and
        # none paired before. Decided once, at start.
        self._open = not configured and not self.owners()
        if self._open:
            self._issue()

    @property
    def open(self) -> bool:
        return self._open

    def owners(self) -> frozenset[int]:
        """The ids paired on this install, from state/owner.json."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return frozenset()
        except (OSError, ValueError) as exc:
            # A file that exists and cannot be read must not reopen pairing
            # for whoever messages next: refuse to guess, say why.
            raise SystemExit(f"cannot read {self._path}: {exc}")
        return frozenset(int(i) for i in data.get("user_ids", []))

    def try_pair(self, user_id: int, text: str) -> bool:
        """True when `text` is the live code; the id is then saved as owner."""
        if not self._open:
            return False
        if self._clock() >= self._expires:
            # Expired codes rotate rather than close: the owner may simply
            # have been slow. The new one goes to the log, like the first.
            self._issue()
            return False
        candidate = text.strip()
        if len(candidate) != 6 or not candidate.isdigit():
            # "hi" is the owner saying hello, not a guess: it costs no attempt.
            return False
        if hmac.compare_digest(candidate.encode(), self._code.encode()):
            self._save(user_id)
            self._open = False
            self._code = ""
            log.warning("paired: Telegram user id %s is now the owner; pairing closed", user_id)
            return True
        self._attempts += 1
        if self._attempts >= self._max_attempts:
            self._open = False
            self._code = ""
            log.warning("pairing closed after %d wrong codes; restart to open it again",
                        self._attempts)
        return False

    def _issue(self) -> None:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self._clock() + self._ttl
        # Alone on its line and loud: this is the line the owner is told to
        # look for in the Log tab.
        log.warning("PAIRING CODE: %s  -- send it to the bot within %d minutes",
                    self._code, self._ttl // 60)

    def _save(self, user_id: int) -> None:
        payload = {
            "user_ids": sorted({*self.owners(), user_id}),
            "paired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)
