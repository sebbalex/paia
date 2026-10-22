"""The Sunday training report: filed by the model, laid out by Python.

A report written as free Markdown came out long, and its look was whatever
the Markdown turned into. So the weekly routine files it through one tool,
`coach_report`, as a few short fields with hard length caps -- the verdict,
what mattered, next week day by day, what to watch -- and this module lays
it out as one designed email. The numbers in it (load, hours, CTL, form,
HRV, eFTP, weeks to the goal) are not the model's: they are computed here
from the same Intervals.icu data the coach tools read (coach skill §2).

Email HTML is its own dialect: tables for layout, every style inline, no
web fonts, images by Content-ID. It is built for Gmail first.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from . import coach_charts as cc
from . import intervals as icu_mod

# -- what the model files ----------------------------------------------------

LIMITS = {
    "headline": 90,
    "summary": 420,
    "item": 160,
    "goal": 220,
    "question": 160,
    "title": 60,
    "purpose": 110,
    "workout": 1500,
}
MAX_ITEMS = 4
MAX_DAYS = 10

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description":
                     f"The week's verdict in one line, at most {LIMITS['headline']} characters."},
        "summary": {"type": "string", "description":
                    f"Two or three sentences, at most {LIMITS['summary']} characters: "
                    "done against planned, and what it means."},
        "highlights": {"type": "array", "items": {"type": "string"}, "description":
                       f"What mattered this week, at most {MAX_ITEMS} lines of "
                       f"{LIMITS['item']} characters, each with the number that proves it."},
        "watch": {"type": "array", "items": {"type": "string"}, "description":
                  f"Useful things to keep an eye on next week (recovery, fuelling, "
                  f"a test, a deload), at most {MAX_ITEMS} lines of {LIMITS['item']}."},
        "goal": {"type": "string", "description":
                 f"Toward the goal, at most {LIMITS['goal']} characters: phase, "
                 "block week, whether this week moved it."},
        "goal_date": {"type": "string", "description":
                      "The goal event's date, YYYY-MM-DD, from context/training-rules.md. "
                      "Weeks to go are computed from it."},
        "next_week": {"type": "array", "description":
                      f"The week coming, one entry per day with something on it, at most {MAX_DAYS}.",
                      "items": {"type": "object", "properties": {
                          "date": {"type": "string", "description": "YYYY-MM-DD, in the week coming."},
                          "title": {"type": "string", "description":
                                    f"Session name, at most {LIMITS['title']}. For a workout "
                                    "already in the calendar, its name there, exactly."},
                          "minutes": {"type": "integer"},
                          "purpose": {"type": "string", "description":
                                      f"Why, and the targets that matter, at most {LIMITS['purpose']}."},
                          "key": {"type": "boolean", "description": "One of the week's key sessions."},
                          "workout": {"type": "string", "description":
                                      "For a ride not yet in the calendar: its steps in "
                                      "Intervals.icu syntax, as coach_plan_workout takes them. "
                                      "Drawn as a chart. Leave empty for gym and rest."},
                      }, "required": ["date", "title"]}},
        "question": {"type": "string", "description":
                     "Optional: one question whose answer would change next week."},
    },
    "required": ["headline", "summary", "next_week"],
}


def _clip(name: str, value: Any, limit_key: str | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    limit = LIMITS[limit_key or name]
    if len(text) > limit:
        raise ValueError(f"{name} is {len(text)} characters; the limit is {limit} -- cut it")
    return text


def validate(args: dict[str, Any], today: date) -> dict[str, Any]:
    """The filed report, checked and normalised. Raises ValueError."""
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    sunday = monday + timedelta(days=6)
    report: dict[str, Any] = {
        "headline": _clip("headline", args.get("headline")),
        "summary": _clip("summary", args.get("summary")),
        "goal": _clip("goal", args.get("goal")),
        "question": _clip("question", args.get("question")),
    }
    if not report["headline"] or not report["summary"]:
        raise ValueError("headline and summary are required")
    for key in ("highlights", "watch"):
        items = [i for i in (args.get(key) or []) if str(i).strip()]
        if len(items) > MAX_ITEMS:
            raise ValueError(f"{key} has {len(items)} lines; at most {MAX_ITEMS}")
        report[key] = [_clip(f"{key} line", i, "item") for i in items]
    report["goal_date"] = None
    if args.get("goal_date"):
        try:
            report["goal_date"] = date.fromisoformat(str(args["goal_date"]).strip())
        except ValueError:
            raise ValueError("goal_date must be YYYY-MM-DD") from None
    days = args.get("next_week") or []
    if not days:
        raise ValueError("next_week is empty: the report always says what comes next")
    if len(days) > MAX_DAYS:
        raise ValueError(f"next_week has {len(days)} entries; at most {MAX_DAYS}")
    out = []
    for d in days:
        try:
            day = date.fromisoformat(str(d.get("date") or "").strip())
        except ValueError:
            raise ValueError(f"next_week date {d.get('date')!r} is not YYYY-MM-DD") from None
        if not monday <= day <= sunday:
            raise ValueError(f"{day} is outside the week coming ({monday} to {sunday})")
        try:
            minutes = int(d.get("minutes") or 0)
        except (TypeError, ValueError):
            raise ValueError("minutes must be a whole number") from None
        out.append({
            "date": day,
            "title": _clip("title", d.get("title")) or "Sessione",
            "minutes": max(0, min(minutes, 900)),
            "purpose": _clip("purpose", d.get("purpose")),
            "key": bool(d.get("key")),
            "workout": str(d.get("workout") or "").strip()[: LIMITS["workout"]],
        })
    report["next_week"] = sorted(out, key=lambda d: d["date"])
    return report


class ReportDesk:
    """Where the tool leaves a filed report for the transport to pick up.

    One slot: routines run one at a time on one Brain, and the transport
    clears it before each run, so a report never reaches the wrong routine.
    """

    def __init__(self) -> None:
        self._report: dict[str, Any] | None = None

    def file(self, report: dict[str, Any]) -> None:
        self._report = report

    def take(self) -> dict[str, Any] | None:
        report, self._report = self._report, None
        return report

    clear = take


# -- what Python computes ----------------------------------------------------


@dataclass
class Tile:
    label: str
    value: str
    note: str = ""
    tone: str = "neutral"  # neutral | good | warn
    unit: str = ""


@dataclass
class Facts:
    week: str
    span: str
    tiles: list[Tile]
    tid: dict[str, Any]
    adherence: str
    weeks_to_goal: int | None
    charts: list[cc.Chart]
    workouts: dict[int, cc.Chart] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _fmt(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}".replace(".", ",").replace("-", "−")


def _delta(now: Any, before: Any, digits: int = 0, unit: str = "") -> str:
    if now is None or before is None:
        return ""
    d = now - before
    if round(d, digits) == 0:
        return "= sett. prima"
    return f"{'+' if d > 0 else '−'}{_fmt(abs(d), digits)}{unit} vs prima"


def _short(day: date) -> str:
    return f"{cc._DAYS_IT[day.weekday()]} {day.day} {cc._MONTHS_IT[day.month - 1]}"


async def facts(icu: "icu_mod.Intervals", today: date,
                report: dict[str, Any] | None) -> Facts:
    monday = today - timedelta(days=today.weekday())
    next_monday = monday + timedelta(days=7)
    rows, _ = await icu.wellness(today - timedelta(days=181), today, today)
    acts, _ = await icu.activities(today - timedelta(days=181), today, today)
    settings, _ = await icu.sport_settings()
    evs, _ = await icu.events(monday, monday + timedelta(days=6), today)
    try:
        nxt, _ = await icu.events(next_monday, next_monday + timedelta(days=6), today)
    except icu_mod.IntervalsError:
        nxt = []
    rows, acts = rows or [], acts or []
    ride = next((s for s in settings or [] if "Ride" in (s.get("types") or [])), {})
    ftp, zones = ride.get("ftp"), ride.get("power_zones")

    y, w, _ = today.isocalendar()
    iso = f"{y}-W{w:02d}"
    py, pw, _ = (monday - timedelta(days=1)).isocalendar()
    prev_iso = f"{py}-W{pw:02d}"
    hist = icu_mod.history(rows, acts, ftp)
    by_week = {h["week"]: h for h in hist["weeks"]}
    this, prev = by_week.get(iso, {}), by_week.get(prev_iso, {})
    in_week = [a for a in acts if icu_mod._day(a) >= monday.isoformat()]
    wk = icu_mod.week(iso, evs or [], in_week,
                      [r for r in rows if str(r.get("id")) >= monday.isoformat()], today)
    rd = icu_mod.readiness(rows, today, icu.hrv_cutover)
    in28 = [a for a in acts if icu_mod._day(a) > (today - timedelta(days=28)).isoformat()]
    ld = icu_mod.load(rows, in28, today)

    hrv = rd["hrv"]
    hrv_ms = round(2.718281828 ** hrv["ln_rmssd_7d"]) if hrv.get("ln_rmssd_7d") else None
    hrv_note = {"normal": "nella fascia normale", "below": "sotto la fascia",
                "above": "sopra la fascia"}.get(hrv.get("status") or "", "baseline in costruzione")
    tsb = rd["load"]["tsb"]
    hidden = len(wk["hidden_strava_sessions"])
    eftp = ld.get("eftp_ride")
    tiles = [
        Tile("Carico", _fmt(this.get("load")), _delta(this.get("load"), prev.get("load"))),
        Tile("Ore in bici", _fmt(this.get("ride_hours_visible"), 1),
             _delta(this.get("ride_hours_visible"), prev.get("ride_hours_visible"), 1, " h"),
             unit="h"),
        Tile("CTL", _fmt(this.get("ctl_end")),
             _delta(this.get("ctl_end"), prev.get("ctl_end"))),
        Tile("Forma TSB", _fmt(tsb),
             "affaticato" if tsb is not None and tsb < -20 else
             "fresco" if tsb is not None and tsb > 5 else "in carico",
             "warn" if tsb is not None and tsb < -30 else "neutral"),
        Tile("HRV 7 g", str(hrv_ms) if hrv_ms else "—", hrv_note,
             "warn" if hrv.get("status") == "below" else
             "good" if hrv.get("status") == "normal" else "neutral", "ms" if hrv_ms else ""),
        Tile("eFTP", _fmt(eftp), f"FTP {ftp} W" if ftp else "", unit="W" if eftp else ""),
    ]
    done = sum(1 for p in wk["planned"] if p["done"])
    adherence = (f"{done} di {len(wk['planned'])} allenamenti pianificati svolti"
                 if wk["planned"] else f"{len(wk['sessions'])} sedute, nessuna pianificata")

    goal = (report or {}).get("goal_date")
    weeks_to_goal = max(0, (goal - today).days // 7) if goal else None

    hist["weeks"] = hist["weeks"][-12:]
    charts = []
    for make in (lambda: cc.fitness(rows, today), lambda: cc.weekly_load(hist, iso)):
        try:
            charts.append(make())
        except Exception:
            cc.log.exception("report chart failed")
    workouts = _workouts((report or {}).get("next_week") or [], nxt or [], ftp, zones)
    return Facts(iso, f"{_short(monday)} – {_short(monday + timedelta(days=6))}",
                 tiles, ld.get("tid_7d") or {}, adherence, weeks_to_goal, charts, workouts,
                 [f"{hidden} attività di questa settimana arrivano da Strava e "
                  "l'API non le mostra: ore e sedute le contano solo in parte"]
                 if hidden else [])


def _workouts(days: list[dict[str, Any]], events: list[dict[str, Any]],
              ftp: float | None, zones: Any) -> dict[int, cc.Chart]:
    """A power profile for each key session of next week, and each one already
    on the calendar: from the calendar when it is there (Intervals parsed
    it), else from the report's own text. Easy rides and gym get none -- a
    flat block says nothing. Keyed by the day's index in the report."""
    cal = {}
    for e in events:
        steps = (e.get("workout_doc") or {}).get("steps")
        if e.get("category") == "WORKOUT" and steps:
            cal[(icu_mod._day(e), str(e.get("name") or "").casefold())] = (e, steps)
    out: dict[int, cc.Chart] = {}
    for i, d in enumerate(days):
        if len(out) >= cc.MAX_WORKOUTS:
            break
        hit = cal.get((d["date"].isoformat(), d["title"].casefold()))
        if not (hit or d["key"]):
            continue
        steps = hit[1] if hit else cc.parse_workout(d.get("workout") or "")
        if not steps:
            continue
        try:
            out[i] = cc.workout_profile(d["title"], d["date"], steps, ftp, zones,
                                        hit[0].get("icu_training_load") if hit else None)
        except Exception:
            cc.log.exception("workout chart failed")
    return out


