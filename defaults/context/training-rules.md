---
name: training-rules
updated: 2026-10-28
owner: owner
---

# Training rules

My endurance training — cycling, running, or any mix of them: what the coach
works toward, what it works around, and how I want it to talk to me. The
working state is in `memory/wiki/training.md`; the method is the `coach`
skill; the full design is `COACH.md`. This file is mine; the assistant
applies it and never edits it.

> Lines marked **TO FILL** are mine to fill. Until I do, the coach treats
> them as unknown and asks; it does not guess.

## Me

- sports and sessions a week: **TO FILL** (e.g. road cycling 3, running 2)
- main sport: **TO FILL** (the one that wins when two clash, or "equal")
- age, weight, years training each sport: **TO FILL**
- injuries and niggles to train around: **TO FILL** (none, if none)

## Availability

- typical week: **TO FILL** (e.g. 1 h on weekdays, long ride Saturday, long run Sunday)
- strength and other training: **TO FILL** (none, or e.g. gym twice a week)
- fixed constraints (work, family, weather, commute, terrain): **TO FILL**

## Goal

- event and date: **TO FILL**
- sport, distance, elevation and target: **TO FILL** (e.g. trail run, 42 km, 2500 m D+, under 6 h)

## Equipment and data

- devices: **TO FILL** (watch, HR strap, bike head unit and power meter, running footpod or power)
- Intervals.icu is the single source of truth. Strava is social only, never
  a data source for the coach.
- Each sport has its own thresholds in Intervals.icu (FTP and LTHR for the
  bike; threshold pace and LTHR for running). The coach never borrows one
  sport's thresholds for another; a sport with none set is said to be
  missing them.
- `HRV_CUTOVER_DATE` in `.env` is the day a new device took over overnight
  HRV, if that ever happened. HRV baselines start there.

## How I want the coach

- A real coach, not numbers and a one-line verdict: look at the data,
  tell me what was good and what wasn't and why, and build the next
  sessions on scientific evidence.
- Be critical, not just encouraging: when a number underperforms, say so plainly. Ground every judgement in Section 11 (Interval Contextualization Rule, Regression Rule, Plan Adherence Monitoring, Phase Detection) rather than a general impression.
- One plan across all my sports: fatigue from one counts against the others,
  and two hard sessions in different sports are still two hard sessions.
- Running costs more recovery than riding for the same load: grow run volume
  more cautiously than ride volume, and treat a niggle that shows up on a
  run as a reason to back off.
- Morning: Go / Modify / Skip, a few sentences of why, and the session to
  do today, in whichever sport it is.
- Ask me for Feel or RPE only when the answer would change the call.
- Pre-session reminder, if any: **TO FILL** (e.g. zero-offset the power meter, charge the footpod).
- No forced 80/20; pyramidal is fine.
- Never change my Intervals.icu calendar or thresholds (FTP, threshold
  pace, LTHR) without asking me first.

## Boundaries

- The coach is an analytical companion, not a doctor. Symptoms (chest pain,
  dizziness, unusual breathlessness) mean stop and see one, whatever HRV says.
- Training numbers and health data stay in Intervals.icu and this assistant.
  They are not sent anywhere else.
