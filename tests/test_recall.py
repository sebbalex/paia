#!/usr/bin/env python3
"""Recall index: exchanges, dates, and nothing from after `now`.

Words only (no embedding model), so it runs anywhere in a second.
Run: .venv/bin/python tests/test_recall.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import recall as R  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


DAY1 = """# 2026-09-23

### owner — 08:55:29

L'appartamento ha rifiutato. Prenotato Pensione Klara a Villabassa.

### assistant — 08:56:10

Annotato.

### Day 0 — Villabassa

A heading inside a reply, not a new entry.

### owner — 09:27:01

210€ in totale con colazione.

### assistant — 09:28:00

Scritto nel documento.
"""

DAY2 = """# 2026-10-28

### assistant — 09:00:00

Routine output with no owner message before it.

### owner — 09:41:14

Domattina parto per Zurigo, vorrei una corsetta leggera.
"""


def test_parse(tmp: Path) -> None:
    print("parse")
    raw = tmp / "memory" / "raw"
    raw.mkdir(parents=True)
    (raw / "2026-09-23.md").write_text(DAY1)
    (raw / "2026-10-28.md").write_text(DAY2)
    units = R.parse_raw(raw / "2026-09-23.md")
    check("one unit per owner message", [u.id for u in units]
          == ["raw:2026-09-23T08:55:29", "raw:2026-09-23T09:27:01"], str([u.id for u in units]))
    check("reply heading stays inside the exchange", "A heading inside a reply" in units[0].text)
    units = R.parse_raw(raw / "2026-10-28.md")
    check("routine output opens its own unit", units[0].id == "raw:2026-10-28T09:00:00")
    check("long text splits into passages",
          len(R.passages("\n\n".join(["parola " * 100] * 5))) > 1)


def test_dates() -> None:
    print("dates")
    now = datetime(2026, 10, 28, 20, 0)
    cases = {
        "com'è andata ieri?": (date(2026, 10, 27), date(2026, 10, 27)),
        "di cosa abbiamo parlato il 21 agosto?": (date(2026, 8, 21), date(2026, 8, 21)),
        "what did I send you on 1 September?": (date(2026, 9, 1), date(2026, 9, 1)),
        "il 30/09 dove dormiamo": (date(2026, 9, 30), date(2026, 9, 30)),
        "a dicembre": None,
        "prezzo delle gomme": None,
    }
    for q, want in cases.items():
        got = R.date_window(q, now)
        check(f"window: {q}", got == want, f"{got} != {want}")
    check("a date not yet reached this year is last year's",
          R.date_window("il 25 dicembre", now) == (date(2025, 12, 25), date(2025, 12, 25)))
    check("topic drops the steering", R.topic("l'ultima volta che abbiamo parlato di prezzi")
          .split()[-1] == "prezzi")


def test_search(tmp: Path) -> None:
    print("search (words only)")
    (tmp / "memory" / "wiki").mkdir(parents=True, exist_ok=True)
    (tmp / "memory" / "wiki" / "adventures.md").write_text(
        "---\nname: adventures\nupdated: 2026-10-22\n---\nAlta Via 1 done. Night 0 at Pensione Klara.\n")
    r = R.Recall(tmp, tmp / "state" / "recall.db", tmp / "models", embed=False)
    check("first refresh indexes everything", r.refresh() == 3)
    check("unchanged files are skipped", r.refresh() == 0)

    now = datetime(2026, 10, 29, 12, 0)
    ids = [x["id"] for x in r.search("Pensione Klara", 5, now)]
    check("finds the exchange", "raw:2026-09-23T08:55:29" in ids, str(ids))
    check("wiki page in its own slot", "wiki:adventures" in ids, str(ids))

    before = datetime(2026, 9, 23, 9, 0)
    ids = [x["id"] for x in r.search("Klara colazione", 5, before)]
    check("nothing after now", all(i <= "raw:2026-09-23T09:00" for i in ids if i.startswith("raw:"))
          and "raw:2026-09-23T09:27:01" not in ids, str(ids))
    check("wiki updated after now is hidden", "wiki:adventures" not in ids, str(ids))

    ids = [x["id"] for x in r.search("di cosa abbiamo parlato il 28 ottobre?", 5, now)]
    check("a bare date returns that day", ids and all(i.startswith("raw:2026-10-28") for i in ids
                                                      if i.startswith("raw:")), str(ids))

    (tmp / "memory" / "raw" / "2026-10-28.md").write_text(DAY2 + "\n### owner — 10:00:00\n\nZurigo confermato.\n")
    check("an appended file is re-read", r.refresh() == 1)
    ids = [x["id"] for x in r.search("Zurigo confermato", 5, now)]
    check("new exchange is found", "raw:2026-10-28T10:00:00" in ids, str(ids))


def main() -> int:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_parse(tmp)
        test_dates()
        test_search(tmp)
    if FAILURES:
        print("\n".join(["", "FAILURES:", *FAILURES]))
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
