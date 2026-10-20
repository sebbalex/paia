# Post-Workout Report Template

> This template defines the standard output format for post-workout reports.  
> Fields in `[brackets]` are placeholders. Omit fields that don't apply to the activity type.  
> **Data Freshness:** Take every number from a current read of the source that owns it: training metrics and planned sessions from the source JSON file, stable athlete facts (such as a tested bottle quantity) from the current official dossier, athlete-reported values from the athlete's explicit current statement, and external conditions such as weather from their current source (see SECTION_11.md §Output Format Guidelines). Never take a number from memory or an earlier chat. Do not carry forward values from earlier reports or earlier in the conversation. Re-read before quoting.  
> **Display Units:** For distance / elevation / weight / height / position / speed, quote `display.*` fields from the source JSON; they're pre-converted to the athlete's Intervals.icu preferences. Use canonical metric (`*_km`, `*_m`, `*_kg`) only for calculations. See SECTION_11.md §Display Unit Semantics.

---

```
Data (last_updated UTC: [YYYY-MM-DDTHH:MM:SS])

[One-line summary of completed session(s) and key observation.]

Outdoor context: [synthesis line per SECTION_11.md §"Completed-Activity Terrain & Weather": Outdoor context synthesis line. Omit line entirely for indoor activities, when both `terrain_summary` and `weather_summary` are absent, or when status fields indicate unavailable data]

Completed workout: [ActivityType] [WorkoutName]
Start time: [HH:MM:SS]
Duration: [XhYm] (planned [XhYm])
Distance: [display.distance.value] [display.distance.unit]
Ascent: [display.elevation.value] [display.elevation.unit]
Power (whole session): [XXX] W avg / [XXX] W NP
Executed structure: [method/status; supported step/set measurements with actual means and agreement, or descriptive thirds/WORK efforts; say unavailable when absent; omit for Walk/Hike]
IF: [X.XX] [omit line if null]
Power zones: [XX]% Zone 1, [XX]% Zone 2, [XX]% Zone 3, ... (list every zone with ≥1% after rounding; omit zones that round to 0%)
Grey Zone (Z3): [XX]%
Quality (Z4+): [XX]%
Session profile: [Classification]
HR: [XXX] avg / [XXX] max
HR zones: [XX]% Zone 1, [XX]% Zone 2, [XX]% Zone 3, ... (list every zone with ≥1% after rounding; omit zones that round to 0%)
Cadence: [XX] avg
Decoupling: [X.XX]%
EF: [X.XX]
HRRc: [XX] bpm [omit line if null]
Variability Index: [X.XX] ([assessment])
DFA a1: [X.XX] avg, [X]% easy/[X]% endurance/[X]% tempo/[X]% supra ([status-aware drift summary]) [omit line entirely if no dfa block on this activity; one-line quality notice with no readings if sufficient=false]
Calories: [XXXX] kcal
Carbs used: [XXX] g
TSS: [XXX] (planned [XXX])
Feel: [X/5] ([label])
RPE: [X/10]
Effort response: [positive/neutral/negative] [omit line if null]
Note: [description or chat_notes text]

[Repeat block for every completed activity whose date falls on the report day (athlete local time). One block per activity ID; never merge. Include walks, ski-erg, short rides, aborted rides, commutes. Never drop secondary sessions. Within a block, omit only fields the activity type does not have (e.g., no power for a walk).]

Weekly totals (rolling 7d):
Phase: [phase_detection.phase] Wk[phase_detection.phase_duration_weeks]
Polarization: Z1+Z2 [XX]%, Z3 [X]%, Z4+ [X]%: [Classification] (PI: [X.XX])
Durability: [X.XX]% 7d mean([X]) / [X.XX]% 28d mean([X]) ([trend])
EF: [X.XX] 7d mean([X]) / [X.XX] 28d mean([X]) ([trend])
HRRc: [XX] bpm 7d mean([X]) / [XX] bpm 28d mean([X]) ([trend]) [4 cases: (a) 28d ≥ 3 and 7d ≥ 1: full line as shown; (b) 28d ≥ 3 and 7d = 0: "[XX] bpm 28d mean([X]); 7d: no data"; (c) 28d < 3 and 7d ≥ 1: "[XX] bpm 7d mean([X]); 28d: insufficient data"; (d) 28d < 3 and 7d = 0: omit entirely]
TID 28d: [Classification] (PI: [X.XX]); drift: [consistent/shifting/acute_depolarization]
TSB: [X.XX]
CTL: [XX.XX]
ATL: [XX.XX]
Ramp rate: [X.XX]
ACWR: [X.XX] ([assessment]); live, includes today's completed load; retrospective context only
Recovery Index: [X.XX]
Hours: [XhYm]
TSS: [XXX]

Interpretation:
[2-4 sentences: compliance check, key quality metrics, load context, recovery note if applicable.]

Tomorrow: [WorkoutType] [Duration], [main set condensed, e.g., "3×12m @260W"]
[Use workout_summary as source. Condense to main set; omit a warm-up, cool-down or recovery step only where the plan labels it so, since a lower target alone does not make a step recovery. Square-bracketed text in workout_summary (`[step not summarized]`, `[repeat not summarized]`, `[target not shown]`) marks a step, repeat or target the summary could not render. A summary without a marker lists every step with its duration and its power target (or HR target when no power target is set); it does not carry secondary targets such as cadence or an HR target set alongside power, and where it compresses repeated steps it shows them at the first step's value when each is within 2 W and 2 s of that first step. If workout_summary is null or carries a marker, take the plan from whichever of `description` (entries up to 7 days ahead) or `description_preview` (later entries; its first 3 lines only, so say the plan may continue) the entry carries; if that field is absent or empty, report a marked summary as given and say it is incomplete, or for a null summary give the workout name and duration and say the structure is not in the data. Never invent targets or step roles.]
[If multiple planned workouts tomorrow, list ALL sessions joined with " + " in start-time order:
 "Tomorrow: Endurance 2h15m, 1h30m @173W + SkiErg 10m Z2 HR"]
[Never drop secondary sessions. Omit the Tomorrow line only if no planned sessions exist tomorrow.]
```

