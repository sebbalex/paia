"""The coach's arithmetic. Pure functions, no I/O, no clock.

Everything the endurance coach reasons over is computed here and handed to the
model as finished numbers. That is the "no virtual math" rule from Section 11
(CrankAddict, v11.77): a model asked to average seven HRV values will produce a
plausible number, and plausible is not the same as right. These functions are
the one place a formula lives, and tests/test_coach.py is what holds them to
it.

The definitions follow Section 11 where it has one -- the 7-zone to Seiler
3-zone mapping, the Treff polarization index and its 5-class classifier,
aggregate durability from steady-state decoupling, grey-zone percentage, Foster
monotony -- and COACH.md's modern layer where it departs: readiness from
ln(rMSSD) against a smallest-worthwhile-change band rather than a percentage
drop, and ACWR reported as load context only, never as a verdict.

Missing stays missing. A function that lacks the data it needs returns None or
says how many points it had; it never fills the gap with a guess, because the
model downstream would repeat the guess as fact.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

# -- readiness ------------------------------------------------------------

# COACH.md 5.2: 7-day rolling mean of ln(rMSSD) against a 60-day baseline,
# "normal" being the baseline mean +/- 0.5 SD. The minimum of 14 baseline days
# is ImReady4's own floor (HRV on >= 14 of the last 30 days); below it the
# baseline is still being built and the coach decides from Feel and load.
HRV_ACUTE_DAYS = 7
HRV_BASELINE_DAYS = 60
HRV_MIN_BASELINE_POINTS = 14
HRV_MIN_ACUTE_POINTS = 3
HRV_SWC_SD = 0.5

# COACH.md 5.4: RHR >= +5 bpm over baseline flags fatigue or illness.
RHR_FLAG_BPM = 5

# Section 11, Recovery Metrics Integration: sleep duration is the readiness
# input, score and quality are context only.
SLEEP_GREEN_HOURS = 7.0
SLEEP_AMBER_HOURS = 5.0

# ImReady4 writes its advice to a select wellness field the athlete creates.
TRAINING_ADVICE = {1: "rest", 2: "lit", 3: "planned", 4: "hit"}


def _window(
    series: dict[str, float | None], end: date, days: int, *, include_end: bool
) -> list[float]:
    """Values present in the `days` days ending at `end` (inclusive or not)."""
    last = end if include_end else end - timedelta(days=1)
    first = last - timedelta(days=days - 1)
    out = []
    for key, value in series.items():
        if value is None:
            continue
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if first <= day <= last:
            out.append(float(value))
    return out


def _since(series: dict[str, float | None], cutover: date | None) -> dict[str, float | None]:
    if cutover is None:
        return series
    return {k: v for k, v in series.items() if k >= cutover.isoformat()}


def hrv_band(
    rmssd_by_day: dict[str, float | None],
    today: date,
    cutover: date | None = None,
) -> dict[str, Any]:
    """ln(rMSSD) 7-day mean against a 60-day baseline band.

    The acute window includes today; the baseline ends yesterday, so a bad
    morning cannot drag its own reference down. Values before `cutover` are
    dropped: Apple SDNN and Garmin rMSSD are different measurements and a
    baseline mixing them is a baseline of nothing (COACH.md 3.3).
    """
    series = _since(rmssd_by_day, cutover)
    logs = {k: (math.log(v) if v and v > 0 else None) for k, v in series.items()}
    acute = _window(logs, today, HRV_ACUTE_DAYS, include_end=True)
    base = _window(logs, today, HRV_BASELINE_DAYS, include_end=False)
    result: dict[str, Any] = {
        "acute_points": len(acute),
        "baseline_points": len(base),
        "baseline_status": (
            "established" if len(base) >= HRV_MIN_BASELINE_POINTS else "building"
        ),
        "ln_rmssd_7d": None,
        "band_low": None,
        "band_high": None,
        "status": None,
    }
    if len(acute) < HRV_MIN_ACUTE_POINTS or len(base) < 2:
        return result
    mean7 = statistics.fmean(acute)
    mean = statistics.fmean(base)
    sd = statistics.stdev(base)
    low, high = mean - HRV_SWC_SD * sd, mean + HRV_SWC_SD * sd
    result.update(
        ln_rmssd_7d=round(mean7, 3),
        rmssd_7d_ms=round(math.exp(mean7), 1),
        band_low=round(low, 3),
        band_high=round(high, 3),
        status="below" if mean7 < low else "above" if mean7 > high else "normal",
    )
    return result


def rhr_check(
    rhr_by_day: dict[str, float | None],
    today: date,
    cutover: date | None = None,
) -> dict[str, Any]:
    """Latest resting HR (today, else the most recent day) against a baseline.

    Baseline is the 60 days before that reading, so a high value is compared
    with the days that preceded it rather than with itself.
    """
    series = _since(rhr_by_day, cutover)
    present = sorted(
        (k, v) for k, v in series.items() if v is not None and k <= today.isoformat()
    )
    result: dict[str, Any] = {
        "latest": None,
        "latest_date": None,
        "baseline": None,
        "baseline_points": 0,
        "delta": None,
        "flag": False,
    }
    if not present:
        return result
    day_key, latest = present[-1]
    base = _window(series, date.fromisoformat(day_key), HRV_BASELINE_DAYS, include_end=False)
    result.update(latest=latest, latest_date=day_key, baseline_points=len(base))
    if len(base) >= HRV_MIN_BASELINE_POINTS:
        mean = statistics.fmean(base)
        delta = latest - mean
        result.update(
            baseline=round(mean, 1),
            delta=round(delta, 1),
            flag=delta >= RHR_FLAG_BPM,
        )
    return result


def sleep_class(sleep_secs: float | None) -> dict[str, Any]:
    if not sleep_secs:
        return {"hours": None, "class": None}
    hours = sleep_secs / 3600
    cls = (
        "green" if hours >= SLEEP_GREEN_HOURS
        else "amber" if hours >= SLEEP_AMBER_HOURS
        else "red"
    )
    return {"hours": round(hours, 2), "class": cls}


def training_advice(raw: Any) -> str | None:
    """ImReady4's select value to its name. Unknown values stay unknown."""
    if raw is None:
        return None
    try:
        return TRAINING_ADVICE.get(int(raw))
    except (TypeError, ValueError):
        text = str(raw).strip().lower()
        return text if text in TRAINING_ADVICE.values() else None


