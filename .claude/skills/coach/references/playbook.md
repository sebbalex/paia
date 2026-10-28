# Coaching playbook

How to turn tool output into judgement. `SKILL.md` says what to do and in
what voice; this file says how to read each kind of session, what a pattern
across sessions means, and how to build the owner's training, in whichever
endurance sports the brief names. Evidence behind the claims is in
`evidence.md`; session templates are in
`section-11/workout-library/WORKOUT_REFERENCE.md` for the bike and
`run-library.md` for running.

## 1. First, name the session's intent

Every analysis starts by saying what the session *was*, because "good" depends
on it. Read in this order: the planned workout it was paired with
(`planned_event_id`), then the name, then `main_set` and `interval_summary`,
then the zone split.

| What the data shows | Intent | Judged on |
|---|---|---|
| `main_set` present, reps ≥ 3 min near 88–105 % FTP | threshold / sweet spot | held power, HR creep, cadence |
| `main_set` present, reps 30 s–5 min above 105 % FTP | VO2 / anaerobic | held power, fade, reps completed |
| no set, IF < 0.70, VI ≤ 1.1, ≥ 60 min | endurance | decoupling, Pw:HR Z2, grey time |
| no set, ≥ 2.5 h | long / durability | decoupling if readable, late-ride power, fuel |
| no set, IF 0.75–0.90, high VI, hilly | **unstructured hard ride** | what it trained, and whether that was meant |
| MTB without power | MTB (HR only) | HR zones, time, climbing, technique |
| run, `main_set` reps near Z4 pace | run threshold (RUN-T1/T2) | held pace (`pace_fade_pct`), HR creep |
| run, `main_set` reps ≤ 5 min at Z5+ or on a hill | run VO2 / hills (RUN-V*, RUN-H1) | held pace or effort, reps completed |
| run, no set, Z1–Z2 | easy / long run | HR stays easy, GAP on hills, decoupling if readable, niggles |
| trail run | RUN-TR | HR, vertical, descent load — never raw pace |
| Hike, WeightTraining | cross-training | fatigue it adds, RPE |

The fifth row is common among amateurs with short weekday slots (hilly loops
at IF ≈ 0.8, VI 1.4–1.5). It is not a bad ride. It is a ride without a clear
purpose: too hard to be recovery, too broken up to be threshold work. Say so
when it is a pattern, not every time.

## 2. Reading a structured session

From `coach_activity.main_set`:

- **Target hit?** `mean_pct_ftp` against the template's zone. If FTP and eFTP
  differ by more than 5 % (`coach_thresholds.eftp_vs_ftp_pct`), say which
  one the % is measured against: 98 % of a stale FTP may be 105 % of their
  real one.
- **Did it hold?** `power_fade_pct` from first to last third:
  - within ±3 %: held. Good pacing, target right.
  - −3 to −5 %: tired at the end. Fine for a last set; the next session can
    stay the same.
  - below −5 %: target too high for that day, or they started too hard. Look at
    rep 1 against the rest. A first rep 5 %+ above the mean is a pacing
    error, not a fitness one.
  - positive: started cautious. Next time the target can go up.
- **What did it cost?** `hr_rise_bpm` with power held: 5–10 bpm across a
  hard set is normal cardiac drift and means the set was demanding (for 4×8,
  reaching ~90 % HRmax in the last reps is the point: Seiler 2013). Much more,
  or HR rising with power falling, means the set went beyond what they could
  sustain.
- **Consistency:** `power_cv_pct` above 6 %: uneven reps. On a hill loop it
  may be terrain (alternating steep and shallow sections); say that is the
  likely reason when reps alternate high–low regularly.
- **Cadence:** reps below ~70 rpm on the road are torque work, whether meant
  or not. For a goal with steep climbing it is a relevant skill; it is also harder on the knees
  and the legs. Name it.
- **W′:** `w_bal_end_j` near zero on a rep means they emptied W′ there; good for
  VO2 work, a sign of over-pacing on threshold work.

## 3. Reading an endurance or long ride

- **Decoupling only if `decoupling_read.readable` is `yes`.** On `rough`
  rides it is a trend hint at most; on `no` do not mention the number at all
  except to say why it can't be read. A 16 % decoupling on an interval ride
  is the structure of the ride, not a fitness problem.
- **Pw:HR in Z2** (`aerobic.power_hr_z2`, with ≥ 20 min of Z2): watts per
  beat at easy intensity. One ride means little; the weekly trend in
  `coach_history.power_hr_z2_mean` rising over 4–8 weeks is aerobic fitness
  improving. Heat (`temp_avg_c` > 25 °C), poor sleep or a cold lowers it.
- **Grey time on an easy ride:** an endurance ride with more than ~15 %
  grey (Z3) drifted. Usually hills or a group. Point it out with the fix
  (lower gear, cap the climbs at the top of Z2).
- **Long rides:** read `fuel`. Burned vs guideline vs eaten. If eating is not
  logged, ask once what they ate and drank; a goal event of several hours is won or
lost on fuelling, and
  the gut is trained like the legs (Jeukendrup 2014).