# -- layout ------------------------------------------------------------------

_BG, _CARD, _INK, _MUTED, _LINE = "#f3f4f6", "#ffffff", "#111827", "#6b7280", "#e5e7eb"
_ACCENT, _WARN, _GOOD = "#2563eb", "#dc2626", "#059669"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _e(text: str) -> str:
    return html.escape(text or "")


def _section(title: str, inner: str) -> str:
    return (
        f'<tr><td style="padding:24px 20px 0">'
        f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
        f'color:{_MUTED};font-weight:600;margin:0 0 12px">{_e(title)}</div>{inner}</td></tr>'
    )


def _bullets(items: list[str], colour: str) -> str:
    return "".join(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 8px"><tr>'
        f'<td width="14" valign="top" style="padding-top:8px"><div style="width:6px;height:6px;'
        f'border-radius:3px;background:{colour}"></div></td>'
        f'<td style="font-size:15px;line-height:1.5;color:{_INK}">{_e(i)}</td></tr></table>'
        for i in items
    )


def _tiles(tiles: list[Tile]) -> str:
    cells = []
    for t in tiles:
        tone = {"warn": _WARN, "good": _GOOD}.get(t.tone, _MUTED)
        cells.append(
            f'<td width="33%" valign="top" style="padding:4px">'
            f'<div style="background:{_BG};border-radius:12px;padding:12px 12px 10px">'
            f'<div style="font-size:12px;color:{_MUTED}">{_e(t.label)}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{_INK};margin:2px 0 2px;'
            f'white-space:nowrap">{_e(t.value)}'
            + (f'<span style="font-size:13px;font-weight:600;color:{_MUTED}"> {_e(t.unit)}</span>'
               if t.unit else "") + '</div>'
            f'<div style="font-size:12px;color:{tone}">{_e(t.note) or "&nbsp;"}</div>'
            f'</div></td>'
        )
    rows = "".join(f"<tr>{''.join(cells[i:i + 3])}</tr>" for i in range(0, len(cells), 3))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'>{rows}</table>')