---

Whole-session average/NP cannot establish main-set power. Apply the structure rule in comparisons, interpretation and tomorrow's prescription as well as in the session block. Thirds, small mean deviations and a low VI do not by themselves prove steady work for DFA or decoupling interpretation.

## Rounding Convention

Round zone percentages to the nearest **whole number** (1%). The JSON data source carries precise values for detailed analysis. A few seconds in a zone is noise, not signal: report `0%` not `0.1%`.

## Field Notes

| Field | When to include | Notes |
|-------|----------------|-------|
| Outdoor context | Outdoor activities only; only when at least one of `terrain_summary` or `weather_summary` is present and not in a status-only state | Single-line synthesis of terrain + weather context plus, when earned, a causal clause attributing variability or environmental cost. Generation rules, descriptive bands, causal triggers, and graceful-degrade behavior in SECTION_11.md §"Completed-Activity Terrain & Weather": Outdoor context synthesis line |
| Distance | Cycling, running, walking | Use display.distance; omit for SkiErg, strength |
| Ascent | Walks, runs and rides, indoors and outdoors, when recorded elevation is present | Quote display.elevation.value and unit. Retain zero; omit when unavailable. Source-dependent and descriptive only: never compare barometric, GPS and virtual ascent, or replace recorded total with detector ascent |
| Executed structure | Completed activities except Walk/Hike | Read latest.json structure_summary method/status/issues. When has_structure is true, load intervals.json's session_structure for window bounds, HR, cadence, kJ, coverage and zero-power share; qualify any stale/cache or partial-data issue. For paired_plan_windows, quote actual mean power and measured duration beside target; follow SECTION_11's recording-time alignment and set contract. Render a repeat descriptor as reps × bout duration, with measured work-bout mean and time; never describe it as continuous work. A step with insufficient coverage has no metric numbers; retain usable neighbouring steps. Clipping is disclosed separately from original workout duration. Match is power agreement, not completion. For fixed_elapsed_thirds, label thirds and upstream WORK efforts descriptive; never call them prescribed steps. no_plan differs from unknown. When detail has expired, use the last captured summary under its method version and say detail is unavailable. Broad followed status is not formal adherence; roles require authoritative context |
| Power (whole session) / Power zones | Activities with power data | Omit if no power meter |
| IF | Activities with `intensity_factor` present | Intensity Factor (session-level, NP ÷ FTP). Rendered to 2 decimals (divide the stored percentage by 100). Omit line when null. Source for Effort Response band lookup |
| Grey Zone / Quality | Always for cycling | Highlights polarization compliance |
| Session profile | Activities with zone data | Per-session intensity classification based on **executed** zone distribution (e.g., Recovery, Endurance, Tempo, Sweetspot, Threshold, VO2max, Anaerobic, Neuromuscular, Mixed). When the planned workout name and the executed character disagree, classify by what was actually done (IF and zone-time distribution), not by the planned label. Sweetspot requires VI ≤ 1.05; sessions averaging IF 0.88–0.94 with VI > 1.05 classify by dominant power zone instead |
| Cadence | Cycling, running | Omit for SkiErg, strength |
| Decoupling | Activities with power + HR | Key aerobic efficiency marker. Display the value whenever present; interpret or label it only when the session passes the per-session gate: ≥ 90 min (5400 s) with `variability_index` ≤ 1.05 and > 0, checked against `duration_formatted`, not rounded hours (SECTION_11.md §Evidence handling). A short or surgy session still carries a decoupling number; it is not evidence. Per-session scale (<5% good) per Friel/Coggan. Aggregate durability uses tighter scale (<3% good) |
| EF | Activities with power + HR | Aerobic efficiency (NP ÷ HR). Display the value whenever present; interpret it only when the session passes the per-session gate: ≥ 20 min (1200 s) with `variability_index` ≤ 1.05 and > 0, checked against `duration_formatted`, not rounded hours (SECTION_11.md §Evidence handling). Track trend over like-for-like eligible sessions. Absolute value is individual-dependent |
| HRRc | Activities where HR exceeded threshold for >1min | Heart rate recovery (largest 60s HR drop in bpm). Higher = faster parasympathetic recovery. Absent on easy rides, rides stopped before cooldown, or no HR data. Omit line when null |
| Variability Index | Cycling with power | 1.00–1.05 = steady, >1.05 = variable. Assessment labels apply to steady-state only; omit label for interval sessions where high VI is expected |
| DFA a1 | Activities with a `dfa` block in `intervals.json` | **Source:** session-level `dfa.avg` and each `tiz_*` band's `pct`, or their compact-summary equivalents. These describe retained samples; use `quality.coverage_by_third` to explain missing-data limits. Per-interval `avg_dfa_a1` was removed in v11.52; stale values must not be used or averaged. **Branching:** (a) `dfa` block absent → omit the line entirely, do not announce absence; (b) `dfa` present with `quality.sufficient: false` → one-line notice reporting the available quality counts and percentage and each failed gate per **DFA a1 Protocol §Quality Gates**; if the supplied quality data cannot resolve the reason, say so; no DFA reading; (c) `sufficient: true` → full line, using the drift rules below. **Validated sports only:** the protocol's interpretive rules apply to cycling. For other sports, surface values descriptively without threshold conclusions |
| Carbs used | Sessions with power data | Omit if unavailable |
| Feel | Omit line if null | 1=Strong, 2=Good, 3=Normal, 4=Poor, 5=Weak. Set in Intervals.icu or pushed from device (e.g. Garmin post-ride prompt). Can appear on any activity type |
| RPE | Omit line if null | Rate of Perceived Exertion, 1–10 scale. Set in Intervals.icu or pushed from device. Can appear on any activity type |
| Effort response | Omit line if null | Session IF vs reported RPE against the RPE Expectation Bands (SECTION_11.md §Testing Protocol). `positive` = RPE below band (fitness/freshness tell), `neutral` = within band, `negative` = above band (fatigue/under-recovery tell). Null when IF absent, RPE absent or ≤ 0, or IF < 0.65 (out of band coverage: recovery rides and aborted sessions). Interpretive overlay: does NOT alter Feel/RPE Override rules (v11.14) and does NOT alter the readiness decision. A single `positive`/`negative` is an observation to surface; a repeated pattern across sessions deserves Interpretation-section commentary |
| Note | Omit line if neither present and no `chat_notes_status` marker | Athlete's own text or coach messages attached to the activity. If both `description` and `chat_notes` exist, combine. Omit line entirely when neither is present. If the activity carries `chat_notes_status: "unavailable"`, the chat notes fetch failed: do not report the activity as having no note. Either omit the line silently or, where the note would have mattered to the interpretation, state that chat notes could not be retrieved |
| Heat context | When `avg_temp` indicates Tier 1+ heat stress (delta above 14d thermal baseline, or absolute fallback) | Contextualize decoupling, power, and RPE against temperature in the Interpretation section. Do not flag heat-elevated decoupling as durability decline. See **Environmental Conditions Protocol** in SECTION_11.md |
| Durability (weekly) | Aggregate decoupling 7d/28d | Steady-state sessions only (VI ≤ 1.05, ≥ 90min). Trend direction matters more than absolute value |
| EF (weekly) | Aggregate EF 7d/28d | Steady-state cycling only (VI ≤ 1.05, ≥ 20min). Trend direction matters more than absolute value |
| TID 28d (weekly) | 28d Seiler classification + drift | Shows whether acute TID matches chronic pattern. Always include drift label |
| Polarization (weekly) | Weekly Seiler TID rendered in power-zone labels | Source: `seiler_tid_7d.z1_pct/z2_pct/z3_pct`. Render as `Z1+Z2` (Seiler Easy / below LT1), `Z3` (Seiler Grey Zone / LT1–LT2), `Z4+` (Seiler Hard / above LT2). Do not output raw `Z1/Z2/Z3` labels; they collide with the per-session Power zones line above |
| Weekly totals (rolling 7d) | Always | Rolling 7-day window (last 7 days including today). Not calendar week. |

