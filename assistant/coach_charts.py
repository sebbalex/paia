"""Charts for the coach's reports, drawn in Python from Intervals.icu data.

The model has no shell and cannot draw; it also must not do arithmetic on
training numbers (coach skill §2). So charts are made here, from the same
payloads and shaping functions the `coach_*` tools use, and attached to a
routine's message by the transport after the model has written its text.
The routine's prompt lists the chart titles so the text can refer to them.

Rules from the dataviz method: one y-axis per panel (two measures of a
different scale get two panels), categorical colours in a fixed order,
thin marks, a legend whenever there are two or more series.
"""

from __future__ import annotations

import io
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from . import intervals as icu_mod

log = logging.getLogger(__name__)

# Which chart sets a routine may ask for, by the name in its `charts:` field.
CHART_SETS = ("coach-week",)

_SURFACE = "#ffffff"
_INK = "#0b0b0b"
_INK_2 = "#52514e"
_GRID = "#e6e5e1"
_BLUE, _ORANGE, _AQUA = "#2563eb", "#eb6834", "#1baf7a"
# Ordinal easy -> grey -> quality: one hue, light to dark.
_TID = ("#a9c8ee", "#5b9be3", "#1d4f8f")
_DAYS_IT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_MONTHS_IT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
# Intervals.icu's own power-zone colours, Z1..Z7, so a workout drawn here
# reads like the one the owner sees in Intervals and on the head unit.
_ZONE = ("#009e80", "#009e00", "#ffcb0e", "#ff7f0e", "#dd0447", "#6633cc", "#504861")
_DEFAULT_ZONES = (55, 78, 95, 105, 125, 150, 999)
# At most this many workout profiles per report: the key sessions, not a gallery.
MAX_WORKOUTS = 6


@dataclass(frozen=True)
class Chart:
    title: str
    png: bytes

    @property
    def filename(self) -> str:
        safe = "".join(c if c.isascii() and c.isalnum() else "-" for c in self.title.lower())
        return "-".join(p for p in safe.split("-") if p)[:50] + ".png"


# -- drawing helpers ---------------------------------------------------------


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": _SURFACE, "axes.facecolor": _SURFACE,
        "axes.edgecolor": _GRID, "axes.labelcolor": _INK_2,
        "text.color": _INK, "xtick.color": _INK_2, "ytick.color": _INK_2,
        "axes.grid": True, "grid.color": _GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "legend.frameon": False,
        "axes.axisbelow": True,
    })
    return plt


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    fig.clf()
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buf.getvalue()


def _empty(title: str, why: str) -> Chart:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 2))
    ax.axis("off")
    ax.set_title(title)
    ax.text(0.0, 0.4, why, color=_INK_2, transform=ax.transAxes)
    return Chart(title, _png(fig))


# -- the charts, pure over shaped data --------------------------------------


def daily_load(monday: date, acts: list[dict[str, Any]]) -> Chart:
    title = "Carico giornaliero della settimana"
    visible = [a for a in acts if not icu_mod.is_stub(a)]
    hidden = len(acts) - len(visible)
    by_sport = icu_mod._daily_by_sport(visible)
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    cyc = [by_sport["cycling"].get(d, 0.0) for d in days]
    oth = [by_sport["other"].get(d, 0.0) for d in days]
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    x = range(7)
    ax.bar(x, cyc, width=0.6, color=_BLUE, label="bici", edgecolor=_SURFACE, linewidth=2)
    ax.bar(x, oth, width=0.6, bottom=cyc, color=_ORANGE, label="altro (gym, hike)",
           edgecolor=_SURFACE, linewidth=2)
    for i, total in enumerate(c + o for c, o in zip(cyc, oth)):
        if total:
            ax.text(i, total, f"{total:.0f}", ha="center", va="bottom", color=_INK_2, fontsize=9)
    ax.set_xticks(list(x), [f"{_DAYS_IT[i]}\n{days[i][8:]}" for i in x])
    ax.set_ylabel("carico (TSS)")
    ax.set_ylim(0, max([c + o for c, o in zip(cyc, oth)] + [10]) * 1.25)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", ncols=2)
    ax.set_title(title + (f"  ({hidden} da Strava, nascoste)" if hidden else ""))
    return Chart(title, _png(fig))


