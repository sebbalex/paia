#!/usr/bin/env python3
"""Add-on entrypoint: the one-shot import of memory and routines from the share.

Run: .venv/bin/python tests/test_entrypoint.py
"""

from __future__ import annotations

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


def main() -> int:
    test_import()
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
