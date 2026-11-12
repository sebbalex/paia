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
    pace = cc.pace_profile({"profile": [
        {"distance": "1k", "42d_time": "3:58", "42d_pct_of_1y": 97},
        {"distance": "5k", "42d_time": "21:40", "42d_pct_of_1y": 94}],
        "critical_speed": {"42d": {"cs_pace": "4:16/km", "trustworthy": False}}})
    check("pace profile draws", pace.png.startswith(PNG) and pace.title == cc.PACE_TITLE, pace.title)
    run_acts = [{"type": "Run", "start_date_local": f"{today.isoformat()}T07:00:00", "moving_time": 3600}]
    check("main sport: running when it has the hours", cc.main_sport(run_acts + acts, today), "running")
    check("main sport: bike on no data", cc.main_sport([], today), "cycling")
    empty = [cc.fitness([], today), cc.intensity({}), cc.recovery([], today, None),
             cc.power_profile({}), cc.pace_profile({}), cc.trend({})]
    check("no data still draws a placeholder", all(c.png.startswith(PNG) for c in empty), "")

    print("\nworkout profiles")
    text = """Set 1 8x
- 30s 330-350w (121-128%)
- 15s 50%

- 10m ramp 55-40%
- 5m Z2
- 5m 70% HR
"""
    steps = cc.parse_workout(text)
    check("repeat block parsed", steps[0].get("reps") == 8 and len(steps[0]["steps"]) == 2, str(steps))
    check("first target wins: watts, not the % gloss",
          steps[0]["steps"][0]["power"] == {"units": "w", "start": 330, "end": 350}, str(steps[0]))
    check("blank line closes the repeat", steps[1].get("ramp") is True, str(steps[1]))
    check("zone target", steps[2]["power"] == {"units": "power_zone", "value": 2}, str(steps[2]))
    check("HR target is not power", "power" not in steps[3], str(steps[3]))
    check("flattened by reps", len(cc._flat(steps)) == 19, str(len(cc._flat(steps))))
    cal = [{"category": "WORKOUT", "name": "VO2 30/15", "start_date_local": "2026-10-28T00:00:00",
            "icu_training_load": 40, "workout_doc": {"steps": [{"duration": 600, "power":
            {"units": "%ftp", "value": 60}}]}},
           {"category": "WORKOUT", "name": "Soglia", "start_date_local": "2026-10-27T00:00:00",
            "workout_doc": {"steps": steps}},
           {"category": "WORKOUT", "name": "Gambe", "type": "WeightTraining",
            "start_date_local": "2026-10-26T00:00:00", "workout_doc": {"steps": []}}]
    profiles = cc.planned_workouts(cal, 273, None)
    check("calendar workouts in date order, gym without steps skipped",
          [c.title for c in profiles] == ["Allenamento: Soglia, mar 27 ott",
                                          "Allenamento: VO2 30/15, mer 28 ott"],
          str([c.title for c in profiles]))
    check("profiles are pngs", all(c.png.startswith(PNG) for c in profiles), "")
    check("no ftp: placeholder, not a crash",
          cc.workout_profile("x", None, steps, None).png.startswith(PNG), "")

    print("\nrun workout profiles")
    run_text = """- 15m Z2 Pace
- 1km 100-104% Pace

Main set 4x
- 3m 4:05-3:58/km Pace
- 2m 70% Pace

- 400mtr HR Z2
- 10m ramp 80-90% Pace
- 5m 70% HR
"""
    rs = cc.parse_workout(run_text)
    check("pace zone target", rs[0]["pace"] == {"units": "pace_zone", "value": 2}, str(rs[0]))
    check("distance step with % pace",
          rs[1].get("distance") == 1000 and rs[1]["pace"] == {"units": "%pace", "start": 100, "end": 104},
          str(rs[1]))
    check("min/km range", rs[2]["steps"][0]["pace"] == {"units": "secs_km", "start": 245, "end": 238},
          str(rs[2]))
    check("pace is not read as power", "power" not in rs[2]["steps"][1], str(rs[2]["steps"][1]))
    check("400mtr is metres, not minutes", rs[3].get("distance") == 400 and "duration" not in rs[3],
          str(rs[3]))
    check("pace ramp", rs[4].get("ramp") is True and rs[4]["pace"]["units"] == "%pace", str(rs[4]))
    check("HR step on a run has no pace", "pace" not in rs[5] and "power" not in rs[5], str(rs[5]))
    zones = [77.5, 87.7, 94.3, 100, 103.4, 111.5, 999]
    lo, hi = cc._speeds({"units": "pace_zone", "value": 4}, 4.0, zones)
    check("Z4 pace spans its band", (round(lo, 3), round(hi, 3)) == (3.772, 4.0), str((lo, hi)))
    check("zone without pace zones unresolved",
          cc._speeds({"units": "pace_zone", "value": 2}, 4.0, None) is None, "")
    rp = cc.run_workout_profile("Soglia corsa", date(2026, 11, 17), rs, 4.0, zones, 55)
    check("run profile draws", rp.png.startswith(PNG) and len(rp.png) > 5000, str(len(rp.png)))
    check("run profile without threshold: placeholder",
          cc.run_workout_profile("x", None, rs, None).png.startswith(PNG), "")
    run_cal = [{"category": "WORKOUT", "type": "Run", "name": "Soglia corsa",
                "start_date_local": "2026-10-29T00:00:00", "description": run_text,
                "workout_doc": {"steps": [{"duration": 900}]}}]
    rprof = cc.planned_workouts(run_cal + cal, 273, None, {"threshold_pace": 4.0, "pace_zones": zones})
    check("run workouts join the ride profiles in date order",
          [c.title for c in rprof] == ["Allenamento: Soglia, mar 27 ott",
                                       "Allenamento: VO2 30/15, mer 28 ott",
                                       "Allenamento: Soglia corsa, gio 29 ott"],
          str([c.title for c in rprof]))

    import asyncio as _aio
    from assistant import intervals as icu_mod

    class FakeIcu:
        hrv_cutover = None
        asked = []
        async def wellness(self, *a): return rows, {}
        async def activities(self, *a): return acts, {}
        async def sport_settings(self):
            return [{"types": ["Ride"], "ftp": 273, "power_zones": [55, 78, 95, 105, 125, 150, 999]}], {}
        async def power_curves(self): return {}, {}
        async def pace_curves(self): return {}, {}
        async def events(self, oldest, newest, today):
            self.asked.append((oldest, newest))
            return cal, {}

    fake = FakeIcu()
    full = _aio.run(cc.render("coach-week", fake, today))
    check("report: six charts, then next week's workouts",
          [c.title for c in full[:6]] == cc.TITLES["coach-week"] and len(full) == 8,
          str([c.title for c in full]))
    check("events read for the week after",
          fake.asked == [(today + timedelta(days=1), today + timedelta(days=7))], str(fake.asked))

    runner = FakeIcu()
    async def run_acts_(*a): return acts + [{"type": "Run", "moving_time": 36000,
        "start_date_local": f"{today.isoformat()}T07:00:00"}], {}
    runner.activities = run_acts_
    titles = [c.title for c in _aio.run(cc.render("coach-week", runner, today))[:6]]
    check("runner's report: pace profile in fifth place", titles[4], cc.PACE_TITLE)
    async def broken(*a): raise icu_mod.IntervalsError("down")
    fake.events = broken
    full = _aio.run(cc.render("coach-week", fake, today))
    check("calendar down: the six still drawn", len(full) == 6, str(len(full)))

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
