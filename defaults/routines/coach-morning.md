---
name: coach-morning
schedule: 00 9 * * *
enabled: false
channel: telegram
---

My training morning call: yesterday, and today. Use the `coach` skill and
follow it — §3 for yesterday, §4 for today. Read the Section 11 rows for
"Analysing a session" and "Readiness, go or skip" in this turn, before the
tools; `PRE_WORKOUT_REPORT_TEMPLATE.md` and `POST_WORKOUT_REPORT_TEMPLATE.md`
under its `reports/` are the structure.

Work out today's date first; yesterday is the day before.

Data, all through the tools: `coach_readiness` (today), `coach_sessions`
(7 days) to find yesterday's sessions and see them in context,
`coach_activity` on each of yesterday's sessions, `coach_week` for what is
planned today, `coach_thresholds` for watts, `coach_load` if the week's load
matters to the call. Read "Last prescribed" in `memory/wiki/training.md` and
judge yesterday against it when it was prescribed. For the shape of the
week, use the availability in `context/training-rules.md`.

The message, in the owner's language, in the coach's voice (skill §1), two parts:

**Yesterday** — if they trained: what the session was, what was good, what was not,
in 6–12 lines with the numbers that prove it (skill §3, shorter). Several
sessions: the main one in full, the others in a line. Rest day: one line
saying so, and whether that was the right call given the load. A hike or
gym session: the load it added and what it means for today.

**Today** — the morning call in the shape of skill §4: GO / MODIFY / SKIP,
why in 2–3 sentences, and today's session in the seven-part format, compact.
If the Intervals calendar has a workout planned, judge it against readiness
and keep or modify it; if nothing is planned, propose the session from the
phase in the wiki and the week so far (two hard sessions a week, 48 h apart,
gym on a hard or rest day, never the day before a key session or the long
ride). Fit it to the weekday slot in `context/training-rules.md`. If the
rules ask for a pre-ride reminder (power meter zero-offset, say), end with it
on riding days.

Then the footer the skill specifies. Nobody is there to answer now: no "do you
want me to...". At most one question at the end, answerable later in chat, only
when it would change today's call or the reading of yesterday (usually Feel
or RPE).

If readiness shows a symptom-level signal (resting HR ≥ +5 bpm, or anything
the skill §8 covers), say in full sentences that they should check how
they feel and not train hard until they have; no training detail in that part.

Write to the wiki only when something durable changed (skill §7) — a daily
call is not a log line. Never write to the training calendar.
