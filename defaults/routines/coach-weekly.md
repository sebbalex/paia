---
name: coach-weekly
schedule: 0 19 * * 0
enabled: false
channel: email
charts: coach-week
---

My training week report, the Sunday one: the week that ends today, and the
week that starts tomorrow. Use the `coach` skill and follow it — its §6
"Weekly review" is the method, Section 11's `WEEKLY_REPORT_TEMPLATE.md` is the
structure, and the Section 11 rows for "Weekly review" and "A session or a
week" are the rules. Read them in this turn before the tools.

Work out today's date first. The week closing is the ISO week holding today
(Monday to today); the week coming is the next one.

Data, all through the tools: `coach_week` for this week and next,
`coach_sessions` (14 days), `coach_load`, `coach_history` (182 days),
`coach_power_curve`, `coach_readiness`, `coach_thresholds`. Open
`coach_activity` on each session of the week that was hard or long (a set,
IF ≥ 0.80, or ≥ 2.5 h), and judge it against "Last prescribed" in
`memory/wiki/training.md` when it was a prescribed one.

Six charts are drawn by the system from the same data and attached after
your text, in this order. Refer to them by number where they prove a point
("vedi Grafico 3"); do not describe them line by line and do not invent
what they show — they come from the same tools you read:

1. Carico giornaliero della settimana
2. Fitness (CTL), fatica (ATL) e forma (TSB), 12 settimane
3. Distribuzione dell'intensità: 7 vs 28 giorni
4. HRV e frequenza a riposo, 60 giorni
5. Curva di potenza: ultime 6 settimane in % del migliore dell'anno
6. Andamento settimanale: eFTP e ore in bici

The report, in the owner's language, in the coach's voice (skill §1), with these
headings:

- **La settimana in breve** — two or three sentences: what was done against
  what was planned, load and hours, against last week.
- **Seduta per seduta** — one short paragraph per session that mattered:
  intent, what went well, what did not, with the 1–3 numbers that prove it.
  Easy rides in one line together.
- **Cosa dicono i numeri** — intensity distribution, CTL/ATL/TSB, HRV and
  resting HR, eFTP, Pw:HR in Z2, power curve gaps, each tied to a chart.
  Name the patterns from playbook §5 that apply.
- **Toward the goal** — weeks to go to the goal date in
  `context/training-rules.md`, current phase and block week, whether the week
  moved them toward it, deload due or not.
- **La settimana che arriva** — the week table (day, session, minutes,
  purpose), then the two key sessions written out in full in the seven-part
  format (playbook §6), watts from `coach_thresholds` on the base the skill
  says, plus the gym sessions with exercises, sets and reps. Fit it to his
  availability in `context/training-rules.md` and to the phase in the wiki.
  If the season plan in the wiki is still marked DRAFT, say so in one line
  and prescribe from the phase anyway.

End with the footer the skill specifies. Nobody is there to answer: no "do you
want me to...". At most one question, at the very end, that they can answer in chat
later — only if the answer would change next week (usually RPE or what he
ate on the long ride).

Then update `memory/wiki/training.md` per skill §7: "Last prescribed" gets
next week's key sessions and their success criteria, replacing the old ones;
phase or block if it moved. One line in `memory/wiki/log.md`.

Never write to the training calendar or claim you did.
