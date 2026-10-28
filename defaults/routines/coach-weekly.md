---
name: coach-weekly
schedule: 0 19 * * 0
enabled: false
channel: email
charts: coach-week
---

My training week report, the Sunday one. Use the `coach` skill and follow it:
§6 "Weekly review" is the method, and the Section 11 rows for "Weekly review"
and "A session or a week" are the rules. Read them in this turn before the
tools.

Work out today's date first. The week closing is the ISO week holding today;
the week coming is the next one.

Data, all through the tools: `coach_week` for this week and next,
`coach_sessions` (14 days), `coach_load`, `coach_history` (182 days),
`coach_readiness`, `coach_thresholds`, and `coach_power_curve` if a gap
matters. Open `coach_activity` on each session of the week that was hard or
long (a set, IF ≥ 0.80, or ≥ 2.5 h), and judge it against "Last prescribed"
in `memory/wiki/training.md` when it was a prescribed one.

Then file the report with `coach_report`, once, in the owner's language, in the coach's
voice (skill §1). Short and straight to the point: the owner reads it in two
minutes. The system adds every number as tiles (load, hours, CTL, TSB, HRV,
eFTP, intensity split, weeks to the goal) and the trend charts, so do not
repeat them — write what they mean.

- `headline`: the week's verdict in one line.
- `summary`: two or three sentences, done against planned and what it means.
- `highlights`: up to four lines, the sessions or signals that mattered, each
  with the one number that proves it. Easy rides and runs only if something happened.
- `next_week`: every day with something on it — title, minutes, and a
  purpose with the targets that matter, watts or paces from `coach_thresholds` on the
  base the skill says. Mark the two key sessions `key`. For a key session not in
  the calendar yet, put its steps in `workout` in Intervals.icu syntax exactly
  as `coach_plan_workout` takes them (`- 9m30s 55-75%`, `- 30s 330-350w`, a
  line `Set 1 8x` before a repeat, a blank line after it): it is drawn as a
  chart, and it is the text that goes on the calendar if the owner says yes
  later. A workout already in the calendar keeps its calendar name, exactly,
  and no `workout`. Gym: exercises, sets and reps in the purpose. Fit it to
  the availability in `context/training-rules.md` and the phase in the wiki;
  if the season plan is still marked DRAFT, prescribe from the phase anyway.
- `watch`: up to four useful things for next week — recovery, fuelling, a
  test, a deload due.
- `goal` and `goal_date`: phase and block week, whether this week moved them
  toward the goal in `context/training-rules.md`; its date for the countdown.
- `question`: only if the answer would change next week (usually RPE, or what
  was eaten on the long ride).

A field over its limit is refused: cut it and file again. Nobody is there to
answer, so no "do you want me to...". After filing, end with one line.

Then update `memory/wiki/training.md` per skill §7: "Last prescribed" gets
next week's key sessions and their success criteria, replacing the old ones;
phase or block if it moved. One line in `memory/wiki/log.md`.

Never write to the training calendar or claim you did.