### DFA drift wording

Read `drift_quality.status` and ordered `reasons`, not the boolean alone. For a sufficient session:

- `unavailable`: `(drift unavailable: [reasons])`; no numeric drift or zero substitute. Other sufficient-session readings may remain.
- `descriptive_only`: `(drift [+/−X.XX], descriptive_only: [reasons])`. Name all emitted reasons in order; always explain `short_session` as fewer than 90 recorded minutes. Retain the number without a physiological flag, even if it is below -0.2. Do not label a false boolean "structural".
- `eligible`: `(drift [+/−X.XX], eligible)`. This reports numerical eligibility, not a physiological conclusion. A flag additionally requires delta < -0.2 and steady easy/endurance cycling confirmed from session structure, with fueling/hydration, heat and fatigue cross-checks per **DFA a1 Protocol §Session Interpretation Rules**. Low supra time alone is insufficient; selective loss can still bias the result.
- Missing, contradictory or unsupported metadata: report that drift eligibility cannot be established; do not infer it from a legacy boolean. Request a current producer export where needed.

The 90-minute rule uses 5400 original DFA slots with verified 1 Hz timing, not rounded activity hours or retained samples. Whole-session insufficiency always takes precedence over these drift-specific branches and retains the v11.72 refusal wording.

## Assessment Labels