def fitness(rows: list[dict[str, Any]], today: date, weeks: int = 12) -> Chart:
    title = f"Fitness (CTL), fatica (ATL) e forma (TSB), {weeks} settimane"
    start = (today - timedelta(weeks=weeks)).isoformat()
    pts = sorted(
        (r["id"], r["ctl"], r["atl"]) for r in rows
        if r.get("id") and r["id"] >= start and r.get("ctl") is not None and r.get("atl") is not None
    )
    if len(pts) < 2:
        return _empty(title, "Dati di wellness insufficienti.")
    xs = [date.fromisoformat(p[0]) for p in pts]
    plt = _plt()
    fig, (top, bot) = plt.subplots(2, 1, figsize=(8, 4.6), sharex=True,
                                   gridspec_kw={"height_ratios": [2.2, 1]})
    top.plot(xs, [p[1] for p in pts], color=_BLUE, lw=2, label="CTL (fitness)")
    top.plot(xs, [p[2] for p in pts], color=_ORANGE, lw=2, label="ATL (fatica)")
    top.legend(loc="upper left", ncols=2)
    top.set_title(title)
    tsb = [p[1] - p[2] for p in pts]
    bot.fill_between(xs, tsb, 0, color=_AQUA, alpha=0.35, lw=0)
    bot.plot(xs, tsb, color=_AQUA, lw=2)
    bot.axhline(0, color=_INK_2, lw=0.8)
    bot.axhline(-30, color=_INK_2, lw=0.8, ls=":")
    bot.set_ylabel("TSB")
    fig.autofmt_xdate()
    return Chart(title, _png(fig))


def intensity(load: dict[str, Any]) -> Chart:
    title = "Distribuzione dell'intensità: 7 vs 28 giorni"
    rows = [("ultimi 7 g", load.get("tid_7d") or {}), ("ultimi 28 g", load.get("tid_28d") or {})]
    if not any(r.get("seconds") for _, r in rows):
        return _empty(title, "Nessuna attività con zone leggibili.")
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 2.4))
    labels = (("easy_pct", "facile (Z1-Z2)"), ("grey_pct", "zona grigia (Z3)"),
              ("quality_pct", "qualità (Z4+)"))
    for y, (_, tid) in enumerate(rows):
        left = 0.0
        for (key, name), color in zip(labels, _TID):
            v = float(tid.get(key) or 0)
            ax.barh(y, v, left=left, color=color, edgecolor=_SURFACE, linewidth=2,
                    label=name if y == 0 else None, height=0.55)
            if v >= 6:
                ax.text(left + v / 2, y, f"{v:.0f}%", ha="center", va="center",
                        color=_INK if color != _TID[2] else _SURFACE, fontsize=9)
            left += v
    ax.set_yticks([0, 1], [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("% del tempo")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncols=3)
    ax.set_title(title)
    return Chart(title, _png(fig))


def recovery(rows: list[dict[str, Any]], today: date, cutover: date | None,
             days: int = 60) -> Chart:
    title = f"HRV e frequenza a riposo, {days} giorni"
    start = (today - timedelta(days=days - 1)).isoformat()
    window = sorted((r for r in rows if r.get("id") and r["id"] >= start), key=lambda r: r["id"])
    hrv = [(date.fromisoformat(r["id"]), float(r["hrv"])) for r in window if r.get("hrv")]
    rhr = [(date.fromisoformat(r["id"]), float(r["restingHR"])) for r in window if r.get("restingHR")]
    if not hrv and not rhr:
        return _empty(title, "Nessun dato HRV o FC a riposo.")
    band = icu_mod.readiness(rows, today, cutover)["hrv"]
    plt = _plt()
    fig, (top, bot) = plt.subplots(2, 1, figsize=(8, 4.6), sharex=True)
    if hrv:
        top.scatter([d for d, _ in hrv], [v for _, v in hrv], s=14, color=_BLUE, alpha=0.45,
                    label="rMSSD del giorno")
        roll = []
        for i, (d, _) in enumerate(hrv):
            logs = [math.log(v) for dd, v in hrv[: i + 1] if (d - dd).days < 7]
            roll.append((d, math.exp(sum(logs) / len(logs))))
        top.plot([d for d, _ in roll], [v for _, v in roll], color=_BLUE, lw=2,
                 label="media 7 g")
        if band.get("band_low") is not None:
            top.axhspan(math.exp(band["band_low"]), math.exp(band["band_high"]),
                        color=_AQUA, alpha=0.18, lw=0, label="fascia normale (60 g)")
        top.legend(loc="upper left", ncols=3, fontsize=8)
    top.set_ylabel("HRV rMSSD (ms)")
    top.set_title(title + ("" if cutover else "  (cutover HRV non impostato)"))
    if rhr:
        bot.plot([d for d, _ in rhr], [v for _, v in rhr], color=_ORANGE, lw=2, marker="o", ms=3)
    bot.set_ylabel("FC riposo (bpm)")
    fig.autofmt_xdate()
    return Chart(title, _png(fig))


def power_profile(curve: dict[str, Any]) -> Chart:
    title = "Curva di potenza: ultime 6 settimane in % del migliore dell'anno"
    prof = [p for p in curve.get("profile") or [] if p.get("42d_pct_of_1y") is not None]
    if not prof:
        return _empty(title, "Nessuna curva di potenza disponibile.")
    names = {"sprint_5s": "5 s", "anaerobic_1m": "1 min", "vo2_5m": "5 min",
             "threshold_20m": "20 min", "endurance_60m": "60 min"}
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 3.2))
    xs = range(len(prof))
    pcts = [p["42d_pct_of_1y"] for p in prof]
    ax.bar(xs, pcts, width=0.6, color=_BLUE)
    for i, p in enumerate(prof):
        ax.text(i, pcts[i] - 3, f"{pcts[i]}%\n{p.get('42d_w')} W", ha="center", va="top",
                color=_SURFACE, fontsize=9)
    ax.axhline(100, color=_INK_2, lw=0.8, ls=":")
    ax.set_xticks(list(xs), [names.get(p["duration"], p["duration"]) for p in prof])
    ax.set_ylim(0, 120)
    ax.set_ylabel("% del migliore 12 mesi")
    ax.grid(axis="x", visible=False)
    ax.set_title(title)
    return Chart(title, _png(fig))