def _tid_bar(tid: dict[str, Any]) -> str:
    parts = [("easy_pct", "facile", "#93c5fd"), ("grey_pct", "Z3", "#3b82f6"),
             ("quality_pct", "qualità", "#1e3a8a")]
    vals = [(float(tid.get(k) or 0), n, c) for k, n, c in parts]
    if not sum(v for v, _, _ in vals):
        return ""
    cells = "".join(
        f'<td width="{v:.0f}%" style="background:{c};height:10px;font-size:0;line-height:0">&nbsp;</td>'
        for v, _, c in vals if v >= 1
    )
    legend = " · ".join(f"{n} {v:.0f}%" for v, n, _ in vals)
    return (
        f'<div style="font-size:13px;color:{_MUTED};margin:14px 0 6px">'
        f'Intensità 7 giorni: {_e(legend)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-radius:5px;overflow:hidden"><tr>{cells}</tr></table>'
    )


def _img(cid: str, alt: str) -> str:
    return (f'<img src="cid:{cid}" alt="{_e(alt)}" width="584" '
            f'style="display:block;width:100%;max-width:584px;height:auto;border:0;'
            f'border-radius:10px;margin:10px 0 0">')


def _day_rows(days: list[dict[str, Any]], cids: dict[int, str]) -> str:
    out = []
    for i, d in enumerate(days):
        key = d["key"]
        badge = (f'<span style="display:inline-block;background:{_ACCENT};color:#fff;'
                 f'font-size:11px;font-weight:600;border-radius:999px;padding:2px 8px;'
                 f'margin-left:6px;vertical-align:2px">chiave</span>' if key else "")
        mins = f"{d['minutes']}′" if d["minutes"] else ""
        chart = _img(cids[i], d["title"]) if i in cids else ""
        out.append(
            f'<tr><td style="padding:12px 0;border-top:1px solid {_LINE}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="64" valign="top" style="font-size:13px;color:{_MUTED};line-height:1.3">'
            f'<b style="color:{_INK};font-size:14px">{cc._DAYS_IT[d["date"].weekday()]}</b><br>'
            f'{d["date"].day} {cc._MONTHS_IT[d["date"].month - 1]}</td>'
            f'<td valign="top"><div style="font-size:15px;font-weight:600;color:{_INK}">'
            f'{_e(d["title"])}{badge}</div>'
            f'<div style="font-size:14px;color:{_MUTED};line-height:1.45;margin-top:2px">'
            f'{_e(d["purpose"])}</div>{chart}</td>'
            f'<td width="48" valign="top" align="right" style="font-size:14px;font-weight:600;'
            f'color:{_INK}">{mins}</td></tr></table></td></tr>'
        )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{"".join(out)}</table>')