- **HRR** (`hrrc_bpm.hrr`): heart-rate drop in 60 s after a hard effort.
  Only meaningful as their own trend.

## 3b. Reading a run

`coach_activity` on a run carries `pace` (avg, `gap`, `pct_threshold`) and,
for a structured run without power, a pace-based `main_set`.

- **Pace on hills is GAP.** Raw pace on a rolling route says nothing; judge
  `gap`, and on trail judge HR and vertical only.
- **Intensity from the run's own thresholds.** `pct_threshold` and pace zones
  come from the run settings. When `missing_inputs` says there is no
  threshold pace, the run is judged on HR — and only if the run HR zones are
  its own (`coach_thresholds.hr_zones_copied_from_ride` false). Running HR
  sits higher than cycling HR at the same effort; bike zones on a run read
  every easy run as too hard.
- **Main set:** `pace_fade_pct` within ±2 % held; −2 to −3 % tired; beyond
  −3 % target too fast or started too hard (rep 1 against the rest).
  `pace_cv_pct` > 4 % is uneven pacing or terrain.
- **Decoupling on runs** is `rough` without power: a trend on flat, even
  runs, nothing more.
- **Easy runs drifting to Z3** are the runner's version of the grey ride:
  slow down on climbs, walk if needed.
- **Ask about niggles** after a run that was longer or faster than usual, or
  when the brief lists an injury. One question; a "yes" changes the next run
  (`run-library.md` §0 rule 5).
- **Cadence** (`cadence_avg`, steps per minute as Garmin reports it): a trend
  only. Do not prescribe a number.

## 4. MTB, hikes, gym

- **A bike without power** (the brief says which): HR zones only. HR lags
  and overstates intensity on short surges and understates it on technical
  descents. Report time, climbing, HR distribution; do not invent watts. When
  the goal is ridden on that bike, say that power on it would make these
  rides readable.
- **Hikes**: fatigue, not cycling fitness. A multi-day trek (e.g. Alta Via)
  is a real load on the legs even at low HR: expect a few days of flat legs.
- **Gym**: needs session RPE. Heavy legs the day after are expected in the
  first 2–3 weeks of a strength block; plan the bike around it.

## 5. Patterns across sessions (coach_sessions, coach_history)

Look for these every weekly review and whenever the owner asks "how am I doing":

1. **Everything medium.** Most rides IF 0.75–0.85, few below 0.65, no set
   above threshold. Fix: make two sessions a week clearly hard and structured,
   and the rest clearly easy.
2. **No easy days.** Grey + quality > 35 % on most rides. Same fix.
3. **Missing top end.** `coach_power_curve` 5 s and 1 min at < 85 % of the
   year's best while 20 min holds: no short hard efforts lately. Not a loss
   of fitness until they have tried; it is a gap in the training.
4. **Missing long sustained power.** 60 min well below season best: no long
   steady efforts. For a goal with long climbs this is the one to rebuild.
5. **eFTP drifting down** over 6+ weeks with CTL falling: detraining or a
   lighter phase, which may be fine after a long season. Say which.
6. **Pw:HR Z2 rising** over weeks: aerobic base improving, even if eFTP is
   flat.
7. **Load spikes after trips.** A week of hiking followed by hard rides.
8. **Run volume jumping.** `coach_history.run_km_visible` or
   `run_hours_visible` up > 30 % over two weeks, or > 10 % a week for
   three: the injury pattern in `evidence.md` "Running". Say it before the
   niggle does.
9. **Hard days in two sports.** A bike VO2 and a run threshold in the same
    48 h: two hard sessions, not one each. Check `coach_load.monotony_by_sport_7d`
    and the week's sessions across sports, not per sport.
10. **Nothing logged.** No RPE or feel on any ride: the analysis is power-only.
   Ask them to add RPE in Intervals or tell you after rides.

## 6. Building training

### The athlete's frame

Everything personal comes from `context/training-rules.md` (sports and their
weekly sessions, main sport, age, weight, availability, goal event, its sport
and distance, devices) and from
`memory/wiki/training.md` (agreed plan, current phase). **What the goal event
demands -- distance, climbing, terrain, duration -- is in the brief or must be
asked.** Earlier events in the season are training races unless the brief
says otherwise.

### Macrocycle to the goal (default, counted back from the goal date)

Adjust from the data; this is the default, not a script. Write the dated
version into `memory/wiki/training.md` once the owner agrees, and move the
current phase there when the data says so.

| Phase | Length | Focus |
|---|---|---|
| Prep | whatever remains before Base, typically 6–8 weeks | Heavy strength 2×/week. Z2 rides. One quality ride/week (sweet spot or 30/15) to keep top end. Fix FTP. |
| Base | ~10 weeks | Strength → 1×/week maintenance. Long ride grows to 3–4 h. Two quality rides: threshold progression + one VO2. |
| Build | ~9 weeks | VO2 and threshold, efforts shaped like the goal's demands (long climbs at 75–90 % FTP, surges, low cadence where relevant). Earlier events as B races. Fuel practice at 80–90 g/h. |
| Specific | ~4 weeks | Race simulations on the goal's bike and terrain: long days, back-to-back weekend days. Durability. |
| Taper | last ~2 weeks | Volume −40–60 %, intensity and frequency kept (Bosquet 2007). Section 11 race-week protocol. |