| Metric | Good | Watch | Flag |
|--------|------|-------|------|
| Decoupling (per-session, gated sessions only) | < 5% | 5–10% | > 10% |
| Variability Index | ≤ 1.05 | 1.05–1.10 | > 1.10 |
| ACWR (live retrospective) | 0.8–<1.3 | 1.3–<1.5 | ≥1.5 |
| Grey Zone (Z3) | < 5% (base) | 5–10% | > 10% (base phase) |
| Durability (7d mean) | < 3% (good) | 3–5% (moderate) | > 5% (declining) |
| EF trend | improving/stable | — | declining |
| DFA a1 (cycling, sufficient) | Internal response matches prescription (very-easy/recovery → predominantly `tiz_easy`; endurance/Z2 → a `tiz_easy`/`tiz_endurance` mix, `tiz_endurance` = working toward LT1; SS/tempo → `tiz_tempo`) | Mild mismatch; note in Interpretation | Significant mismatch OR eligible drift below -0.2 on confirmed steady easy/endurance cycling, with the context checks above |
| TID drift | consistent | shifting | acute_depolarization |

ACWR <0.8 is reduced-load context, not a Watch/Flag classification.

## Formatting Rule

- **Durations and sleep:** Always use `_formatted` fields from JSON (e.g., `sleep_formatted`, `duration_formatted`, `total_training_formatted`). Never convert decimal `_hours` fields to display format; the formatted values are pre-calculated from raw seconds and avoid rounding errors.
