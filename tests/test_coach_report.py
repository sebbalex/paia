#!/usr/bin/env python3
"""Weekly report tests. Run: .venv/bin/python tests/test_coach_report.py

A filed report and synthetic Intervals.icu payloads in; the laid-out email,
its text twin and the tool's refusals out. No network.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import coach_report as cr  # noqa: E402
from assistant.channels import Delivery, SmtpConfig  # noqa: E402
from assistant.intervals import build_tools  # noqa: E402

FAILURES: list[str] = []
PNG = b"\x89PNG\r\n\x1a\n"
TODAY = date(2026, 10, 25)  # a Sunday: the week coming is 26 Oct - 1 Nov


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def refused(args: dict, needle: str) -> bool:
    try:
        cr.validate(args, TODAY)
    except ValueError as exc:
        return needle in str(exc)
    return False


ARGS = {
    "headline": "Settimana piena e assorbita",
    "summary": "Tre uscite su tre e la palestra. VO2 sui target.",
    "highlights": ["VO2 30/15: 16 ripetute a 338 W medi"],
    "watch": ["HRV in calo da giovedì"],
    "goal": "Prep, blocco 1 settimana 3 di 4.",
    "goal_date": "2027-06-11",
    "next_week": [
        {"date": "2026-10-30", "title": "Soglia 3x10", "minutes": 70, "key": True,
         "purpose": "3x10' a 255-265 W", "workout": "- 15m 55%\n\nMain set 3x\n- 10m 95%\n- 5m 55%\n"},
        {"date": "2026-10-26", "title": "Gambe", "minutes": 60, "purpose": "Squat 4x6"},
        {"date": "2026-10-27", "title": "Z2 facile", "minutes": 75, "workout": "- 75m 65%"},
        {"date": "2026-10-28", "title": "VO2-2 30/15", "minutes": 36, "key": True},
    ],
    "question": "RPE dell'ultima ripetuta?",
}


class FakeIcu:
    hrv_cutover = date(2026, 6, 1)

    def __init__(self) -> None:
        self.rows = [
            {"id": (TODAY - timedelta(days=i)).isoformat(), "ctl": 50 - i * 0.05,
             "atl": 55 - i * 0.04, "atlLoad": 40 + i % 30, "hrv": 50 + i % 9,
             "restingHR": 46, "sportInfo": [{"type": "Ride", "eftp": 262}]}
            for i in range(180)
        ]
        self.acts = [
            {"id": f"i{i}", "start_date_local": f"{TODAY - timedelta(days=i)}T10:00:00",
             "type": "Ride", "icu_training_load": 70, "moving_time": 5400,
             "icu_zone_times": [{"id": f"Z{z}", "secs": s}
                                for z, s in zip(range(1, 8), [1800, 2400, 600, 300, 240, 0, 0])]}
            for i in range(0, 180, 2)
        ] + [{"source": "STRAVA", "start_date_local": f"{TODAY}T08:00:00"}]

    async def wellness(self, *a): return self.rows, {}
    async def activities(self, *a): return self.acts, {}
    async def sport_settings(self):
        return [{"types": ["Ride"], "ftp": 273, "power_zones": [55, 78, 95, 105, 125, 150, 999]}], {}
    async def events(self, oldest, newest, today):
        if oldest <= today:
            return [], {}
        return [{"category": "WORKOUT", "name": "VO2-2 30/15", "icu_training_load": 40,
                 "start_date_local": "2026-10-28T00:00:00",
                 "workout_doc": {"steps": [{"reps": 8, "steps": [
                     {"duration": 30, "power": {"units": "w", "start": 330, "end": 350}},
                     {"duration": 15, "power": {"units": "%ftp", "value": 50}}]}]}}], {}


def test_validate() -> None:
    print("validate")
    rep = cr.validate(ARGS, TODAY)
    check("days sorted by date", [d["date"].day for d in rep["next_week"]] == [26, 27, 28, 30],
          str(rep["next_week"]))
    check("goal date parsed", rep["goal_date"] == date(2027, 6, 11), str(rep["goal_date"]))
    check("headline over the limit refused",
          refused({**ARGS, "headline": "x" * 91}, "limit is 90"))
    check("five highlights refused", refused({**ARGS, "highlights": ["a"] * 5}, "at most 4"))
    check("day outside the week coming refused",
          refused({**ARGS, "next_week": [{"date": "2026-10-25", "title": "x"}]}, "outside"))
    check("empty next week refused", refused({**ARGS, "next_week": []}, "next_week is empty"))
    check("bad goal date refused", refused({**ARGS, "goal_date": "giugno"}, "goal_date"))
    check("whitespace folded", cr.validate({**ARGS, "summary": "a\n\n  b"}, TODAY)["summary"] == "a b")


def test_layout() -> None:
    print("\nlayout")
    rep = cr.validate(ARGS, TODAY)
    f = asyncio.run(cr.facts(FakeIcu(), TODAY, rep))
    check("week and span", f.week == "2026-W43" and f.span == "lun 19 ott – dom 25 ott",
          f"{f.week} {f.span}")
    check("six tiles", [t.label for t in f.tiles][:2] == ["Carico", "Ore in bici"]
          and len(f.tiles) == 6, str(f.tiles))
    tiles = {t.label: t for t in f.tiles}
    check("tone and unit kept apart", tiles["HRV 7 g"].unit == "ms"
          and tiles["Forma TSB"].unit == "" and tiles["eFTP"].unit == "W", str(f.tiles))
    check("weeks to goal computed, not written", f.weeks_to_goal == 32, str(f.weeks_to_goal))
    check("two trend charts", len(f.charts) == 2 and all(c.png.startswith(PNG) for c in f.charts),
          str([c.title for c in f.charts]))
    # Index 2 is Z2 facile (not key, not in the calendar): no chart. Index 2 in
    # the sorted list: 26 Gambe, 27 Z2, 28 VO2 (calendar), 30 Soglia (text).
    check("profiles for the key sessions only",
          sorted(f.workouts) == [2, 3], str(sorted(f.workouts)))
    check("calendar profile carries its load", "VO2-2" in f.workouts[2].title, f.workouts[2].title)
    check("strava-hidden noted in Italian", any("Strava" in m for m in f.missing), str(f.missing))

    body, images = cr.render_html(rep, f)
    check("every image referenced", all(f"cid:{cid}" in body for cid, _ in images), "")
    check("four images: two trends, two profiles", len(images) == 4, str(len(images)))
    check("text escaped", "<script" not in cr.render_html(
        {**rep, "headline": "<script>x</script>"}, f)[0], "")
    check("headline in the header", rep["headline"] in body, "")
    text = cr.render_text(rep, f)
    check("text twin has next week", "ven 30 ott: Soglia 3x10 (70′) ★" in text, text)

    msg = cr.build_email(rep, f, None, "me@x")
    check("subject names week and verdict",
          msg["Subject"] == "Settimana W43 · Settimana piena e assorbita", msg["Subject"])
    check("plain and html parts", msg.get_body(("plain",)) is not None
          and msg.get_body(("html",)) is not None, "")
    cids = [p["Content-ID"] for p in msg.walk() if p.get_content_type() == "image/png"]
    check("images inline by content id", len(cids) == 4, str(cids))


def test_tool_and_delivery() -> None:
    print("\ntool and delivery")
    desk = cr.ReportDesk()
    tools = {t.name: t for t in build_tools(FakeIcu(), clock=lambda: TODAY, desk=desk)}
    check("tool present with a desk", "coach_report" in tools, str(sorted(tools)))
    check("absent without one", "coach_report" not in
          {t.name for t in build_tools(FakeIcu(), clock=lambda: TODAY)}, "")
    out = asyncio.run(tools["coach_report"].handler({**ARGS, "headline": "x" * 200}))
    check("over-long refused, nothing filed", out.get("is_error") and desk.take() is None, str(out))
    out = asyncio.run(tools["coach_report"].handler(ARGS))
    body = json.loads(out["content"][0]["text"])
    check("filed", body["filed"] and body["workouts_from_text"] == 2, str(body))
    check("desk hands it over once", desk.take() is not None and desk.take() is None, "")

    class Tg:
        def __init__(self): self.sent = []
        async def send(self, chat_id, text): self.sent.append(text)
        async def send_photos(self, chat_id, photos): self.sent.append(len(photos))

    tg = Tg()
    d = Delivery(tg, 1, SmtpConfig("", 587, "", "", "", ""), None)
    built = []
    asyncio.run(d.send_built("email", lambda s, t: built.append(1), "testo", ()))
    check("no mail configured: text on telegram, nothing built",
          tg.sent == ["testo"] and built == [], str(tg.sent))


def main() -> int:
    for test in (test_validate, test_layout, test_tool_and_delivery):
        test()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