def render_html(report: dict[str, Any], f: Facts) -> tuple[str, list[tuple[str, cc.Chart]]]:
    """The email's HTML and the images it references, as (content id, chart)."""
    images: list[tuple[str, cc.Chart]] = []

    def cid_for(chart: cc.Chart) -> str:
        cid = make_msgid(domain="paia")[1:-1]
        images.append((cid, chart))
        return cid

    day_cids = {i: cid_for(c) for i, c in f.workouts.items()}
    goal_pill = (
        f'<span style="display:inline-block;background:#eef2ff;color:{_ACCENT};'
        f'font-weight:700;border-radius:999px;padding:4px 12px;font-size:13px;'
        f'margin:0 0 10px">{f.weeks_to_goal} settimane all\'obiettivo</span><br>'
        if f.weeks_to_goal is not None else ""
    )
    parts = [
        # Header band.
        f'<tr><td style="background:{_INK};padding:24px 20px 22px;border-radius:16px 16px 0 0">'
        f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
        f'color:#9ca3af;font-weight:600">Settimana {f.week[-3:]} · {_e(f.span)}</div>'
        f'<div style="font-size:24px;line-height:1.25;font-weight:700;color:#fff;margin-top:8px">'
        f'{_e(report["headline"])}</div></td></tr>',
        f'<tr><td style="padding:22px 20px 0;font-size:16px;line-height:1.55;color:{_INK}">'
        f'{_e(report["summary"])}</td></tr>',
        _section("I numeri", _tiles(f.tiles) + _tid_bar(f.tid)
                 + f'<div style="font-size:13px;color:{_MUTED};margin-top:10px">'
                   f'{_e(f.adherence)}</div>'),
    ]
    if report["highlights"]:
        parts.append(_section("Cosa ha contato", _bullets(report["highlights"], _ACCENT)))
    if f.charts:
        parts.append(_section("Tendenza", "".join(_img(cid_for(c), c.title) for c in f.charts)))
    parts.append(_section("La settimana che arriva", _day_rows(report["next_week"], day_cids)))
    if report["watch"]:
        parts.append(_section("Da tenere d'occhio", _bullets(report["watch"], "#f59e0b")))
    if report["goal"] or goal_pill:
        parts.append(_section("Verso l'obiettivo",
                              goal_pill + f'<div style="font-size:15px;line-height:1.5;'
                              f'color:{_INK}">{_e(report["goal"])}</div>'))
    if report["question"]:
        parts.append(
            f'<tr><td style="padding:22px 20px 0"><div style="border-left:3px solid {_ACCENT};'
            f'padding:4px 0 4px 14px;font-size:15px;color:{_INK}"><b>Una domanda:</b> '
            f'{_e(report["question"])}</div></td></tr>'
        )
    footer = " ".join(["Numeri da Intervals.icu, calcolati dal sistema."] + f.missing)
    parts.append(f'<tr><td style="padding:24px 20px 24px;font-size:12px;color:{_MUTED};'
                 f'line-height:1.5">{_e(footer)}</td></tr>')
    body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">'
        f'</head><body style="margin:0;padding:0;background:{_BG};font-family:{_FONT}">'
        f'<div style="display:none;max-height:0;overflow:hidden">{_e(report["summary"])}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_BG}"><tr><td align="center" style="padding:16px 8px">'
        f'<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:640px;background:{_CARD};border-radius:16px">'
        f'{"".join(parts)}</table></td></tr></table></body></html>'
    )
    return body, images


