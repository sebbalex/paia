#!/usr/bin/env python3
"""Coach metrics, Intervals.icu shaping and gate tests.

Run: .venv/bin/python tests/test_coach.py

No test framework, same reason as tests/test_policy.py. Data is synthetic:
the field names are the ones the live API returned on 2026-10-19, the values
are invented, so nothing of the owner's health data lives in the repo. The zone
shapes were confirmed on Garmin-synced rides on 2026-10-19: icu_zone_times is
a list of {"id": "Zn", "secs"} plus an "SS" entry, icu_hr_zone_times a plain
list of seconds.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import coach_metrics as m  # noqa: E402
from assistant import intervals as icu_mod  # noqa: E402
from assistant.intervals import Intervals, IntervalsError, build_tools  # noqa: E402
from assistant.policy import Policy  # noqa: E402

FAILURES: list[str] = []
TODAY = date(2026, 10, 19)


def check(name: str, got, want, reason: str = "") -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r} {reason}".rstrip())
    print(f"  {'ok  ' if got == want else 'FAIL'} {name}")


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def wellness_rows(hrv_recent: float = 60.0, n: int = 67) -> list[dict]:
    """60 baseline days alternating 55/65 ms, then 7 acute days at hrv_recent."""
    rows = []
    for i in range(n - 1, -1, -1):
        hrv = hrv_recent if i < 7 else (55.0 if i % 2 else 65.0)
        rows.append({
            "id": day(i), "hrv": hrv, "restingHR": 48, "sleepSecs": 27000,
            "ctl": 50.0, "atl": 55.0, "rampRate": 0.8,
            "ctlLoad": 60.0 if i % 2 else 0.0, "atlLoad": 60.0 if i % 2 else 0.0,
            "sportInfo": [{"type": "Ride", "eftp": 254.4}],
            "updated": f"{day(i)}T06:00:00+00:00",
        })
    return rows


def stub(offset: int) -> dict:
    return {"id": f"s{offset}", "icu_athlete_id": "x", "source": "STRAVA",
            "start_date_local": f"{day(offset)}T08:00:00",
            "_note": "STRAVA activities are not available via the API"}


def ride(offset: int, **extra) -> dict:
    base = {
        "id": f"i{offset}", "type": "Ride", "source": "GARMIN_CONNECT",
        "start_date_local": f"{day(offset)}T08:00:00", "moving_time": 6000,
        "icu_training_load": 80, "decoupling": 3.0, "icu_variability_index": 1.03,
        "icu_zone_times": [
            {"id": "Z1", "secs": 1200}, {"id": "Z2", "secs": 3600},
            {"id": "Z3", "secs": 300}, {"id": "Z4", "secs": 600},
            {"id": "Z5", "secs": 300}, {"id": "Z6", "secs": 0},
            {"id": "Z7", "secs": 0}, {"id": "SS", "secs": 700},
        ],
    }
    base.update(extra)
    return base


def test_hrv() -> None:
    print("hrv band")
    normal = {r["id"]: r["hrv"] for r in wellness_rows(60.0)}
    band = m.hrv_band(normal, TODAY)
    check("baseline established", band["baseline_status"], "established")
    check("60 baseline points", band["baseline_points"], 60)
    check("geometric middle is normal", band["status"], "normal")
    expected = round(math.log(60.0), 3)
    check("7d mean is ln(rMSSD)", band["ln_rmssd_7d"], expected)

    low = m.hrv_band({r["id"]: r["hrv"] for r in wellness_rows(45.0)}, TODAY)
    check("suppressed HRV reads below", low["status"], "below")
    high = m.hrv_band({r["id"]: r["hrv"] for r in wellness_rows(80.0)}, TODAY)
    check("raised HRV reads above", high["status"], "above")

    cut = m.hrv_band(normal, TODAY, cutover=TODAY - timedelta(days=10))
    check("cutover discards old device data", cut["baseline_points"], 10)
    check("short baseline is building", cut["baseline_status"], "building")

    sparse = {day(0): 60.0, day(1): 60.0}
    check("too few acute points gives no status",
          m.hrv_band(sparse, TODAY)["status"], None)
    check("zero or missing HRV ignored",
          m.hrv_band({day(0): 0, day(1): None}, TODAY)["acute_points"], 0)


def test_rhr_sleep() -> None:
    print("rhr and sleep")
    series = {r["id"]: r["restingHR"] for r in wellness_rows()}
    check("steady RHR no flag", m.rhr_check(series, TODAY)["flag"], False)
    series[day(0)] = 54
    r = m.rhr_check(series, TODAY)
    check("+6 bpm flags", r["flag"], True)
    check("delta computed from baseline", r["delta"], 6.0)
    del series[day(0)]
    check("falls back to latest day", m.rhr_check(series, TODAY)["latest_date"], day(1))
    check("7.5h green", m.sleep_class(27000)["class"], "green")
    check("6h amber", m.sleep_class(21600)["class"], "amber")
    check("4h red", m.sleep_class(14400)["class"], "red")
    check("no sleep is None", m.sleep_class(None)["class"], None)
    check("advice 1 is rest", m.training_advice(1), "rest")
    check("advice '4' is hit", m.training_advice("4"), "hit")
    check("unknown advice stays unknown", m.training_advice(9), None)


def test_load_metrics() -> None:
    print("load metrics")
    check("tsb", m.tsb(50.0, 55.0), -5.0)
    check("tsb missing", m.tsb(None, 55.0), None)
    flat = {day(i): 50.0 for i in range(30)}
    check("flat load ACWR is 1", m.acwr(flat, TODAY, include_today=True)["value"], 1.0)
    spike = dict(flat)
    spike[day(0)] = 400.0
    live = m.acwr(spike, TODAY, include_today=True)["value"]
    sod = m.acwr(spike, TODAY, include_today=False)["value"]
    check("today's ride moves live ACWR", live > 1.5, True)
    check("start-of-day ACWR ignores today", sod, 1.0)
    check("short history gives no ACWR",
          m.acwr({day(i): 50.0 for i in range(10)}, TODAY, include_today=True)["value"], None)
    check("monotony of identical days is None", m.monotony([50] * 7)["monotony"], None)
    mono = m.monotony([60, 0, 60, 0, 60, 0, 60])
    check("monotony value", mono["monotony"], round((240 / 7) / 29.69, 2))
    check("session RPE", m.session_rpe(6, 60), 360)
    check("session RPE without RPE", m.session_rpe(None, 60), None)


def test_zones_tid() -> None:
    print("zones and TID")
    secs = m.zone_seconds(ride(0)["icu_zone_times"])
    check("SS entry dropped, 7 zones", len(secs), 7)
    check("zone order kept", secs[:3], [1200.0, 3600.0, 300.0])
    check("plain HR list accepted", m.zone_seconds([60, 120, 30]), [60.0, 120.0, 30.0])
    check("garbage refused", m.zone_seconds([{"x": 1}]), None)
    check("seiler mapping", m.seiler(secs), (4800.0, 300.0, 900.0))
    pol = m.tid(80, 5, 15)
    check("polarized", pol["class"], "polarized")
    check("PI value", pol["pi"], round(math.log10((0.80 / 0.05) * 0.15 * 100), 2))
    check("pyramidal", m.tid(70, 20, 10)["class"], "pyramidal")
    check("threshold", m.tid(30, 50, 20)["class"], "threshold")
    check("high intensity", m.tid(30, 10, 60)["class"], "high_intensity")
    check("base", m.tid(99.5, 0.5, 0)["class"], "base")
    check("grey pct", m.tid(70, 20, 10)["grey_pct"], 20.0)
    check("drift consistent", m.tid_drift(m.tid(70, 20, 10), m.tid(70, 20, 10)), "consistent")
    check("drift acute depolarization",
          m.tid_drift({"class": "pyramidal", "pi": None}, m.tid(80, 5, 15)), "shifting")
    check("drift needs both", m.tid_drift({"class": None}, m.tid(80, 5, 15)), None)


def test_decision() -> None:
    print("baseline decision")
    est = {"baseline_status": "established", "status": "normal"}
    calm = dict(advice=None, hrv=est, rhr={"delta": 0.5},
                sleep={"class": "green"}, tsb_value=-12.0, acwr_start_of_day=1.1)
    check("all green is go", m.decision(m.signals(**calm))["recommendation"], "go")
    sig = m.signals(**{**calm, "tsb_value": -25.0})
    check("deep build TSB alone stays green", sig["tsb"], "green")
    sig = m.signals(**{**calm, "tsb_value": -35.0})
    check("TSB below -30 alone is not a skip",
          m.decision(sig)["recommendation"], "go")
    sig = m.signals(**{**calm, "tsb_value": -35.0,
                       "hrv": {"baseline_status": "established", "status": "below"}})
    check("TSB below -30 with HRV below is P1 skip",
          (m.decision(sig)["recommendation"], m.decision(sig)["priority"]), ("skip", "P1"))
    sig = m.signals(**{**calm, "acwr_start_of_day": 1.6})
    check("ACWR alone never decides", m.decision(sig)["recommendation"], "go")
    sig = m.signals(**{**calm, "acwr_start_of_day": 1.6, "sleep": {"class": "amber"}})
    check("ACWR with a tier-1 amber is P1 skip", m.decision(sig)["priority"], "P1")
    sig = m.signals(**{**calm, "rhr": {"delta": 6}})
    check("one red is modify", m.decision(sig)["recommendation"], "modify")
    sig = m.signals(**{**calm, "rhr": {"delta": 6}, "sleep": {"class": "red"}})
    check("two reds is skip", m.decision(sig)["recommendation"], "skip")
    sig = m.signals(**{**calm, "advice": "rest"})
    check("ImReady4 rest is P1 skip", m.decision(sig)["priority"], "P1")
    sig = m.signals(**{**calm, "advice": "lit", "sleep": {"class": "amber"}})
    check("two ambers is modify", m.decision(sig)["recommendation"], "modify")
    sig = m.signals(**{**calm, "hrv": {"baseline_status": "building", "status": None},
                       "rhr": {"delta": None}, "sleep": {"class": None}})
    check("go on no evidence says so",
          m.decision(sig)["tier1_signals_available"], 0)


def test_durability() -> None:
    print("durability")
    acts = [ride(i, decoupling=2.0) for i in (1, 3, 5)] + [
        ride(i, decoupling=2.0) for i in (10, 15, 20)
    ]
    d = m.durability(acts, TODAY)
    check("qualifying 7d", d["qualifying_7d"], 3)
    check("qualifying 28d", d["qualifying_28d"], 6)
    check("stable trend", d["trend"], "stable")
    check("reliable", d["reliability_limited"], False)
    worse = [ride(i, decoupling=7.0) for i in (1, 3, 5)] + [
        ride(i, decoupling=1.0) for i in (10, 15, 20)
    ]
    d = m.durability(worse, TODAY)
    check("declining", d["trend"], "declining")
    check("three warnings fire", len(d["flags"]), 2)
    check("short ride excluded", m.durability_qualifies(ride(0, moving_time=3600)), False)
    check("intervals excluded", m.durability_qualifies(ride(0, icu_variability_index=1.2)), False)


def test_shaping() -> None:
    print("shaping")
    rows = wellness_rows()
    r = icu_mod.readiness(rows, TODAY, None)
    check("hrv_swc source when baseline established", r["readiness_source"], "hrv_swc")
    check("cutover warning", any("HRV_CUTOVER_DATE" in x for x in r["missing_inputs"]), True)
    check("feel always asked for", any("Feel" in x for x in r["missing_inputs"]), True)
    rows[-1]["TrainingAdvice"] = 2
    r = icu_mod.readiness(rows, TODAY, TODAY - timedelta(days=90))
    check("imready4 wins when present", r["readiness_source"], "imready4")
    check("advice decoded", r["imready4_advice"], "lit")
    r = icu_mod.readiness(rows, TODAY, TODAY - timedelta(days=5))
    check("advice still wins over a building baseline", r["readiness_source"], "imready4")
    del rows[-1]["TrainingAdvice"]
    r = icu_mod.readiness(rows, TODAY, TODAY - timedelta(days=5))
    check("feel_only while baseline builds", r["readiness_source"], "feel_only")

    acts = [stub(1), stub(2), ride(3), ride(4, type="WeightTraining", icu_zone_times=None)]
    load = icu_mod.load(wellness_rows(), acts, TODAY)
    check("hidden counted", load["activities_28d"], {"visible": 2, "hidden_strava": 2})
    check("cycling load withheld when rides are hidden", load["load_7d_cycling"], None)
    check("hidden named in missing", "Strava" in load["missing_inputs"][0], True)
    check("eftp read from sportInfo", load["eftp_ride"], 254.0)
    clean = icu_mod.load(wellness_rows(), [ride(3)], TODAY)
    check("cycling load when nothing hidden", clean["load_7d_cycling"], 80)

    check("stub activity marked hidden", icu_mod.activity(stub(1))["hidden"], True)
    gym = icu_mod.activity({"id": "g1", "type": "WeightTraining", "moving_time": 3600,
                            "start_date_local": f"{day(0)}T18:00:00"})
    check("gym without RPE asks for it", len(gym["missing_inputs"]), 1)
    gym = icu_mod.activity({"id": "g1", "type": "WeightTraining", "moving_time": 3600,
                            "icu_rpe": 7, "start_date_local": f"{day(0)}T18:00:00"})
    check("gym session RPE computed", gym["load"]["session_rpe"], 420)
    check("ride zones carried", icu_mod.activity(ride(0))["zones"]["basis"], "power")

    monday, sunday = icu_mod.iso_week_bounds("2026-W43")
    check("ISO week bounds", (monday.isoformat(), sunday.isoformat()), ("2026-10-19", "2026-10-25"))
    events = [
        {"id": 1, "category": "WORKOUT", "start_date_local": "2026-10-19T00:00:00",
         "name": "Z2 90'", "icu_training_load": 70},
        {"id": 2, "category": "WORKOUT", "start_date_local": "2026-10-21T00:00:00",
         "name": "4x8 SS", "icu_training_load": 90},
        {"id": 3, "category": "NOTE", "start_date_local": "2026-10-20T00:00:00", "name": "x"},
    ]
    wk = icu_mod.week("2026-W43", events, [ride(0, paired_event_id=1)], wellness_rows(), TODAY)
    check("future workout not counted against adherence", wk["adherence_pct"], 100)
    check("planned load summed", wk["planned_load"], 160)
    check("note kept apart", len(wk["other_events"]), 1)
    try:
        icu_mod.iso_week_bounds("2026-43")
        check("bad week refused", False, True)
    except ValueError:
        check("bad week refused", True, True)


class FakeIntervals(Intervals):
    """Counts requests and answers from a script; exercises the real cache."""

    def __init__(self, cache_dir: Path, answers: list) -> None:
        super().__init__("key", "i0", cache_dir)
        self.answers = answers
        self.requests = 0

    async def _request(self, path, params):
        self.requests += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_session_analysis() -> None:
    print("session analysis")
    check("decoupling absent", m.decoupling_read(None, 1.0, 7200)["readable"], "absent")
    check("decoupling short ride", m.decoupling_read(3.0, 1.02, 1800)["readable"], "no")
    steady = m.decoupling_read(3.0, 1.03, 7200)
    check("decoupling steady readable", (steady["readable"], steady["verdict"]), ("yes", "held"))
    check("decoupling steady drift", m.decoupling_read(7.5, 1.04, 7200)["verdict"], "drifted")
    check("decoupling rough", m.decoupling_read(6.0, 1.10, 7200)["readable"], "rough")
    check("decoupling interval ride unreadable", m.decoupling_read(16.8, 1.38, 4500)["readable"], "no")

    check("fuel short ride band", m.fuel(3600, 100, None)["guideline_g_per_h"], [0, 30])
    check("fuel medium band", m.fuel(2 * 3600, 200, None)["guideline_g_per_h"], [30, 60])
    long_ride = m.fuel(5 * 3600, 600, 300)
    check("fuel long band", long_ride["guideline_g_per_h"], [60, 90])
    check("fuel ingested per hour", long_ride["ingested_g_per_h"], 60)
    check("fuel burned per hour", long_ride["burned_est_g_per_h"], 120)
    check("fuel without ingest logged", m.fuel(3600, 100, None)["ingested_logged"], False)

    def rep(start, w, hr, group="120s@260w", kind="WORK", secs=120):
        return {"type": kind, "group_id": group, "start_time": start, "moving_time": secs,
                "average_watts": w, "average_heartrate": hr, "max_heartrate": hr + 5,
                "average_cadence": 80}
    held = [rep(i * 240, 260, 150 + 2 * i) for i in range(6)]
    s = m.interval_set(held + [rep(0, 150, 120, kind="RECOVERY", group=None)], 260)
    check("set reps counted", s["reps"], 6)
    check("set at 100% FTP", s["mean_pct_ftp"], 100)
    check("held set no fade", s["power_fade_pct"], 0.0)
    check("hr rise first to last third", s["hr_rise_bpm"], 8.0)
    check("hr creep at held power flagged", any("HR climbed" in f for f in s["flags"]), True)
    faded = [rep(i * 240, w, 160) for i, w in enumerate((280, 280, 270, 260, 250, 250))]
    s = m.interval_set(faded, 260)
    check("fade measured", s["power_fade_pct"], -10.7)
    check("fade flagged", any("faded" in f for f in s["flags"]), True)
    main = m.interval_set(held + [rep(5000 + i * 60, 400, 170, group="30s@400w", secs=30) for i in range(3)], 260)
    check("main set is the most work time", main["rep_secs_median"], 120)
    check("one-off surges are no set",
          m.interval_set([rep(0, 461, 150, group="22s@461w", secs=22)], 260), None)

    curves = {"42d": {5: 846, 60: 388, 300: 283, 1200: 265, 3600: 197},
              "1y": {5: 1107, 60: 531, 300: 320, 1200: 278, 3600: 231}}
    prof = {p["duration"]: p for p in m.power_profile(curves, 77.2, "42d", "1y")}
    check("profile pct of season", prof["vo2_5m"]["42d_pct_of_1y"], 88)
    check("profile wkg", prof["threshold_20m"]["42d_wkg"], 3.43)
    check("pick curve by secs", m.pick_curve([5, 60, 300], [900, 400, 300])[60], 400)
    check("pick curve missing secs", m.pick_curve([5], [900])[3600], None)

    zw = m.zone_watts(273, [55, 75, 90, 105, 120, 150, 999])
    check("zone watts Z2", (zw[1]["low_w"], zw[1]["high_w"]), (151, 205))
    check("zone watts top open", zw[-1]["high_w"], None)
    check("zone watts without ftp", m.zone_watts(None, [55]), None)

    acts = [stub(1), ride(2), ride(5, type="MountainBikeRide")]
    out = icu_mod.sessions(acts)
    check("sessions newest first", [r["date"] for r in out["sessions"]], [day(2), day(5)])
    check("sessions count hidden", out["hidden_strava"], 1)
    a = icu_mod.activity(ride(0, icu_intervals=held, moving_time=7200, icu_ftp=260))
    check("activity carries main set", a["main_set"]["reps"], 6)
    check("activity asks for rpe", any("RPE" in x for x in a["missing_inputs"]), True)
    check("activity flags unlogged carbs", any("carbs" in x for x in a["missing_inputs"]), True)
    pc = icu_mod.power_curve({"list": [
        {"id": "42d", "secs": [5, 60, 300, 1200, 3600], "values": [846, 388, 283, 265, 197],
         "weight": 77.2, "start_date_local": "2026-09-08T00:00:00", "end_date_local": "2026-10-20"},
    ]})
    check("power curve names missing windows", len(pc["missing_inputs"]), 2)
    th = icu_mod.thresholds([{"types": ["Ride"], "ftp": 273,
                              "power_zones": [55, 75, 90, 105, 120, 150, 999],
                              "sweet_spot_min": 84, "sweet_spot_max": 97}])
    check("thresholds sweet spot watts", th["sports"][0]["sweet_spot_watts"], [229, 265])
    th = icu_mod.thresholds([{"types": ["Ride"], "ftp": 273, "power_zones": [55, 75, 999]},
                             {"types": ["Run"], "lthr": 170}], 254)
    check("eftp gap", th["sports"][0]["eftp_vs_ftp_pct"], -7.0)
    check("zones on eftp", th["sports"][0]["power_zones_watts_eftp"][0]["high_w"], 140)
    check("no eftp on non-ride sport", "eftp" in th["sports"][1], False)


def test_client() -> None:
    print("client cache and backoff")
    cache = Path(tempfile.mkdtemp(prefix="icu-"))
    fake = FakeIntervals(cache, [[{"id": "a"}], [{"id": "b"}]])
    p1, meta1 = asyncio.run(fake.get("/x", {"a": 1}, ttl=600))
    p2, meta2 = asyncio.run(fake.get("/x", {"a": 1}, ttl=600))
    check("second call served from cache", fake.requests, 1)
    check("cache meta", (meta1["source"], meta2["source"]), ("intervals_api", "cache"))
    check("cached payload identical", p2, p1)

    limited = FakeIntervals(cache, [icu_mod._RateLimited(time.time() + 900)])
    payload, meta = asyncio.run(limited.get("/x", {"a": 1}, ttl=0))
    check("429 falls back to stale cache", (payload, meta["stale"]), ([{"id": "a"}], True))
    check("rate limit reported", "rate_limited_until" in meta, True)

    limited._set_backoff("60")
    until = limited._backoff_until()
    check("backoff never under 15 minutes", until - time.time() > 14 * 60, True)
    payload, meta = asyncio.run(limited.get("/x", {"a": 1}, ttl=0))
    check("no request during backoff", limited.requests, 1)
    try:
        asyncio.run(limited.get("/never-cached", {}, ttl=0))
        check("backoff without cache fails", False, True)
    except IntervalsError as exc:
        check("backoff without cache fails", "rate-limited" in str(exc), True)

    # A rejected write says why: Intervals names the field in a 4xx body.
    import httpx
    real = httpx.AsyncClient
    answers = {"POST": (422, '{"error":  "Invalid\n workout step"}'), "GET": (500, "trace")}
    def mock(**kw):
        return real(transport=httpx.MockTransport(
            lambda req: httpx.Response(answers[req.method][0], text=answers[req.method][1])), **kw)
    httpx.AsyncClient = mock
    try:
        client = Intervals("key", "i0", Path(tempfile.mkdtemp(prefix="icu-")))
        try:
            asyncio.run(client.save_workout({"name": "x"}))
            check("422 raises", False, True)
        except IntervalsError as exc:
            check("422 carries Intervals' reason", str(exc),
                  'Intervals.icu answered HTTP 422: {"error": "Invalid workout step"}')
        try:
            asyncio.run(client._request("/y", {}))
            check("500 raises", False, True)
        except IntervalsError as exc:
            check("5xx body not repeated", str(exc), "Intervals.icu answered HTTP 500")
    finally:
        httpx.AsyncClient = real


def test_tools() -> None:
    print("tools")
    cache = Path(tempfile.mkdtemp(prefix="icu-"))
    fake = FakeIntervals(cache, [wellness_rows()])
    tools = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}
    check("eight reads and one write", sorted(tools), sorted([
        "coach_readiness", "coach_load", "coach_activity", "coach_sessions",
        "coach_week", "coach_history", "coach_power_curve", "coach_thresholds",
        "coach_plan_workout",
    ]))
    out = asyncio.run(tools["coach_readiness"].handler({}))
    body = json.loads(out["content"][0]["text"])
    check("readiness carries data age", "data_age_hours" in body, True)
    check("readiness carries source", body["source"], "intervals_api")
    out = asyncio.run(tools["coach_readiness"].handler({"date": "2027-01-01"}))
    check("future date refused", out.get("is_error"), True)
    out = asyncio.run(tools["coach_activity"].handler({"id": "../athlete/x"}))
    check("path-ish activity id refused", out.get("is_error"), True)
    check("refused before any request", fake.requests, 1)
    out = asyncio.run(tools["coach_week"].handler({"week": "2027-W40"}))
    check("far-future week refused", out.get("is_error"), True)

    # Next week: only its calendar exists. Intervals refuses newest < oldest.
    class Strict(FakeIntervals):
        async def _request(self, path, params):
            if params.get("newest", "9") < params.get("oldest", "0"):
                raise IntervalsError("Intervals.icu answered HTTP 422")
            return await super()._request(path, params)
    nxt = (TODAY + timedelta(days=7)).isocalendar()
    strict = Strict(Path(tempfile.mkdtemp(prefix="icu-")),
                    [[{"id": 1, "category": "WORKOUT", "name": "VO2",
                       "start_date_local": f"{TODAY + timedelta(days=7)}T00:00:00"}]])
    week_tool = {t.name: t for t in build_tools(strict, clock=lambda: TODAY)}["coach_week"]
    out = asyncio.run(week_tool.handler({"week": f"{nxt[0]}-W{nxt[1]:02d}"}))
    check("next week readable", out.get("is_error"), None)
    check("next week asks for its calendar only", strict.requests, 1)
    check("key never in tool output", "key" in json.dumps(body).replace("_key", ""), False)


class WritingIntervals(FakeIntervals):
    """Reads from a script, writes recorded and echoed back as Intervals does."""

    def __init__(self, cache_dir: Path, answers: list, saved: dict | None = None) -> None:
        super().__init__(cache_dir, answers)
        self.writes: list[tuple[str, str, dict]] = []
        self.saved = saved

    async def _write(self, method, path, body):
        self.writes.append((method, path, body))
        if self.saved is not None:
            return {**body, **self.saved}
        return {**body, "id": 77, "moving_time": 3600, "icu_training_load": 70,
                "workout_doc": {"steps": [{}, {}, {}], "duration": 3600}}


VO2 = "Riscaldamento\n- 15m 55%\n\nMain set 2x\n- 30s 330-350w\n- 15s 50%\n\n- 10m 50%"


def test_plan_workout() -> None:
    print("plan workout")
    cache = Path(tempfile.mkdtemp(prefix="icu-"))
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    args = {"date": tomorrow, "name": "VO2 30/15", "description": VO2}

    fake = WritingIntervals(cache, [[], []])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler(args))
    body = json.loads(out["content"][0]["text"])
    check("preview writes nothing", (body["written"], fake.writes), (False, []))
    check("preview shows the event", body["would_write"]["category"], "WORKOUT")
    check("preview starts at local midnight", body["would_write"]["start_date_local"],
          f"{tomorrow}T00:00:00")
    out = asyncio.run(plan.handler({**args, "confirm": "false"}))
    check("string false is not a yes", fake.writes, [])

    fake = WritingIntervals(cache, [[]])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "confirm": True}))
    body = json.loads(out["content"][0]["text"])
    check("confirm writes once", len(fake.writes), 1)
    method, path, sent = fake.writes[0]
    check("a create is a POST to events", (method, path), ("POST", "/athlete/i0/events"))
    check("tagged as the assistant's", sent["external_id"].startswith("paia-"), True)
    check("only named fields sent", sorted(sent), sorted(
        ["category", "start_date_local", "type", "name", "description", "external_id"]))
    check("parsed into steps", (body["structured"], body["steps"], body["planned_min"]),
          (True, 3, 60))

    fake = WritingIntervals(cache, [[]], saved={"id": 78, "workout_doc": {}})
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    body = json.loads(asyncio.run(plan.handler({**args, "confirm": True}))["content"][0]["text"])
    check("unparsed text is reported", body["structured"], False)

    for bad, why in (
        ({**args, "date": (TODAY - timedelta(days=1)).isoformat()}, "past date"),
        ({**args, "date": (TODAY + timedelta(days=40)).isoformat()}, "too far ahead"),
        ({**args, "type": "Swim"}, "unplannable type"),
        ({**args, "name": ""}, "empty name"),
        ({**args, "description": "ride hard"}, "no step lines"),
        ({**args, "event_id": "../x"}, "path-ish event id"),
    ):
        fake = WritingIntervals(cache, [])
        plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
        out = asyncio.run(plan.handler({**bad, "confirm": True}))
        check(f"{why} refused before any request",
              (out.get("is_error"), fake.requests, fake.writes), (True, 0, []))

    same = [{"id": 5, "category": "WORKOUT", "name": "VO2 30/15",
             "start_date_local": f"{tomorrow}T00:00:00"}]
    fake = WritingIntervals(cache, [same])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "confirm": True}))
    check("same name same day refused", (out.get("is_error"), fake.writes), (True, []))

    theirs = {"id": 9, "category": "WORKOUT", "name": "The owner's own",
              "start_date_local": f"{tomorrow}T00:00:00"}
    fake = WritingIntervals(cache, [theirs])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "event_id": "9", "confirm": True}))
    check("the owner's own workout not rewritten", (out.get("is_error"), fake.writes), (True, []))

    ours = {**theirs, "name": "VO2 30/15", "external_id": "paia-abc"}
    fake = WritingIntervals(cache, [ours, [], [ours]])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "event_id": "9", "confirm": True}))
    check("own workout rewritten with PUT", [(w[0], w[1]) for w in fake.writes],
          [("PUT", "/athlete/i0/events/9")])
    check("rewrite keeps its tag", fake.writes[0][2]["external_id"], "paia-abc")

    ridden = {**ours, "start_date_local": f"{TODAY.isoformat()}T00:00:00"}
    fake = WritingIntervals(cache, [ridden, [{"id": "i1", "paired_event_id": 9}]])
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "date": TODAY.isoformat(), "event_id": "9",
                                    "confirm": True}))
    check("ridden workout stays", (out.get("is_error"), fake.writes), (True, []))

    cache = Path(tempfile.mkdtemp(prefix="icu-"))
    fake = WritingIntervals(cache, [[{"id": 1}], []])
    asyncio.run(fake.events(TODAY, TODAY, TODAY))
    asyncio.run(fake.save_workout({"name": "x"}))
    asyncio.run(fake.events(TODAY, TODAY, TODAY))
    check("a write drops the cached calendar", fake.requests, 2)

    fake = WritingIntervals(cache, [])
    fake._set_backoff("60")
    plan = {t.name: t for t in build_tools(fake, clock=lambda: TODAY)}["coach_plan_workout"]
    out = asyncio.run(plan.handler({**args, "confirm": True}))
    check("backoff: nothing read or written", (out.get("is_error"), fake.writes), (True, []))


def test_gate() -> None:
    print("policy gate")
    root = Path(tempfile.mkdtemp(prefix="coach-")).resolve()
    (root / "state").mkdir()
    policy = Policy(root, frozenset())
    s11 = root / ".claude/skills/coach/references/section-11"
    s11.mkdir(parents=True)
    (s11 / "SECTION_11.md").write_text("# Section 11\n")
    (root / "memory/wiki").mkdir(parents=True)
    policy.begin_turn()
    check("coach tool refused before Section 11",
          policy.check("mcp__coach__coach_load", {}).allow, False)
    policy.check("Read", {"file_path": str(root / "memory/wiki/training.md")})
    check("other read does not open the gate",
          policy.check("mcp__coach__coach_load", {}).allow, False)
    policy.check("Read", {"file_path": str(s11 / "../../../../../.env")})
    check("traversal out of section-11 does not open it",
          policy.check("mcp__coach__coach_load", {}).allow, False)
    policy.check("Read", {"file_path": ".claude/skills/coach/references/section-11/SECTION_11.md",
                          "offset": 1, "limit": 40})
    for name in ("coach_readiness", "coach_load", "coach_activity", "coach_sessions",
                 "coach_week", "coach_history", "coach_power_curve", "coach_thresholds"):
        check(f"{name} allowed", policy.check(f"mcp__coach__{name}", {}).allow, True)
    policy.begin_turn()
    check("next turn closes the gate again",
          policy.check("mcp__coach__coach_load", {}).allow, False)
    policy.check("Read", {"file_path": str(s11 / "SECTION_11.md")})
    check("unpinned coach tool denied",
          policy.check("mcp__coach__push_workout", {}).allow, False)

    plan = "mcp__coach__coach_plan_workout"
    args = {"date": "2026-10-21", "name": "VO2 30/15", "description": VO2}
    check("confirm without preview refused",
          policy.check(plan, {**args, "confirm": True}).allow, False)
    check("preview allowed", policy.check(plan, args).allow, True)
    check("confirm in the preview's own turn refused",
          policy.check(plan, {**args, "confirm": True}).allow, False)
    policy.begin_turn()
    check("confirm of a different workout refused before Section 11",
          policy.check(plan, {**args, "name": "VO2 40/20", "confirm": True}).allow, False)
    check("preview still needs Section 11",
          policy.check(plan, {**args, "name": "VO2 40/20"}).allow, False)
    check("reads still need Section 11",
          policy.check("mcp__coach__coach_week", {}).allow, False)
    check("confirm of an earlier preview skips the Section 11 read",
          policy.check(plan, {**args, "confirm": True}).allow, True)
    policy.check("Read", {"file_path": str(s11 / "SECTION_11.md")})
    policy.check(plan, args)
    policy.begin_turn()
    policy.check("Read", {"file_path": str(s11 / "SECTION_11.md")})
    check("confirm of a different workout refused",
          policy.check(plan, {**args, "name": "VO2 40/20", "confirm": True}).allow, False)
    check("confirm in a later turn allowed",
          policy.check(plan, {**args, "name": " VO2  30/15 ", "confirm": True}).allow, True)
    check("one preview, one write",
          policy.check(plan, {**args, "confirm": True}).allow, False)
    policy.check(plan, args)
    policy.begin_turn(routine=True)
    policy.check("Read", {"file_path": str(s11 / "SECTION_11.md")})
    check("routine cannot preview", policy.check(plan, args).allow, False)
    check("routine cannot confirm",
          policy.check(plan, {**args, "confirm": True}).allow, False)
    check("routine still reads", policy.check("mcp__coach__coach_week", {}).allow, True)
    check("routine files the weekly report",
          policy.check("mcp__coach__coach_report", {}).allow, True)
    policy.begin_turn()
    policy.check("Read", {"file_path": str(s11 / "SECTION_11.md")})
    check("chat cannot file the weekly report",
          policy.check("mcp__coach__coach_report", {}).allow, False)
    check("trailing newline denied",
          policy.check("mcp__coach__coach_load\n", {}).allow, False)
    check("intervals cache unreadable",
          policy.check("Read", {"file_path": str(root / "state/intervals/x.json")}).allow, False)


def run(offset: int, **extra) -> dict:
    base = {
        "id": f"r{offset}", "type": "Run", "source": "GARMIN_CONNECT",
        "start_date_local": f"{day(offset)}T07:00:00", "moving_time": 4200,
        "distance": 12000, "icu_training_load": 70, "average_speed": 2.857,
        "gap": 3.0, "threshold_pace": 4.0, "use_gap_zone_times": True,
        "decoupling": 4.0, "average_heartrate": 148,
        "gap_zone_times": [600, 3000, 300, 200, 100, 0, 0],
        "pace_zone_times": [900, 2700, 300, 200, 100, 0, 0],
    }
    base.update(extra)
    return base


def test_running() -> None:
    print("running")
    check("pace string", m.pace_str(4.0), "4:10/km")
    check("pace of nothing", m.pace_str(None), None)
    zones = m.zone_paces(4.0, [77.5, 87.7, 999])
    check("pace zone 1 open slow edge", zones[0], {"zone": "Z1", "slowest": None, "fastest": "5:23/km"})
    check("pace zone top open fast edge", zones[2]["fastest"], None)

    a = icu_mod.activity(run(0))
    check("run sport family", a["sport"], "running")
    check("run gap pace", a["pace"]["gap"], "5:33/km")
    check("run gap as % of threshold", a["pace"]["pct_threshold"], 75)
    check("run zones on gap", a["zones"]["basis"], "gap")
    check("run zones on pace without gap setting",
          icu_mod.activity(run(0, use_gap_zone_times=False))["zones"]["basis"], "pace")
    check("run decoupling rough without power", a["hr"]["decoupling_read"]["readable"], "rough")
    check("run without RPE asks", any("RPE" in x for x in a["missing_inputs"]), True)
    nothr = icu_mod.activity(run(0, threshold_pace=None))
    check("run without threshold pace named",
          any("threshold pace" in x for x in nothr["missing_inputs"]), True)
    check("ride has no pace block", icu_mod.activity(ride(0))["pace"], None)

    reps = [{"type": "WORK", "group_id": "300s", "moving_time": 300, "start_time": i * 600,
             "gap": v, "average_heartrate": 165 + i} for i, v in enumerate([4.2, 4.2, 4.1, 3.9, 3.9, 3.9])]
    a = icu_mod.activity(run(0, icu_intervals=reps))
    check("run main set on pace", a["main_set"]["pace_basis"], "gap")
    check("run main set fade", a["main_set"]["pace_fade_pct"], -7.1)
    check("run fade flagged", any("pace faded" in f for f in a["main_set"]["flags"]), True)

    load = icu_mod.load(wellness_rows(), [ride(1), run(2)], TODAY)
    check("running load split", (load["load_7d_cycling"], load["load_7d_running"]), (80, 70))
    check("running monotony family", "running" in load["monotony_by_sport_7d"], True)
    check("run row carries gap", icu_mod.sessions([run(1)])["sessions"][0]["pace_gap"], "5:33/km")
    h = icu_mod.history([], [run(1), run(2)], 273)
    check("history with no wellness rows", h["weeks"], [])

    th = icu_mod.thresholds([
        {"types": ["Ride"], "ftp": 273, "hr_zones": [140, 155, 196]},
        {"types": ["Run", "TrailRun"], "lthr": 172, "hr_zones": [140, 155, 196],
         "threshold_pace": 4.0, "pace_zones": [77.5, 87.7, 999]},
        {"types": ["Hike"], "hr_zones": [120, 140, 190]},
    ])
    run_row = th["sports"][1]
    check("threshold pace per km", run_row["threshold_pace_per_km"], "4:10/km")
    check("pace zones per km", run_row["pace_zones_per_km"][1]["fastest"], "4:45/km")
    check("copied ride HR zones flagged", run_row.get("hr_zones_copied_from_ride"), True)
    check("own HR zones not flagged", "hr_zones_copied_from_ride" in th["sports"][2], False)
    th = icu_mod.thresholds([{"types": ["Run"], "lthr": 172}])
    check("run without threshold pace named", "threshold pace" in th["sports"][0]["missing"], True)

    body = icu_mod.workout_body({"date": TODAY.isoformat(), "type": "Run", "name": "Soglia",
                                 "description": "- 15m Z2 Pace\n- 20m 95-100% Pace"}, TODAY)
    check("run is a plan type", body["type"], "Run")


def main() -> int:
    for test in (test_hrv, test_rhr_sleep, test_load_metrics, test_decision, test_zones_tid,
                 test_durability, test_shaping, test_session_analysis, test_running, test_client, test_tools, test_plan_workout, test_gate):
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
