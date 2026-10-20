#!/usr/bin/env python3
"""Chart and report-mail tests. Run: .venv/bin/python tests/test_charts.py

Synthetic Intervals.icu payloads in, PNGs and a MIME message out. No network.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import coach_charts as cc  # noqa: E402
from assistant.channels import build_email  # noqa: E402

FAILURES: list[str] = []
PNG = b"\x89PNG\r\n\x1a\n"


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def main() -> int:
    today = date(2026, 10, 25)  # a Sunday
    monday = today - timedelta(days=6)
    rows = []
    for i in range(120):
        d = today - timedelta(days=i)
        rows.append({"id": d.isoformat(), "ctl": 50 + i % 7, "atl": 55 - i % 5,
                     "atlLoad": 60, "hrv": 50 + i % 9, "restingHR": 46 + i % 4,
                     "sportInfo": [{"type": "Ride", "eftp": 254 + i % 3}]})
    acts = [
        {"start_date_local": f"{monday.isoformat()}T12:00:00", "type": "Ride",
         "icu_training_load": 75, "moving_time": 3900, "icu_zone_times":
         [{"id": f"Z{z}", "secs": s} for z, s in zip(range(1, 8), [600, 1200, 900, 600, 300, 60, 0])]},
        {"start_date_local": f"{(monday + timedelta(days=2)).isoformat()}T18:00:00",
         "type": "WeightTraining", "icu_training_load": 30, "moving_time": 3600},
        {"source": "STRAVA", "start_date_local": f"{(monday + timedelta(days=5)).isoformat()}T09:00:00",
         "_note": "hidden"},
    ]
    curve = {"profile": [
        {"duration": "sprint_5s", "42d_w": 846, "42d_pct_of_1y": 76},
        {"duration": "threshold_20m", "42d_w": 265, "42d_pct_of_1y": 95},
    ]}

    print("charts")
    made = [
        cc.daily_load(monday, acts),
        cc.fitness(rows, today),
        cc.intensity({"tid_7d": {"easy_pct": 60, "grey_pct": 23, "quality_pct": 17, "seconds": 9},
                      "tid_28d": {"easy_pct": 79, "grey_pct": 13, "quality_pct": 8, "seconds": 9}}),
        cc.recovery(rows, today, None),
        cc.power_profile(curve),
        cc.trend({"ftp_setting_ride": 273, "weeks": [
            {"week": "2026-W41", "eftp_ride": 255, "ride_hours_visible": 5.3},
            {"week": "2026-W42", "eftp_ride": None, "ride_hours_visible": 7.3}]}),
    ]
    for c in made:
        check(f"png: {c.title}", c.png.startswith(PNG) and len(c.png) > 5000, str(len(c.png)))
    check("titles match the list routines read",
          [c.title for c in made] == cc.TITLES["coach-week"], str([c.title for c in made]))
    check("filenames are plain ascii", all(c.filename.isascii() for c in made),
          str([c.filename for c in made]))
    empty = [cc.fitness([], today), cc.intensity({}), cc.recovery([], today, None),
             cc.power_profile({}), cc.trend({})]
    check("no data still draws a placeholder", all(c.png.startswith(PNG) for c in empty), "")

    print("\nreport mail")
    msg = build_email("Routine coach-weekly", "**La settimana** in breve\nriga due",
                      "bot@x", "me@x", made[:2])
    html = msg.get_body(("html",))
    check("has a plain-text part", msg.get_body(("plain",)) is not None, "")
    check("html renders markdown", "<b>La settimana</b>" in html.get_content(), "")
    cids = [p["Content-ID"] for p in msg.walk() if p.get_content_type() == "image/png"]
    check("two inline images", len(cids) == 2, str(cids))
    check("each image referenced by cid",
          all(f"cid:{c[1:-1]}" in html.get_content() for c in cids), "")
    nofrom = build_email("s", "body", None, "b")
    check("no sender: From left to Gmail", nofrom["From"] is None, str(nofrom["From"]))

    print("\ngws mailer")
    import asyncio, os, stat, tempfile
    from assistant.channels import Delivery, GwsMailer, SmtpConfig
    tmp = Path(tempfile.mkdtemp(prefix="gwsmail-"))
    fake = tmp / "gws"
    log = tmp / "argv"
    fake.write_text(f"""#!/bin/sh
echo "$@" > {log}
cp upload {tmp}/sent.eml
echo '{{"id":"abc"}}'
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    mailer = GwsMailer(str(fake), "me@example.com")

    class Tg:
        sent = []
        async def send(self, chat_id, text): self.sent.append(text)
        async def send_photos(self, chat_id, photos): self.sent.append(len(photos))

    tg = Tg()
    smtp = SmtpConfig("", 587, "", "", "", "")
    d = Delivery(tg, 1, smtp, mailer)
    check("email available through gws", d.available("email"), "")
    asyncio.run(d.send("email", "Routine x", "**ciao**", made[:2]))
    argv = log.read_text()
    check("uses the upload endpoint as rfc822",
          "messages send" in argv and "message/rfc822" in argv, argv)
    sent = (tmp / "sent.eml").read_bytes()
    check("to the fixed recipient", b"To: me@example.com" in sent, "")
    check("charts travel inline", sent.count(b"Content-ID:") == 2, "")
    check("nothing on telegram when mail works", tg.sent == [], str(tg.sent))
    fake.write_text("#!/bin/sh\necho boom >&2\nexit 1\n")
    asyncio.run(d.send("email", "Routine x", "body", made[:1]))
    check("gws failure falls back to telegram, charts too",
          any("fallito" in str(m) for m in tg.sent) and 1 in tg.sent, str(tg.sent))
    check("no mailer, no smtp: email unavailable",
          not Delivery(tg, 1, smtp).available("email"), "")

    plain = build_email("s", "body", "a", "b")
    check("no charts: plain mail only", not plain.is_multipart(), "")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("charts ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