def trend(hist: dict[str, Any]) -> Chart:
    title = "Andamento settimanale: eFTP e ore in bici"
    weeks = hist.get("weeks") or []
    if len(weeks) < 2:
        return _empty(title, "Storico insufficiente.")
    labels = [w["week"][-3:] for w in weeks]
    plt = _plt()
    fig, (top, bot) = plt.subplots(2, 1, figsize=(8, 4.6), sharex=True)
    eftp = [w.get("eftp_ride") for w in weeks]
    top.plot(range(len(weeks)), [v if v is not None else float("nan") for v in eftp],
             color=_BLUE, lw=2, marker="o", ms=3)
    ftp = hist.get("ftp_setting_ride")
    if ftp:
        top.axhline(ftp, color=_INK_2, lw=0.8, ls=":")
        top.text(0, ftp, f" FTP impostato {ftp} W", va="bottom", color=_INK_2, fontsize=8)
    top.set_ylabel("eFTP (W)")
    top.set_title(title)
    bot.bar(range(len(weeks)), [w.get("ride_hours_visible") or 0 for w in weeks],
            width=0.6, color=_AQUA)
    bot.set_ylabel("ore in bici")
    bot.grid(axis="x", visible=False)
    step = max(1, len(labels) // 13)
    bot.set_xticks(range(0, len(labels), step), labels[::step], fontsize=8)
    return Chart(title, _png(fig))


# -- workout profiles --------------------------------------------------------

# A planned workout comes in Intervals.icu's `workout_doc` shape: a list of
# steps, each with a duration in seconds and a power target, or a repeat
# block {"reps": n, "steps": [...]}. Calendar events carry it already parsed
# by Intervals; a workout the report prescribes is parsed from the same text
# syntax by `parse_workout` below, into the same shape.

_DUR = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_NOT_POWER = r"(?!\s*(?:LT)?HR|\s*Pace)"
_TARGETS = (
    (re.compile(r"\bramp\s+(\d+)\s*-\s*(\d+)\s*%" + _NOT_POWER, re.I), "%ftp", True),
    (re.compile(r"(?<![\w.])(\d+)\s*-\s*(\d+)\s*%" + _NOT_POWER), "%ftp", False),
    (re.compile(r"(?<![\w.-])(\d+)\s*%" + _NOT_POWER), "%ftp", False),
    (re.compile(r"(?<![\w.])(\d+)\s*-\s*(\d+)\s*w\b", re.I), "w", False),
    (re.compile(r"(?<![\w.-])(\d+)\s*w\b", re.I), "w", False),
    (re.compile(r"(?<!\w)Z([1-7])\b" + r"(?!\s*(?:LT)?HR|\s*Pace)"), "power_zone", False),
)
_REPEAT = re.compile(r"(?:^|\s)(\d+)\s*x\s*$", re.I)


def _seconds(token: str) -> int | None:
    m = _DUR.match(token.lower())
    if not m or not any(m.groups()):
        return None
    h, mi, se = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + se


def _target(text: str) -> dict[str, Any] | None:
    """The first power target in a step's text: '30s 330-350w (121-128%)' is
    330-350 W, the percentage after it only a gloss."""
    found = [(m.start(), i, m, units, ramp) for i, (rx, units, ramp) in enumerate(_TARGETS)
             if (m := rx.search(text))]
    for _, _, m, units, ramp in sorted(found, key=lambda f: f[:2])[:1]:
        nums = [int(g) for g in m.groups()]
        out: dict[str, Any] = {"units": units}
        if len(nums) == 2:
            out["start"], out["end"] = nums
        else:
            out["value"] = nums[0]
        return {"power": out, **({"ramp": True} if ramp else {})}
    return None


def parse_workout(text: str) -> list[dict[str, Any]]:
    """Intervals.icu workout text -> `workout_doc` steps, for drawing only.

    The subset the coach writes: '- 9m33s 55-75%', '- 30s 330-350w',
    '- 10m ramp 50-75%', '- 20m Z2', and a line ending 'Nx' ('Set 1 8x')
    opening a repeat block that runs to the next blank line. A step whose
    target is not power (HR, pace, none) keeps its duration and no power, and
    is drawn grey. Intervals.icu remains the parser that counts; this one only
    draws what the report says.
    """
    steps: list[dict[str, Any]] = []
    block: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            block = None
            continue
        if line.startswith("-"):
            body = line[1:].strip()
            head, _, rest = body.partition(" ")
            secs = _seconds(head)
            if secs is None:
                continue
            step = {"duration": secs, **(_target(rest) or {})}
            (block["steps"] if block is not None else steps).append(step)
            continue
        m = _REPEAT.search(line)
        if m:
            block = {"reps": int(m.group(1)), "text": line, "steps": []}
            steps.append(block)
    return [s for s in steps if s.get("duration") or s.get("steps")]


def _flat(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        if s.get("steps"):
            inner = _flat(s["steps"])
            for _ in range(int(s.get("reps") or 1)):
                out.extend(inner)
        elif s.get("duration"):
            out.append(s)
    return out


def _watts(power: dict[str, Any] | None, ftp: float,
           zones: tuple | list) -> tuple[float, float] | None:
    """(low, high) watts of a power target; None when there is no power."""
    if not power:
        return None
    units = power.get("units")
    lo = power.get("start", power.get("value"))
    hi = power.get("end", power.get("value"))
    if lo is None or hi is None:
        return None
    if units == "%ftp":
        return ftp * lo / 100, ftp * hi / 100
    if units == "w":
        return float(lo), float(hi)
    if units == "power_zone":
        z = int(lo)
        if not 1 <= z <= len(zones):
            return None
        bottom = zones[z - 2] + 1 if z > 1 else 0
        # The last zone is open-ended (999 %): draw it a tenth above its floor.
        top = zones[z - 1] if zones[z - 1] < 999 else bottom * 1.1
        return ftp * bottom / 100, ftp * top / 100
    return None


def _zone_colour(watts: float, ftp: float, zones: tuple | list) -> str:
    pct = 100 * watts / ftp
    for i, top in enumerate(zones[: len(_ZONE)]):
        if pct <= top:
            return _ZONE[i]
    return _ZONE[-1]


def _clock(secs: float) -> str:
    secs = int(round(secs))
    return f"{secs // 3600}h{secs % 3600 // 60:02d}" if secs >= 3600 else f"{secs // 60}m"


def workout_profile(name: str, day: date | None, steps: list[dict[str, Any]],
                    ftp: float | None, zones: tuple | list | None = None,
                    load: float | None = None) -> Chart:
    """One planned workout drawn the way Intervals.icu draws it: blocks of
    target power over time, coloured by zone, a range as a solid block to its
    low end and a pale band to its high end, the FTP as a dotted line."""
    when = f"{_DAYS_IT[day.weekday()]} {day.day} {_MONTHS_IT[day.month - 1]}" if day else ""
    title = "Allenamento: " + name + (f", {when}" if when else "")
    flat = _flat(steps)
    if not flat:
        return _empty(title, "Nessun passo leggibile nel testo dell'allenamento.")
    if not ftp:
        return _empty(title, "FTP non impostato in Intervals.icu: watt non disegnabili.")
    zones = tuple(zones or _DEFAULT_ZONES)
    plt = _plt()
    from matplotlib.patches import Polygon

    fig, ax = plt.subplots(figsize=(8, 2.6))
    t, top, blind = 0.0, ftp, 0
    for s in flat:
        d = float(s["duration"])
        w = _watts(s.get("power"), ftp, zones)
        x0, x1 = t / 60, (t + d) / 60
        if w is None:
            blind += 1
            ax.add_patch(Polygon([(x0, 0), (x0, ftp * 0.4), (x1, ftp * 0.4), (x1, 0)],
                                 closed=True, facecolor=_GRID, edgecolor=_INK_2,
                                 hatch="///", lw=0.5))
        elif s.get("ramp"):
            ax.add_patch(Polygon([(x0, 0), (x0, w[0]), (x1, w[1]), (x1, 0)], closed=True,
                                 facecolor=_zone_colour(max(w), ftp, zones), alpha=0.85, lw=0))
        else:
            lo, hi = min(w), max(w)
            colour = _zone_colour((lo + hi) / 2, ftp, zones)
            ax.add_patch(Polygon([(x0, 0), (x0, lo), (x1, lo), (x1, 0)], closed=True,
                                 facecolor=colour, alpha=0.85, edgecolor=_SURFACE, lw=0.4))
            if hi > lo:
                ax.add_patch(Polygon([(x0, lo), (x0, hi), (x1, hi), (x1, lo)], closed=True,
                                     facecolor=colour, alpha=0.35, lw=0))
        top = max(top, *(w or (0,)))
        t += d
    ax.axhline(ftp, color=_INK_2, lw=0.8, ls=":")
    ax.text(t / 60, ftp, f"FTP {ftp:.0f} W ", ha="right", va="bottom", color=_INK_2, fontsize=8)
    ax.set_xlim(0, t / 60)
    ax.set_ylim(0, top * 1.12)
    ax.set_xlabel("minuti")
    ax.set_ylabel("potenza (W)")
    ax.grid(axis="x", visible=False)
    facts = [_clock(t)] + ([f"carico {load:.0f}"] if load else [])
    if blind:
        facts.append("1 passo senza target di potenza, in grigio" if blind == 1
                     else f"{blind} passi senza target di potenza, in grigio")
    ax.set_title(title + "\n", loc="left")
    ax.text(0, 1.02, "  ·  ".join(facts), transform=ax.transAxes, color=_INK_2, fontsize=9)
    return Chart(title, _png(fig))


def planned_workouts(events: list[dict[str, Any]], ftp: float | None,
                     zones: tuple | list | None) -> list[Chart]:
    """Profiles of the workouts on the Intervals.icu calendar, as Intervals
    parsed them. Date order, at most MAX_WORKOUTS; workouts with no steps
    (gym, a note) are left out."""
    items = []
    for e in events:
        steps = (e.get("workout_doc") or {}).get("steps") or []
        if e.get("category") != "WORKOUT" or not steps:
            continue
        items.append((icu_mod._day(e), str(e.get("name") or "Allenamento"), steps,
                      e.get("icu_training_load")))
    charts = []
    for day, name, steps, load in sorted(items, key=lambda i: i[:2])[:MAX_WORKOUTS]:
        try:
            charts.append(workout_profile(name, date.fromisoformat(day), steps, ftp, zones, load))
        except Exception:
            log.exception("workout chart failed")
    return charts


def weekly_load(hist: dict[str, Any], current: str) -> Chart:
    """Weekly load for the last weeks, the week being reported in the accent."""
    title = f"Carico settimanale, ultime {len(hist.get('weeks') or [])} settimane"
    weeks = hist.get("weeks") or []
    if len(weeks) < 2:
        return _empty(title, "Storico insufficiente.")
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 2.8))
    xs = range(len(weeks))
    loads = [w.get("load") or 0 for w in weeks]
    colours = [_BLUE if w["week"] == current else "#c7d7ee" for w in weeks]
    ax.bar(xs, loads, width=0.62, color=colours)
    for i, w in enumerate(weeks):
        if w["week"] == current or i == len(weeks) - 2:
            ax.text(i, loads[i], f"{loads[i]:.0f}", ha="center", va="bottom",
                    color=_INK if w["week"] == current else _INK_2, fontsize=9)
    ax.set_xticks(list(xs), [w["week"][-3:] for w in weeks], fontsize=8)
    ax.set_ylim(0, max(loads + [10]) * 1.2)
    ax.set_ylabel("carico (TSS)")
    ax.grid(axis="x", visible=False)
    ax.set_title(title)
    return Chart(title, _png(fig))