# -- load -----------------------------------------------------------------

def tsb(ctl: float | None, atl: float | None) -> float | None:
    if ctl is None or atl is None:
        return None
    return round(ctl - atl, 1)


def acwr(daily_load: dict[str, float | None], today: date, *, include_today: bool) -> dict[str, Any]:
    """7-day over 28-day mean daily load.

    Reported as load-progression context only (Impellizzeri et al. 2020,
    COACH.md 5.2). `include_today=False` is Section 11's start-of-day value:
    a session ridden this morning must not move this morning's readiness.
    Days with no row count as zero load: a rest day is load 0, not missing.
    """
    end = today if include_today else today - timedelta(days=1)

    def total(days: int) -> tuple[float, int]:
        vals = [
            float(daily_load.get((end - timedelta(days=i)).isoformat()) or 0)
            for i in range(days)
        ]
        known = sum(
            1 for i in range(days)
            if (end - timedelta(days=i)).isoformat() in daily_load
        )
        return sum(vals), known

    acute, _ = total(7)
    chronic, known = total(28)
    if known < 21 or chronic <= 0:
        return {"value": None, "days_known_28d": known}
    return {
        "value": round((acute / 7) / (chronic / 28), 2),
        "days_known_28d": known,
    }


def monotony(loads: Sequence[float]) -> dict[str, Any]:
    """Foster: mean daily load / SD, over the days given (7 expected).

    Rest days must be in the sequence as zeros; leaving them out is what makes
    a varied week look monotonous. SD of zero (every day identical) has no
    finite monotony and is reported as None rather than infinity.
    """
    if len(loads) < 2:
        return {"monotony": None, "strain": None}
    mean = statistics.fmean(loads)
    sd = statistics.pstdev(loads)
    if sd == 0:
        return {"monotony": None, "strain": None}
    mono = mean / sd
    return {"monotony": round(mono, 2), "strain": round(sum(loads) * mono, 0)}


