# Running workout library

The running counterpart of `section-11/workout-library/WORKOUT_REFERENCE.md`,
which is cycling-only and invites exactly this addition (its §6.1, §6.2).
Same template shape, same rules: Section 11 A readiness has the final say,
pick a template and name it, adapt within it, progress one variable a week,
never invent a format when one here fits. Where the bike library's general
rules (warm-up logic, §5.2 progression order, §5.4 truncation) are not
contradicted below, they hold for runs too.

Ours, not vendored: edit it like `playbook.md`. Evidence for the claims is
in `evidence.md`, section "Running".

## 0. Targets, and what makes running different

**Targets.** Pace zones from `coach_thresholds` (`pace_zones_per_km`, on the
run's own threshold pace), never derived from the bike. On hills and trails
judge by grade-adjusted pace (`gap`) or HR, not raw pace. If the run has no
threshold pace set, prescribe by HR zones (run LTHR) and say so; if its HR
zones are `hr_zones_copied_from_ride`, the run LTHR is unknown too: prescribe
by RPE and talk test (easy = full sentences, threshold = a few words), and
suggest a test (RUN-TEST).

Zone words below follow Intervals.icu's seven pace zones: Z1–Z2 easy, Z3
steady/marathon, Z4 threshold, Z5+ VO2 and faster. Read the actual min/km
from the tool; do not quote these as numbers.

**Impact.** Running costs more recovery than riding for the same load, and
injury comes from the tissues (tendons, bone) adapting slower than the heart
and lungs. A cyclist's engine will happily run faster and longer than their
legs can take. So, always:

1. **Run volume grows slower than ride volume.** Weekly run time or distance
   rises by about 10 % at most, and never more than ~30 % over any two weeks.
   After 3 weeks of growth, one easier week.
2. **Frequency before duration, duration before intensity.** Three short runs
   beat one long one for a returning or new runner.
3. **48 h between hard runs, and a hard run counts as one of the week's two
   hard sessions whatever the sport.** A bike VO2 day and a run threshold day
   are two hard days. A brick (bike → run) is one hard session.
4. **Long run ≤ ~30 % of weekly run volume**, and not on the day after the
   long ride unless the goal demands back-to-back fatigue (Specific phase).
5. **Pain that changes the stride stops the run.** A niggle that is there the
   next morning means the next run is easy or replaced by a ride. Never a
   diagnosis (SKILL.md §8): name it, adjust, and if it persists past a week
   say to see a physio.
6. **Downhill is the hidden load.** A trail run with a lot of descent costs
   far more muscle damage than its HR shows; expect sore quads 24–48 h later
   and keep the next day easy.

**Write steps in time, not distance.** Intervals and the report charts both
handle `- 10m ...` reliably; distance steps are not drawn. Syntax for
`coach_plan_workout` (type `Run` or `TrailRun`):

```
- 15m Z2 Pace
- 5m Z3 Pace

Main set 5x
- 3m 100-104% Pace
- 2m 70% Pace

- 10m Z1 Pace
```

Pace targets as `Z2 Pace` or `95-100% Pace` (% of threshold speed: above 100
is faster). HR targets as `Z2 HR` when pace is not set or not meaningful
(trails, heat).

## 1. Templates

### 1A. Easy and long

#### RUN-E1: Easy run
```yaml
id: RUN-E1
domain: endurance
is_hard_session: false
work_minutes: 40
est_total_minutes: 40
```
- **Zones:** Z1–Z2 pace, or HR Z2; conversational.
- **Structure:** continuous, flat or rolling. Walk breaks are allowed and
  not a failure (returning runners: 4′ run / 1′ walk).
- **Duration:** 25–60 min.
- **Coaching notes:** the bulk of run volume. If HR drifts into Z3 on a hill,
  slow down or walk it; easy is judged by HR, not pace.
- **Select when:** every week; the default run; the day after a hard session.

#### RUN-E2: Easy run + strides
```yaml
id: RUN-E2
domain: endurance
is_hard_session: false
work_minutes: 45
est_total_minutes: 45
```
- **Zones:** RUN-E1, then 4–8 × 20 s strides at relaxed fast pace (not a
  sprint), full walk/jog recovery 60–90 s.
- **Duration:** 30–50 min easy + strides at the end.
- **Coaching notes:** economy and leg speed with almost no fatigue cost.
  Smooth, tall, quick feet; stop before form breaks.
- **Select when:** 1–2× a week in every phase; the run before a hard day or
  a race.

#### RUN-L1: Long run
```yaml
id: RUN-L1
domain: endurance
is_hard_session: false
work_minutes: 90
est_total_minutes: 90
```
- **Zones:** Z1–Z2 throughout.
- **Duration:** 60–150 min, by goal and history; grows by ~10 min a week at
  most, capped at ~30 % of weekly run volume.
- **Coaching notes:** durability and tissue tolerance. Read decoupling only
  if `decoupling_read.readable` allows (runs without power are `rough` at
  best). Fuel above ~75 min, practise race nutrition (gut training). Not a
  hard session, but not free either: no hard session the day after.
- **Select when:** weekly from Base onward for half marathon and longer.

#### RUN-L2: Long run, fast finish
```yaml
id: RUN-L2
domain: race_specific
is_hard_session: true
work_minutes: 100
est_total_minutes: 100
```
- **Zones:** Z2 for most, final 15–30 min at goal race pace (Z3 for
  marathon, Z4 low for half).
- **Duration:** 75–130 min.
- **Coaching notes:** pacing on tired legs. Counts as a hard session.
- **Select when:** Build and Specific, at most every other week, only after
  3+ weeks of RUN-L1 at that length without a niggle.

#### RUN-TR: Trail / vertical
```yaml
id: RUN-TR
domain: endurance
is_hard_session: false
work_minutes: 90
est_total_minutes: 90
```
- **Zones:** HR Z2 on climbs (power-hike when HR would pass Z2), easy on
  descents with controlled form.
- **Duration:** by vertical rather than distance: 300–1500 m D+.
- **Coaching notes:** for trail or mountain goals. Descents are the loading
  stimulus for the quads — build them like volume, gradually. Judge by HR
  and vertical, never by pace.
- **Select when:** trail goal, Base onward; replaces RUN-L1 on alternate
  weeks.

### 1B. Threshold

#### RUN-T1: Cruise intervals
```yaml
id: RUN-T1
domain: threshold
is_hard_session: true
work_minutes: 25
est_total_minutes: 50
```
- **Zones:** Z4 (≈ 95–100 % of threshold pace), "comfortably hard".
- **Structure:** 4–6 × 5 min, 1 min easy jog; progress to 3 × 10 min, 2 min.
- **Coaching notes:** the core threshold session. Reps even or slightly
  faster; `pace_fade_pct` beyond −3 % means the target was too fast.
- **Select when:** Base and Build; the first threshold session of a block.

#### RUN-T2: Tempo run
```yaml
id: RUN-T2
domain: threshold
is_hard_session: true
work_minutes: 25
est_total_minutes: 50
```
- **Zones:** Z3–low Z4 continuous.
- **Structure:** 15–30 min continuous, or 2 × 15 min, 3 min jog.
- **Coaching notes:** sustained pace control; HR should rise gently and
  level, not climb throughout.
- **Select when:** after RUN-T1 at 3 × 10 is held; half marathon and longer.

### 1C. VO2max

#### RUN-V1: Long VO2 intervals
```yaml
id: RUN-V1
domain: vo2max
is_hard_session: true
work_minutes: 16
est_total_minutes: 50
```
- **Zones:** Z5 (≈ 3–5 km race pace), reaching ~90–95 % HRmax by the
  last reps.
- **Structure:** 4–5 × 3–4 min, recovery equal to rep time as easy jog.
- **Coaching notes:** same physiology as the bike's VO2-1, at a much higher
  musculoskeletal cost. A rep slower than the first by > 3 % ends the set.
- **Select when:** Build; only with ≥ 4 weeks of consistent easy running
  and a threshold session held.

#### RUN-V2: 30/30
```yaml
id: RUN-V2
domain: vo2max
is_hard_session: true
work_minutes: 12
est_total_minutes: 45
```
- **Zones:** 30 s at Z5–Z6, 30 s easy jog (not walk).
- **Structure:** 2 sets of 8–12, 3 min between sets.
- **Coaching notes:** the gentler entry to VO2 running and the time-crunch
  option; time at high VO2 accumulates across reps.
- **Select when:** first VO2 run of a block; a short slot.

#### RUN-H1: Hill repeats
```yaml
id: RUN-H1
domain: vo2max
is_hard_session: true
work_minutes: 12
est_total_minutes: 45
```
- **Zones:** hard uphill effort by HR/RPE (pace is meaningless on a slope),
  6–8 % grade.
- **Structure:** 6–10 × 60–90 s up, walk/jog down as recovery.
- **Coaching notes:** strength and VO2 with less impact than flat speed
  (the descent is slow). Good first hard run for a cyclist.
- **Select when:** Prep and Base; trail goals throughout.

#### RUN-H2: Hill sprints
```yaml
id: RUN-H2
domain: anaerobic
is_hard_session: false
work_minutes: 4
est_total_minutes: 35
```
- **Zones:** near-max 8–10 s on a steep hill, full recovery 2 min.
- **Structure:** 4–8 sprints inside an easy run.
- **Coaching notes:** neuromuscular power and tendon stiffness; so short it
  does not count as a hard session. Start with 4.
- **Select when:** Prep and Base, once a week, on an easy day.

### 1D. Test and race-week

#### RUN-TEST: Threshold test
```yaml
id: RUN-TEST
domain: threshold
is_hard_session: true
work_minutes: 30
est_total_minutes: 50
```
- **Structure:** after a full warm-up, 30 min run as hard as can be held
  evenly, flat route; threshold pace ≈ average pace of the last 20 min, LTHR
  ≈ average HR of the last 20 min. Or a recent 5–10 km race through
  Intervals.icu's own estimate.
- **Coaching notes:** the owner updates thresholds in Intervals.icu; the
  coach suggests, never sets them.
- **Select when:** no threshold pace set; every 8–12 weeks; after illness.

#### RUN-OPEN: Race-week opener
```yaml
id: RUN-OPEN
domain: race_specific
is_hard_session: false
work_minutes: 25
est_total_minutes: 25
```
- **Structure:** 15–20 min easy, 3–4 × 1 min at race pace, 4 strides.
- **Select when:** 1–2 days before a race (Section 11 Race-Week Protocol).

## 2. Warm-up and cool-down

- Before Z4+: 10–15 min easy, 4 strides, 2–3 min at the session's lower
  target. Never skip before VO2 or hills.
- Cool-down: 5–10 min easy jog or walk.

## 3. Selection

| Situation | Format |
|---|---|
| New or returning to running | RUN-E1 with walk breaks, 3× a week, nothing hard for 4–6 weeks |
| First threshold run of a block | RUN-T1 at 4 × 5 |
| First VO2 run, or a cyclist's first hard run | RUN-H1 or RUN-V2 |
| Established VO2 fitness | RUN-V1 |
| Half marathon / marathon Build | RUN-T2 and RUN-L2 alternating with RUN-L1 |
| Trail or mountain goal | RUN-TR, RUN-H1; descents built gradually |
| Readiness "Modify" | RUN-E1 or RUN-E2, or swap to an easy ride (less impact) |
| Niggle present | ride instead; no run until it is gone on waking |
| Time < 35 min | RUN-V2 or RUN-E2; never a compressed RUN-V1 |
| No threshold pace set | RUN-TEST before pace-based work; meanwhile HR/RPE |

Progression order as the bike library §5.2: reps or duration first,
recovery second, intensity last. On top of it the impact rules in §0 cap
how fast weekly run volume may rise, whatever the session criteria say.