For a running goal the same phases hold, with run templates
(`run-library.md` §3): Prep is easy running and hill sprints, building
frequency; Base adds RUN-T1 and the long run; Build adds VO2 (RUN-V*) and
race-pace work (RUN-L2); Specific is race-pace long runs and, for trail,
vertical and descents; Taper the same cut. Whatever the bike phase, run
volume follows the impact rules (`run-library.md` §0), not the bike's ±10 %
alone.

### More than one sport

The brief says which sports and which is main. One plan, one fatigue:

- **Two hard sessions a week in total**, not per sport. The main sport gets
  the hard sessions that matter most for the goal; the other sport is mostly
  easy volume, with at most one hard session of its own when the goal needs
  it.
- **Riding is the recovery sport.** When the legs or a niggle say no to a
  run, an easy ride keeps the aerobic work with no impact. The reverse is not
  true: a run is never recovery from a hard ride.
- **Order in a day:** hard first, easy second; never a hard run the day after
  the long ride or a long run the day after a hard ride.
- **Brick** (bike then run): one hard session. Only when the goal is
  multisport.
- Load is one number across sports (`coach_load`), split in
  `load_7d_cycling` / `load_7d_running`; Intervals' load for runs and rides
  is not the same physiological cost — a run's load costs more recovery.

Example week, cyclist with a running goal second (adapt to the brief's
slots):

| Day | Session |
|---|---|
| Mon | Rest or gym |
| Tue | Bike hard (VO2) |
| Wed | RUN-E2 |
| Thu | Run hard (RUN-T1 or RUN-H1) |
| Fri | Rest or easy ride |
| Sat | Long ride |
| Sun | RUN-L1 short, or rest |

### Their week

The workout library's patterns assume free weekdays. When the brief gives a
short weekday slot, fit the session to it: a 60 min slot is 10–15 min warm-up, 35–40 min of work, 5–10 min cool-down
(`WORKOUT_REFERENCE.md` §5.4 truncation rules). Default week, for weekday
slots of about an hour and a free weekend:

| Day | Session |
|---|---|
| Mon | Rest, or gym (after a weekend long ride, the legs can lift; the bike can't recover) |
| Tue | Hard 1: VO2 (VO2-2 30/15, VO2-1 4–5×3–4′) — freshest weekday |
| Wed | Easy: AE-1 Z2 60′, flat route, or gym if Mon was rest |
| Thu | Hard 2: threshold / sweet spot (SS-5 3×10′, TH-1 2×15′, TH-2 3×8′ fits in 60′) |
| Fri | Rest or AE-4 |
| Sat | Long: AE-3 (road) or MTB with long climbs; durability, fuelling practice |
| Sun | Easy MTB or Z2, skills; or rest |

Gym goes on a hard day (after the ride, hours later) or a rest day, never
the day before Tue/Thu or the long ride (Wilson 2012; library §3.4).

### Gym: legs for endurance

Prep phase, 2×/week; base onwards, 1×/week heavy (Rønnestad 2010, both
studies). After a warm-up, 3 sets:

- half squat or leg press — 10 RM → 6 RM → 4–5 RM over the weeks
- single-leg leg press or Bulgarian split squat — same
- hip thrust or Romanian deadlift
- standing calf raise
- plus core (plank variations, dead bug) and hip mobility

Runners add single-leg work and calf/Achilles loading (eccentric calf
raises, step-downs) and, once the base is there, low-volume plyometrics
(pogo hops, skips): economy and injury resistance (Blagrove 2018; Lauersen
2014).

Lift heavy and controlled down, push fast. No sets to failure in the week
of a key session. New to heavy lifting: two weeks at 12–15 RM first to learn
the movement. They log RPE so the load counts (Foster 2001).

### Writing one session

Every prescribed session has, in the owner's language (headings translated):

1. **Name and template** (`TH-2`, `VO2-2`, `RUN-T1`…) and the phase it
   belongs to.
2. **Structure** with times and targets from `coach_thresholds`: watts for
   the bike (and which base, FTP or eFTP), pace zones in min/km for a run
   (or HR when the run has no threshold pace). Cadence where it matters; HR
   expectation for the last reps.
3. **Purpose** in one or two sentences of physiology in plain words: what this
   session changes in them and why it matters for the goal.
4. **Why this and not something else**: one evidence line from `evidence.md`.
5. **How we'll know it went well**: the criterion you will check in the
   post-session analysis (fade within 3 %, HR in the last rep at 160–170 bpm,
   decoupling < 5 %…). This closes the loop: the next analysis judges against
   it.
6. **If…**: what to do if the legs are bad (drop a rep, switch to AE-1), or if
   they have only 45 minutes.
7. **Fuel** if > 75 min.

Rules from the library still hold: progression only when its criteria are
met; ±10 % volume per week (and the run impact caps); 48 h between hard
sessions across sports; readiness has the final say.