def session_rpe(rpe: float | None, minutes: float | None) -> float | None:
    """Foster session-RPE: CR-10 RPE times duration in minutes."""
    if rpe is None or not minutes:
        return None
    return round(float(rpe) * float(minutes), 0)


# -- intensity distribution -----------------------------------------------

def zone_seconds(raw: Any) -> list[float] | None:
    """Per-zone seconds from an Intervals.icu activity, in zone order.

    Two shapes are accepted, because the field names are documented but the
    payload was not observed on a real ride when this was written: a list of
    {"id": "Z1", "secs": n} objects (power, which also carries a sweet-spot
    "SS" entry that overlaps Z3/Z4 and must be dropped), or a plain list of
    seconds (HR). Anything else is None, not a guess.
    """
    if not isinstance(raw, list) or not raw:
        return None
    if all(isinstance(x, (int, float)) for x in raw):
        return [float(x) for x in raw]
    zones: dict[int, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            return None
        zid = str(item.get("id") or "")
        if len(zid) >= 2 and zid[0] in "Zz" and zid[1:].isdigit():
            zones[int(zid[1:])] = float(item.get("secs") or 0)
    if not zones:
        return None
    return [zones.get(i, 0.0) for i in range(1, max(zones) + 1)]


def seiler(zone_secs: Sequence[float]) -> tuple[float, float, float]:
    """7-zone (or 5-zone HR) seconds to Seiler's three: Z1-2, Z3, Z4+."""
    easy = sum(zone_secs[:2])
    grey = zone_secs[2] if len(zone_secs) > 2 else 0.0
    hard = sum(zone_secs[3:])
    return easy, grey, hard


def polarization_index(z1: float, z2: float, z3: float) -> float | None:
    """Treff et al. 2019, fractions 0-1. Only for a polarized structure."""
    if not z1 > z3 > z2 or z3 < 0.01:
        return None
    z2 = z2 or 0.01
    return round(math.log10((z1 / z2) * z3 * 100), 2)


def tid(easy: float, grey: float, hard: float) -> dict[str, Any]:
    """Section 11's 5-class TID over Seiler zones, from seconds."""
    total = easy + grey + hard
    if total <= 0:
        return {"class": None, "pi": None, "easy_pct": None,
                "grey_pct": None, "quality_pct": None, "seconds": 0}
    z1, z2, z3 = easy / total, grey / total, hard / total
    pi = polarization_index(z1, z2, z3)
    if z3 < 0.01 and z1 >= max(z2, z3):
        cls = "base"
    elif z1 > z3 > z2 and pi is not None and pi > 2.0:
        cls = "polarized"
    elif z1 > z2 > z3:
        cls = "pyramidal"
    elif z2 >= max(z1, z3):
        cls = "threshold"
    elif z3 >= max(z1, z2):
        cls = "high_intensity"
    else:
        cls = "pyramidal"
    return {
        "class": cls,
        "pi": pi,
        "easy_pct": round(z1 * 100, 1),
        "grey_pct": round(z2 * 100, 1),
        "quality_pct": round(z3 * 100, 1),
        "seconds": round(total),
    }


def tid_drift(acute: dict[str, Any], chronic: dict[str, Any]) -> str | None:
    if not acute.get("class") or not chronic.get("class"):
        return None
    if (
        acute.get("pi") is not None and chronic.get("pi") is not None
        and acute["pi"] < 2.0 <= chronic["pi"]
    ):
        return "acute_depolarization"
    return "consistent" if acute["class"] == chronic["class"] else "shifting"


# -- durability -----------------------------------------------------------

DURABILITY_MIN_SECS = 5400
DURABILITY_MAX_VI = 1.05


def durability_qualifies(activity: dict[str, Any]) -> bool:
    """Section 11 aggregate-durability filter: long, steady, decoupling known."""
    vi = activity.get("icu_variability_index")
    return (
        activity.get("decoupling") is not None
        and vi is not None and 0 < vi <= DURABILITY_MAX_VI
        and (activity.get("moving_time") or 0) >= DURABILITY_MIN_SECS
    )


def durability(
    activities: Iterable[dict[str, Any]], today: date
) -> dict[str, Any]:
    """Mean decoupling of qualifying rides, 7 vs 28 days, with its gates."""
    rows = []
    for act in activities:
        if not durability_qualifies(act):
            continue
        day = str(act.get("start_date_local") or "")[:10]
        try:
            age = (today - date.fromisoformat(day)).days
        except ValueError:
            continue
        if 0 <= age < 28:
            rows.append((age, float(act["decoupling"])))
    d7 = [v for age, v in rows if age < 7]
    d28 = [v for _, v in rows]
    mean7 = round(statistics.fmean(d7), 2) if len(d7) >= 2 else None
    mean28 = round(statistics.fmean(d28), 2) if len(d28) >= 2 else None
    trend = None
    if mean7 is not None and mean28 is not None:
        diff = mean7 - mean28
        trend = "declining" if diff > 1 else "improving" if diff < -1 else "stable"
    flags = []
    if mean28 is not None and mean28 > 5 and len(d28) >= 5:
        flags.append("alarm: 28d mean decoupling above 5%")
    if (
        mean7 is not None and mean28 is not None
        and mean7 - mean28 > 2 and len(d7) >= 3 and len(d28) >= 5
    ):
        flags.append("warning: durability declining")
    if sum(1 for v in d7 if v > 5) >= 3:
        flags.append("warning: 3+ rides above 5% decoupling in 7d")
    return {
        "mean_decoupling_7d": mean7,
        "mean_decoupling_28d": mean28,
        "qualifying_7d": len(d7),
        "qualifying_28d": len(d28),
        "trend": trend,
        "reliability_limited": len(d7) < 3 or len(d28) < 5,
        "flags": flags,
    }


# -- the baseline decision --------------------------------------------------
# Section 11's priority ladder (v11.77, Readiness Decision), with COACH.md's
# modern layer applied to the signals: HRV is the SWC band rather than a
# percentage drop, ImReady4 is a signal of its own when the bridge writes it,
# TSB is never red on its own (-10 to -30 is a normal build), and start-of-day
# ACWR can only corroborate, never decide. Current Feel is not here because
# it is never in the data: the model asks for it, and it may escalate the
# result unconditionally but de-escalate only from P2.

TSB_AMBER = -30.0
ACWR_AMBER = 1.5
_TIER1 = ("imready4", "hrv", "rhr", "sleep")


def signals(
    *,
    advice: str | None,
    hrv: dict[str, Any],
    rhr: dict[str, Any],
    sleep: dict[str, Any],
    tsb_value: float | None,
    acwr_start_of_day: float | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    out["imready4"] = (
        "unavailable" if advice is None
        else "red" if advice == "rest" else "amber" if advice == "lit" else "green"
    )
    if hrv.get("baseline_status") != "established" or hrv.get("status") is None:
        out["hrv"] = "unavailable"
    else:
        out["hrv"] = "amber" if hrv["status"] == "below" else "green"
    if rhr.get("delta") is None:
        out["rhr"] = "unavailable"
    else:
        out["rhr"] = (
            "red" if rhr["delta"] >= RHR_FLAG_BPM
            else "amber" if rhr["delta"] >= 3 else "green"
        )
    out["sleep"] = sleep.get("class") or "unavailable"
    out["tsb"] = (
        "unavailable" if tsb_value is None
        else "amber" if tsb_value < TSB_AMBER else "green"
    )
    out["acwr"] = (
        "unavailable" if acwr_start_of_day is None
        else "amber" if acwr_start_of_day >= ACWR_AMBER else "green"
    )
    return out


def decision(sig: dict[str, str]) -> dict[str, Any]:
    """First match wins. Unavailable signals count for nothing either way."""
    tier1_hit = [k for k in _TIER1 if sig.get(k) in ("amber", "red")]
    reds = [k for k, v in sig.items() if v == "red"]
    ambers = [k for k, v in sig.items() if v == "amber"]
    if sig.get("imready4") == "red":
        return {"recommendation": "skip", "priority": "P1", "because": ["imready4"]}
    if sig.get("tsb") == "amber" and sig.get("hrv") == "amber":
        return {"recommendation": "skip", "priority": "P1", "because": ["tsb", "hrv"]}
    if sig.get("acwr") == "amber" and tier1_hit:
        return {"recommendation": "skip", "priority": "P1", "because": ["acwr", *tier1_hit]}
    if len(reds) >= 2:
        return {"recommendation": "skip", "priority": "P2", "because": reds}
    if reds or len(ambers) >= 2:
        return {"recommendation": "modify", "priority": "P2", "because": reds + ambers}
    known = sum(1 for k in _TIER1 if sig.get(k) not in (None, "unavailable"))
    return {
        "recommendation": "go",
        "priority": "P3",
        "because": ambers,
        # Go on no evidence is not the same as Go on good evidence.
        "tier1_signals_available": known,
    }


# -- one session, read like a coach would ------------------------------------
# What a post-ride analysis turns on beyond the averages: did the main set
# hold (power fade, HR creep across reps at the same power), can the whole-ride
# decoupling be read at all, and was the ride fuelled for its length.

# Friel's aerobic-decoupling reading, only meaningful on a steady ride of an
# hour or more: under 5 % the aerobic system held for that duration.
DECOUPLING_GOOD_PCT = 5.0
DECOUPLING_MIN_SECS = 3600
DECOUPLING_ROUGH_VI = 1.15

# Carbohydrate during exercise by duration (Jeukendrup 2014, Sports Med 44
# S25-33; Thomas, Erdman & Burke 2016, ACSM position stand), grams per hour.
FUEL_BANDS = ((75, 0, 30), (150, 30, 60), (10_000, 60, 90))

# Rep fade beyond this, first third of the set to last, means the target was
# above what he could hold that day (Section 11 VO2-1: stop beyond 5 %).
REP_FADE_PCT = 5.0
# Pace varies less than power rep to rep: 3 % slower is a real fade on a run.
RUN_REP_FADE_PCT = 3.0


def decoupling_read(
    decoupling: float | None, vi: float | None, moving_secs: float | None,
    *, run: bool = False,
) -> dict[str, Any]:
    """Whether a session's whole-session Pa:HR decoupling means anything."""
    if decoupling is None:
        return {"readable": "absent", "why": "no decoupling (no power or pace, or no HR)"}
    if (moving_secs or 0) < DECOUPLING_MIN_SECS:
        return {"readable": "no", "why": "under 60 min: too short to show drift"}
    if run and vi is None:
        # A run without power has no variability index to say it was steady:
        # pace on hills, stops and surges all move the number.
        return {"readable": "rough", "why": "run without power: pace:HR, steadiness "
                "unknown; trust it on flat, even runs only, trend only"}
    if vi is None:
        return {"readable": "no", "why": "variability index unknown"}
    if vi <= DURABILITY_MAX_VI:
        verdict = "held" if decoupling < DECOUPLING_GOOD_PCT else "drifted"
        return {"readable": "yes", "verdict": verdict, "why": f"steady ride, VI {vi:.2f}"}
    if vi <= DECOUPLING_ROUGH_VI:
        return {"readable": "rough", "why": f"VI {vi:.2f}: terrain or surges blur it; trend only"}
    return {
        "readable": "no",
        "why": f"VI {vi:.2f}: intervals or hills -- the number reflects the "
               "structure of the ride, not aerobic drift",
    }


def fuel(moving_secs: float | None, used_g: float | None, ingested_g: float | None) -> dict[str, Any] | None:
    if not moving_secs:
        return None
    hours = moving_secs / 3600
    minutes = moving_secs / 60
    low, high = next((lo, hi) for limit, lo, hi in FUEL_BANDS if minutes <= limit)
    return {
        "guideline_g_per_h": [low, high],
        "burned_est_g_per_h": round(used_g / hours) if used_g else None,
        "ingested_g_per_h": round(ingested_g / hours) if ingested_g else None,
        "ingested_logged": ingested_g is not None,
    }


def _third(xs: list[float]) -> int:
    return max(1, len(xs) // 3)


def interval_set(intervals: Iterable[dict[str, Any]], ftp: float | None) -> dict[str, Any] | None:
    """The main set of a structured ride, and whether it held.

    Intervals.icu tags efforts of the same shape with one group_id
    ("120s@266w74rpm"); the main set is the group with the most work time and
    at least two reps. A ride with no such group was not structured, and gets
    None rather than a set made of whatever surges it had.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for iv in intervals:
        if iv.get("type") != "WORK" or not iv.get("group_id"):
            continue
        if not iv.get("average_watts") or (iv.get("moving_time") or 0) < 10:
            continue
        groups.setdefault(str(iv["group_id"]), []).append(iv)
    sets = [g for g in groups.values() if len(g) >= 2]
    if not sets:
        return None
    reps = sorted(max(sets, key=lambda g: sum(i["moving_time"] for i in g)),
                  key=lambda i: i.get("start_time") or 0)
    watts = [float(i["average_watts"]) for i in reps]
    hrs = [float(i["average_heartrate"]) for i in reps if i.get("average_heartrate")]
    k = _third(watts)
    first, last = statistics.fmean(watts[:k]), statistics.fmean(watts[-k:])
    fade = round((last - first) / first * 100, 1) if first else None
    hr_rise = None
    if len(hrs) == len(reps):
        hr_rise = round(statistics.fmean(hrs[-k:]) - statistics.fmean(hrs[:k]), 1)
    mean_w = statistics.fmean(watts)
    cv = round(statistics.pstdev(watts) / mean_w * 100, 1) if mean_w else None
    secs = [i["moving_time"] for i in reps]
    flags = []
    if fade is not None and fade < -REP_FADE_PCT:
        flags.append(f"power faded {fade}% from first to last third of the set")
    if hr_rise is not None and fade is not None and hr_rise >= 8 and fade > -2:
        flags.append("HR climbed across reps at held power: expected late in a hard set")
    if cv is not None and cv > 6:
        flags.append(f"reps uneven (CV {cv}%): pacing or terrain")
    return {
        "reps": len(reps),
        "rep_secs_median": statistics.median(secs),
        "work_min": round(sum(secs) / 60, 1),
        "mean_w": round(mean_w),
        "mean_pct_ftp": round(mean_w / ftp * 100) if ftp else None,
        "power_fade_pct": fade,
        "hr_rise_bpm": hr_rise,
        "power_cv_pct": cv,
        "rep_detail": [
            {
                "secs": i["moving_time"],
                "w": round(i["average_watts"]),
                "pct_ftp": round(i["average_watts"] / ftp * 100) if ftp else None,
                "hr": i.get("average_heartrate"),
                "hr_max": i.get("max_heartrate"),
                "rpm": round(i["average_cadence"]) if i.get("average_cadence") else None,
                "w_bal_end_j": i.get("wbal_end"),
            }
            for i in reps[:30]
        ],
        "flags": flags,
    }


def pace_str(speed_ms: float | None) -> str | None:
    """m/s to 'm:ss/km'. Intervals.icu keeps every pace as a speed in m/s."""
    if not speed_ms or speed_ms <= 0:
        return None
    secs = round(1000 / speed_ms)
    return f"{secs // 60}:{secs % 60:02d}/km"


def zone_paces(threshold_ms: float | None, upper_pcts: Sequence[float] | None) -> list[dict[str, Any]] | None:
    """Intervals.icu stores pace zones as upper bounds in % of threshold
    speed, so a higher zone is faster. Each zone as min/km, slow edge first."""
    if not threshold_ms or not upper_pcts:
        return None
    out, low = [], None
    for n, pct in enumerate(upper_pcts, start=1):
        high = None if pct >= 999 else threshold_ms * pct / 100
        out.append({"zone": f"Z{n}", "slowest": pace_str(low), "fastest": pace_str(high)})
        low = high
    return out


def run_interval_set(intervals: Iterable[dict[str, Any]], threshold_ms: float | None) -> dict[str, Any] | None:
    """The main set of a structured run without power, judged on pace.

    Same grouping as interval_set; grade-adjusted speed where Intervals gives
    it, so a rep uphill is not read as a slow one. Fade is speed lost from the
    first to the last third, negative meaning slower.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for iv in intervals:
        if iv.get("type") != "WORK" or not iv.get("group_id"):
            continue
        if not (iv.get("gap") or iv.get("average_speed")) or (iv.get("moving_time") or 0) < 10:
            continue
        groups.setdefault(str(iv["group_id"]), []).append(iv)
    sets = [g for g in groups.values() if len(g) >= 2]
    if not sets:
        return None
    reps = sorted(max(sets, key=lambda g: sum(i["moving_time"] for i in g)),
                  key=lambda i: i.get("start_time") or 0)
    speed = [float(i.get("gap") or i["average_speed"]) for i in reps]
    hrs = [float(i["average_heartrate"]) for i in reps if i.get("average_heartrate")]
    k = _third(speed)
    first, last = statistics.fmean(speed[:k]), statistics.fmean(speed[-k:])
    fade = round((last - first) / first * 100, 1) if first else None
    hr_rise = None
    if len(hrs) == len(reps):
        hr_rise = round(statistics.fmean(hrs[-k:]) - statistics.fmean(hrs[:k]), 1)
    mean_v = statistics.fmean(speed)
    cv = round(statistics.pstdev(speed) / mean_v * 100, 1) if mean_v else None
    secs = [i["moving_time"] for i in reps]
    flags = []
    if fade is not None and fade < -RUN_REP_FADE_PCT:
        flags.append(f"pace faded {fade}% from first to last third of the set")
    if hr_rise is not None and fade is not None and hr_rise >= 8 and fade > -2:
        flags.append("HR climbed across reps at held pace: expected late in a hard set")
    if cv is not None and cv > 4:
        flags.append(f"reps uneven (CV {cv}%): pacing or terrain")
    return {
        "reps": len(reps),
        "rep_secs_median": statistics.median(secs),
        "work_min": round(sum(secs) / 60, 1),
        "mean_pace": pace_str(mean_v),
        "pace_basis": "gap" if all(i.get("gap") for i in reps) else "actual",
        "mean_pct_threshold": round(mean_v / threshold_ms * 100) if threshold_ms else None,
        "pace_fade_pct": fade,
        "hr_rise_bpm": hr_rise,
        "pace_cv_pct": cv,
        "rep_detail": [
            {
                "secs": i["moving_time"],
                "pace": pace_str(i.get("gap") or i["average_speed"]),
                "hr": i.get("average_heartrate"),
                "hr_max": i.get("max_heartrate"),
            }
            for i in reps[:30]
        ],
        "flags": flags,
    }


# -- power profile ------------------------------------------------------------

PROFILE_SECS = {5: "sprint_5s", 60: "anaerobic_1m", 300: "vo2_5m",
                1200: "threshold_20m", 3600: "endurance_60m"}


def pick_curve(secs: Sequence[int], values: Sequence[float | None]) -> dict[int, float | None]:
    at = dict(zip(secs, values))
    return {s: at.get(s) for s in PROFILE_SECS}


def power_profile(
    curves: dict[str, dict[int, float | None]], weight_kg: float | None, recent: str, reference: str
) -> list[dict[str, Any]]:
    """Best power per duration in each window, and recent as % of reference."""
    out = []
    for s, label in PROFILE_SECS.items():
        now, ref = curves.get(recent, {}).get(s), curves.get(reference, {}).get(s)
        out.append({
            "duration": label,
            **{f"{name}_w": c.get(s) for name, c in curves.items()},
            f"{recent}_wkg": round(now / weight_kg, 2) if now and weight_kg else None,
            f"{recent}_pct_of_{reference}": round(now / ref * 100) if now and ref else None,
        })
    return out


def zone_watts(ftp: float | None, upper_pcts: Sequence[float] | None) -> list[dict[str, Any]] | None:
    """Intervals.icu stores power zones as upper bounds in % of FTP."""
    if not ftp or not upper_pcts:
        return None
    out, low = [], 0
    for n, pct in enumerate(upper_pcts, start=1):
        high = None if pct >= 999 else round(ftp * pct / 100)
        out.append({"zone": f"Z{n}", "low_w": low, "high_w": high})
        low = (high or 0) + 1
    return out