# -- collecting the data -----------------------------------------------------


async def coach_week(icu: "icu_mod.Intervals", today: date) -> list[Chart]:
    """The six charts of the Sunday report, for the ISO week holding `today`,
    then one profile per workout already planned for the week after."""
    monday = today - timedelta(days=today.weekday())
    rows, _ = await icu.wellness(today - timedelta(days=181), today, today)
    acts, _ = await icu.activities(today - timedelta(days=181), today, today)
    settings, _ = await icu.sport_settings()
    curves, _ = await icu.power_curves()
    rows, acts = rows or [], acts or []
    in28 = [a for a in acts if icu_mod._day(a) > (today - timedelta(days=28)).isoformat()]
    in_week = [a for a in acts if icu_mod._day(a) >= monday.isoformat()]
    ride = next((s for s in settings or [] if "Ride" in (s.get("types") or [])), {})
    hist = icu_mod.history(rows, acts, ride.get("ftp"))
    hist["weeks"] = hist["weeks"][-26:]
    makers = (
        lambda: daily_load(monday, in_week),
        lambda: fitness(rows, today),
        lambda: intensity(icu_mod.load(rows, in28, today)),
        lambda: recovery(rows, today, icu.hrv_cutover),
        lambda: power_profile(icu_mod.power_curve(curves)),
        lambda: trend(hist),
    )
    charts = []
    for make in makers:
        try:
            charts.append(make())
        except Exception:
            # One broken chart must not cost the other five or the report.
            log.exception("chart failed")
    next_monday = monday + timedelta(days=7)
    try:
        evs, _ = await icu.events(next_monday, next_monday + timedelta(days=6), today)
    except icu_mod.IntervalsError:
        log.exception("next week's events unreadable; no workout profiles")
        evs = []
    charts += planned_workouts(evs or [], ride.get("ftp"), ride.get("power_zones"))
    return charts


TITLES = {
    "coach-week": [
        "Carico giornaliero della settimana",
        "Fitness (CTL), fatica (ATL) e forma (TSB), 12 settimane",
        "Distribuzione dell'intensità: 7 vs 28 giorni",
        "HRV e frequenza a riposo, 60 giorni",
        "Curva di potenza: ultime 6 settimane in % del migliore dell'anno",
        "Andamento settimanale: eFTP e ore in bici",
    ],
}


async def render(name: str, icu: "icu_mod.Intervals", today: date) -> list[Chart]:
    if name == "coach-week":
        return await coach_week(icu, today)
    raise ValueError(f"unknown chart set '{name}'")