def render_text(report: dict[str, Any], f: Facts) -> str:
    """The same report as short Markdown: the plain-text part, and Telegram."""
    lines = [f"**Settimana {f.week[-3:]} · {f.span}**", f"**{report['headline']}**", "",
             report["summary"], ""]
    lines += [" · ".join(f"{t.label} {t.value}{(' ' + t.unit) if t.unit else ''}"
                         for t in f.tiles), f.adherence, ""]
    if report["highlights"]:
        lines += ["**Cosa ha contato**"] + [f"- {i}" for i in report["highlights"]] + [""]
    lines.append("**La settimana che arriva**")
    for d in report["next_week"]:
        mins = f" ({d['minutes']}′)" if d["minutes"] else ""
        star = " ★" if d["key"] else ""
        lines.append(f"- {_short(d['date'])}: {d['title']}{mins}{star}"
                     + (f" — {d['purpose']}" if d["purpose"] else ""))
    lines.append("")
    if report["watch"]:
        lines += ["**Da tenere d'occhio**"] + [f"- {i}" for i in report["watch"]] + [""]
    if report["goal"] or f.weeks_to_goal is not None:
        weeks = f"{f.weeks_to_goal} settimane all'obiettivo. " if f.weeks_to_goal is not None else ""
        lines += ["**Verso l'obiettivo**", weeks + report["goal"], ""]
    if report["question"]:
        lines.append(f"_Una domanda:_ {report['question']}")
    return "\n".join(lines).strip()


def subject(report: dict[str, Any], f: Facts) -> str:
    return f"Settimana {f.week[-3:]} · {report['headline']}"


def build_email(report: dict[str, Any], f: Facts, sender: str | None,
                recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject(report, f)
    if sender:
        message["From"] = sender
    message["To"] = recipient
    message.set_content(render_text(report, f))
    body, images = render_html(report, f)
    message.add_alternative(body, subtype="html")
    html_part = message.get_payload()[1]
    for cid, chart in images:
        html_part.add_related(chart.png, "image", "png", cid=f"<{cid}>", filename=chart.filename)
    return message
