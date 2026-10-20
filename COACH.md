# AI Cycling Coach — Implementation Spec

> Design spec for the evidence-based cycling coach (`coach` skill, `assistant/intervals.py`,
> `assistant/coach_metrics.py`). Implemented; the code cites its sections (`COACH.md 5.2`).
> The devices named below are the reference setup it was built and tested on
> (Garmin head unit, dual-sided power pedals, chest strap, a watch for HRV), not a requirement:
> any setup that lands rides and wellness in Intervals.icu works. Your own setup goes in
> `context/training-rules.md`.

---

## 0. TL;DR

- **Single source of truth:** Intervals.icu. Everything flows in there; the coach reads only from there.
- **Data in:** Garmin (Edge 850 + Favero Assioma Duo + Garmin HRM-Dual; Garmin watch soon) → Garmin Connect → Intervals.icu, plus **Garmin Intervals Bridge** (Home Assistant app) to restore stripped data and protect against Garmin API restrictions.
- **AI data access:** **no MCP server.** The `coach` skill bundles a small set of **pre-written scripts** that call the Intervals.icu REST API (read + write) and return compact, pre-computed JSON. The assistant only runs these scripts; it never builds API calls itself.
- **Analysis brain:** a custom `coach` skill built on the **Section 11** protocol (CrankAddict), with modernised rules (HRV-guided readiness, de-weighted ACWR, no forced 80/20, load hierarchy per data quality).
- **Planning:** Section 11 B rules + its workout library; workouts written to the Intervals.icu calendar (via the skill's `push_workout` script, with confirmation) → synced to Garmin Connect → Edge 850.
- **Outputs:** text reports (pre-ride, post-ride, weekly, block) from the assistant + **Home Assistant dashboard** (readiness, today's session, fitness/fatigue/form, goal progress). Intervals.icu remains the deep-analysis UI.

---

## 1. Athlete & equipment context

| Item | Detail |
|---|---|
| Athlete | Amateur road cyclist, 4–5 sessions/week |
| Bike head unit | Garmin Edge 850 |
| Power | Favero Assioma Duo (dual-sided: L/R balance available) |
| HR strap (bike) | Garmin HRM-Dual (ANT+ & BLE, records RR intervals) |
| Watch | Apple Watch today → **migrating to a Garmin watch soon** (full Garmin ecosystem) |
| Other activities | Hiking/trekking and gym: **watch only, wrist optical HR** |
| Platforms | Garmin Connect, Strava (social only), Apple Health (being phased out), Intervals.icu (aggregator — all historical activities already there, from both Garmin and Apple Watch) |
| Home automation | Home Assistant (already running; will host the bridge and the dashboard) |

---

## 2. Architecture

```
┌──────────────────────────────┐
│ Edge 850 + Assioma + HRM-Dual│──┐
│ Garmin watch (hike/gym/sleep)│  │
└──────────────────────────────┘  ▼
                         Garmin Connect ──(official sync)──► Intervals.icu ◄── Athlete inputs
                                │                                ▲   │         (Feel, RPE)
                                └──► Garmin Intervals Bridge ────┘   │
                                     (HA app: full FIT fields,       │
                                      wellness, ImReady4 advice)     │
                                                                     ▼
                                      Intervals.icu REST API
                                                                     ▲
                                       skills/coach/scripts/icu_client.py
                                   (auth, local cache, retry/429 backoff)
                                                                     ▲
                       get_readiness / get_load / get_activity / get_week / push_workout
                                                                     ▲
                                    AI Assistant + `coach` skill (Section 11-based)
                                         │                    │                 │
                                         ▼                    ▼                 ▼
                                   Text reports      push_workout.py      publish_ha.py
                                  (chat/notify)   (Intervals calendar →  (MQTT discovery →
                                                   Garmin → Edge)         HA dashboard)

Strava: social only, NOT part of the data pipeline.
```

---

## 3. Data ingestion

### 3.1 Sources and routing

| Source | Data | Route | Notes |
|---|---|---|---|
| Edge 850 + Assioma Duo + HRM-Dual | Rides: power (L/R), HR, cadence, RR intervals, DFA a1 (AlphaHRV dev field) | Garmin Connect → Intervals.icu | Primary training data. Record rides **only on the Edge**, never also on the watch (avoid duplicates). |
| Garmin watch | Hike / gym (optical HR), sleep, overnight HRV (rMSSD), resting HR | Garmin Connect → Intervals.icu | Wellness source after cutover. |
| Garmin Intervals Bridge | Fields Garmin strips from FIT files, extra wellness, ImReady4 TrainingAdvice | Own Garmin account → Intervals.icu API | Runs as a Home Assistant app. |
| Athlete | Feel, session-RPE (gym), notes | Intervals.icu wellness / activity fields | Coach must prompt for RPE after gym. |
| Strava | — | — | Excluded: Strava API strips detailed fields for third parties. Devices must connect **directly** to Intervals.icu. |

### 3.2 Garmin Intervals Bridge (critical)

- Repo: https://github.com/futureweb/garmin-intervals-bridge
- Why:
  1. Restores metrics Garmin strips from FIT files sent to Intervals.icu.
  2. **Risk mitigation:** Garmin changed its partner terms (Oct 2026); the Intervals.icu author expects to have to block Garmin-sourced data in the Intervals.icu API. That would make Garmin activities invisible to the coach's API scripts. The bridge reads from the athlete's own Garmin account and has an **"upload mode"** ready for when the block lands.
  3. Optional **ImReady4** HRV-guided daily advice (Rest / LIT / Planned / HIT), computed from Intervals HRV + resting HR with 30-day z-scores.
- Setup:
  - Install as Home Assistant app.
  - Start in **enrich mode** with the official Garmin→Intervals sync ON.
  - Keep polling at the default (5 min) or adaptive; do not poll every minute.
  - Enable ImReady4: `BRIDGE_IMREADY4_ENABLED=true`. Requires a `TrainingAdvice` wellness field in Intervals (select: 1 Rest, 2 LIT, 3 Planned, 4 HIT — create it per ImReady4 guide; the bridge never creates it) and HRV + RHR on ≥14 of the last 30 days.
  - Monitor the forum thread; switch to **upload mode** if/when Intervals blocks Garmin-sourced API data.

### 3.3 Apple Watch → Garmin cutover

- Define `HRV_CUTOVER_DATE` (date the Garmin watch becomes the overnight wearable). Add a note on that date in the Intervals.icu calendar.
- HRV values from different devices/metrics (Apple SDNN vs Garmin rMSSD) are **not comparable**. All HRV/RHR baselines must be computed **only from data on/after the cutover**.
- Intervals.icu stores one wellness value per day: only **one** device may write HRV/sleep/RHR. Disable Apple Health → Intervals wellness sync at cutover.
- Garmin HRV Status needs ~3 weeks of consistent overnight wear to establish a baseline; ImReady4 needs 14/30 days. During this **baseline-building phase** the coach must down-weight HRV and rely on Feel + load metrics.
- Historical activities: power-based data remains fully valid (device-independent). HR-derived load from Apple Watch wrist HR may differ slightly from Garmin → prefer power-based TSS for cycling history.

### 3.4 Sensor configuration (data quality)

- **Assioma:** zero-offset calibration before every ride (log a reminder in the pre-ride report).
- **DFA a1 (AlphaHRV Connect IQ field, available on Edge 850):**
  - Edge ↔ HRM-Dual over **BLE**; AlphaHRV field ↔ same strap over **ANT+**.
  - Enable "Record HRV" on the Edge (RR stream in FIT).
  - Lock AlphaHRV to the strap's device ID (avoid pairing to other riders' straps).
  - If artefact rate is high, consider a Polar H10.
- **Intervals.icu sport settings:** separate thresholds per sport — cycling (FTP, power zones, LTHR, max HR) vs hiking (own LTHR / max HR, HR zones). Do not let hiking inherit cycling HR settings.
- Wet the strap before rides; wear it a few minutes before starting the recording.

---

## 4. AI data access — skill-bundled API scripts (no MCP)

### 4.1 Decision

We do **not** run an MCP server. The `coach` skill ships its own scripts that call the Intervals.icu REST API directly.

Rationale:
- **Fewer moving parts:** no extra container, TLS proxy or OAuth to operate.
- **Read and write in one place:** the MCP option considered (futureweb) is read-only; calendar writes would have needed a separate module anyway.
- **Determinism:** scripts return **pre-computed, aggregated** values (baselines, per-sport load, TID, decoupling). The LLM reasons over ready numbers and never does "virtual math" — same principle as Section 11 (`sync.py` / `push.py`).
- **Context efficiency:** compact JSON summaries instead of raw activity streams → lower token cost and latency for daily reports.
- **Versioned and testable** in the assistant's repo, alongside the coaching rules.

Trade-offs accepted:
- Not reusable as-is by other MCP clients (Claude Desktop, etc.). Acceptable: the coach lives only in this assistant. Wrapping the same client in an MCP later remains possible.
- We maintain the API client ourselves.
- The assistant runtime must be able to execute scripts in a controlled way.

Unchanged by this decision: the **Garmin Intervals Bridge is still required** (a block of Garmin-sourced data would affect the Intervals API itself, whatever the client).

### 4.2 Skill layout

```
skills/coach/
├── SKILL.md                # coaching rules (§5), which script to run when, report templates index
├── references/
│   ├── SECTION_11.md       # vendored at a pinned version
│   ├── thresholds.md       # §5.4 values (tunable)
│   ├── workout-library/    # Section 11 templates (§6.1)
│   └── reports/            # pre-ride / post-ride / weekly / block templates (§7.1)
├── scripts/
│   ├── icu_client.py       # ONLY module that talks to the API: auth, cache, rate limiting, retries
│   ├── get_readiness.py    # today's wellness + HRV/RHR baselines + ImReady4 advice → JSON
│   ├── get_load.py         # CTL/ATL/TSB, ramp, cycling vs total load, per-sport monotony, ACWR (info only)
│   ├── get_activity.py     # one activity: zones, grey-zone/quality %, NP, VI, EF, decoupling, L/R balance, DFA a1 summary
│   ├── get_week.py         # planned vs executed, adherence %, TID class, durability 7d/28d, flags
│   ├── get_history.py      # longer trends: FTP/eFTP timeline, EF trend, aerobic threshold (DFA a1 ramps)
│   ├── push_workout.py     # create/move/delete calendar events — requires --confirm
│   └── publish_ha.py       # publish coach entities to Home Assistant (§7.2)
└── tests/
    └── fixtures/           # recorded API responses (anonymised) for offline tests
```

### 4.3 API client (`icu_client.py`)

- Base URL: `https://intervals.icu/api/v1`
- Auth: HTTP Basic, username `API_KEY`, password = the athlete's API key (Intervals → Settings → Developer Settings). Athlete ID from the same page. *(Verify against current API docs.)*
- Secrets: `INTERVALS_API_KEY`, `INTERVALS_ATHLETE_ID` from environment / secret store. **Never** in prompts, SKILL.md, logs or memory.
- Endpoints to cover (verify names/params against the API docs / OpenAPI spec):
  - Wellness (daily): HRV, resting HR, sleep, feel, custom fields (`TrainingAdvice`), CTL/ATL.
  - Activities list (date range) and activity detail; intervals; streams (on demand only).
  - Calendar events: list, create, update, delete (planned workouts, race tags `RACE_A/B/C`, notes).
  - Athlete sport settings (FTP, zones, LTHR, max HR per sport).
- **Rate limiting & politeness** (the Intervals.icu maintainer has asked self-hosted tools not to poll aggressively):
  - No polling loops. Calls are on demand (report requested) or scheduled at most a few times a day.
  - Local cache (SQLite or JSON files) keyed by endpoint + params; activities are immutable after processing → cache indefinitely; wellness for today → short TTL.
  - On HTTP 429: honour `Retry-After` (min. 15 min), then fail gracefully with a "data stale" flag.
  - Custom `User-Agent` identifying the tool.
- Every script output includes `data_timestamp`, `data_age_hours`, `source: "intervals_api" | "cache"`.

### 4.4 Script contract

- CLI with fixed arguments (e.g. `get_activity.py --id <id>`, `get_week.py --week 2026-W43`).
- Output: **one compact JSON object** on stdout; errors as JSON with `error` + `hint`.
- All metric computations happen in scripts (pure functions, unit-tested), never in the LLM.
- Read scripts are side-effect free (except cache).
- `push_workout.py`:
  - Default is **dry run**: prints a preview of the event(s) it would create/change.
  - Writes only with `--confirm`, which the assistant passes only after explicit athlete approval in chat.
  - Workout description in Intervals.icu native workout syntax; after creation, verify Intervals parsed it into structured steps (only parsed workouts sync to Garmin).
  - Never deletes past/completed activities; deletes only future planned events it created (tag them, e.g. `source: coach`).

### 4.5 Rules for the assistant (to put in SKILL.md)

1. Use **only** the bundled scripts to access Intervals.icu. Never construct URLs or raw HTTP calls.
2. Never print, store or ask for the API key.
3. Read `data_age_hours` before reasoning; if stale or missing, say so and lower confidence.
4. Any calendar change: show the dry-run preview, ask for approval, then rerun with `--confirm`.
5. Use script values as-is; if a needed metric is missing, ask or state the gap — do not estimate it.

---

## 5. Analysis — the `coach` skill

### 5.1 Basis

Build a custom skill (`skills/coach/SKILL.md` + `references/`) on top of **Section 11** (https://github.com/CrankAddict/section-11, MIT):
- Protocol file `SECTION_11.md`: 11 A (coach behaviour), 11 B (plan generation), 11 C (validation checklist + metadata).
- Core principles to keep: deterministic, auditable (every recommendation cites data + framework), no "virtual math" on pre-computed metrics, ask when data is missing.
- Framework set: Seiler TID, Banister impulse-response (CTL/ATL/TSB), Foster monotony & strain, Coggan power-duration, Skiba critical power / W′bal, Issurin block periodisation, Mujika/Bosquet tapering.
- Do **not** copy the protocol blindly — pin a version, vendor it into `references/`, and apply the modifications below.

### 5.2 Modifications ("modern" layer) — mandatory

1. **HRV-guided readiness (primary readiness signal)**
   - Source A (preferred): ImReady4 `TrainingAdvice` field written by the bridge.
   - Source B (fallback / cross-check): 7-day rolling mean of ln(rMSSD) vs a 60-day baseline; "normal" = baseline mean ± 0.5 SD (smallest worthwhile change approach, as used in HRV-guided training studies on cyclists). Same logic for RHR (inverse).
   - Always **triangulate** HRV with Feel/subjective wellness and load context. HRV is sensitive but non-specific.
   - Before baseline is established (§3.3): flag `confidence: low` and decide from Feel + load.
2. **ACWR de-weighted**: report it as a load-progression indicator only; never use it as an injury-risk decision trigger (methodological criticism, Impellizzeri et al. 2020).
3. **No forced 80/20**: monitor TID (polarised / pyramidal / threshold), grey-zone % and quality % — but recent meta-analyses show no clear superiority of polarised over pyramidal for amateurs. Flag drift, don't impose a model.
4. **Durability first** for long rides: track decoupling / aggregate durability (7d vs 28d); it is the key limiter for a 4–5×/week amateur.
5. **DFA a1 as a trend**, not an absolute: do not apply fixed 0.75 / 0.5 thresholds during rides. Use periodic standardised ramp tests to track the HR/power at the aerobic threshold over time. Discard data with high artefact rates.

### 5.3 Load hierarchy by data quality

| Activity | Load metric | Reliability | Counts toward |
|---|---|---|---|
| Ride (Edge + Assioma + strap) | Power TSS | High | Cycling fitness (cycling CTL), fatigue, zones, progression |
| Ride (DFA a1) | DFA a1 trend | Medium (signal-dependent) | Aerobic threshold tracking |
| Hike (watch) | hrTSS / TRIMP (optical HR) | Medium | Total fatigue only |
| Gym (watch) | **session-RPE** (CR-10 RPE × minutes, Foster) | Medium, validated | Total fatigue only — optical HR underestimates strength load |
| Overnight (watch) | rMSSD, RHR, sleep | High as a trend | Readiness |

Rules:
- Keep **cycling-specific CTL** separate from **total load**. Hikes/gym add fatigue, not cycling fitness.
- After every gym session the coach asks for RPE if missing.
- Monotony computed per sport (avoid cross-training inflating it).

### 5.4 Thresholds (starting values — tune per athlete)

Readiness (from Section 11, adapted):

| Trigger | Response |
|---|---|
| ImReady4 = Rest, or HRV 7d below normal band **and** Feel poor | Easy day / rest |
| RHR ≥ +5 bpm vs baseline | Flag fatigue/illness, check symptoms |
| Feel ≥ 4/5 (bad) | Reduce volume 30–40% |
| TSB −10 to −30 | Normal in build — do **not** recommend recovery on TSB alone |

Progression green-lights (all should be true):
- Durability index ≥ 0.97 over 3+ long rides
- Aerobic HR drift / decoupling < 3%
- Readiness normal (7-day)
- Feel ≤ 3/5
- Weekly volume increase within ±10% of baseline

### 5.5 Validation output

Every coach answer that makes a recommendation includes (hidden or collapsed) metadata, à la Section 11 C:

```json
{
  "data_timestamp": "...",
  "data_age_hours": 2.1,
  "protocol_version": "coach-1.0 (based on Section 11 vX.Y)",
  "readiness_source": "imready4 | hrv_swc | feel_only",
  "hrv_baseline_status": "building | established",
  "frameworks_cited": ["Banister", "Seiler TID"],
  "missing_inputs": [],
  "confidence": "high | medium | low"
}
```

---

## 6. Planning toward a goal

### 6.1 Components

| Component | Source | Role |
|---|---|---|
| Plan rules | Section 11 B | Phase alignment, ±10% volume ceiling, intensity distribution control, session composition |
| Workout library | Section 11 `examples/workout-library/` (26 templates) | Coach must **select** from templates, not invent sessions |
| Race-week protocol | Section 11 | Events tagged `RACE_A/B/C` in Intervals → day-by-day taper (D-7 → D-0); TSB projection to race day |
| Phase detection | Section 11 / sync metrics | Base / Build / Peak / Taper / Recovery / Overreached |
| UX commands | Inspired by jsattler/claude-plugins `intervals-cycling-coach` | `/onboarding`, `/coach`, `/analyze-ride`, `/weekly-review`, `/plan-week`, `/plan-workout` |
| Long-horizon draft (optional) | felixrieseberg/claude-coach | Only as a starting point for a macrocycle toward a specific event |

### 6.2 Loop

1. **Goal set:** athlete defines target event + date (tag in Intervals as RACE_A/B/C) or "maintenance/general improvement" mode.
2. **Macrocycle:** coach drafts phases back from the goal date; athlete approves.
3. **Weekly (`/plan-week`, Sunday):** pick sessions from the library based on phase, load, readiness trend, calendar constraints (4–5 sessions/week). Write to calendar.
4. **Daily (morning):** pre-ride report → Go / Modify / Skip. Modifications rewrite today's calendar entry.
5. **Post-ride:** analysis + compliance (planned vs executed).
6. **Weekly review (`/weekly-review`):** adherence, TID, durability trend, flags → recalibrate next week.

### 6.3 Onboarding (`/onboarding`) collects

Age, weight, years riding, current FTP / power zones, LTHR / max HR per sport, typical weekly availability, goal(s), constraints (family, work, weather), injuries. Store as athlete dossier (Section 11 `DOSSIER_TEMPLATE.md` as base).

### 6.4 Writing workouts to the Edge

- Done by `push_workout.py` (§4.4); Section 11 `examples/agentic/push.py` can be used as a reference implementation.
- Workouts must use Intervals.icu native workout syntax so Intervals parses them into structured steps; only parsed workouts sync to Garmin.
- Enable Intervals.icu → Garmin Connect calendar sync → workouts appear on the Edge 850 with power targets.
- Writes require explicit confirmation from the athlete (dry run → approval → `--confirm`). No silent calendar edits.

---

## 7. Outputs

### 7.1 Text reports (assistant)

Base on Section 11 `examples/reports/` templates.

**Pre-ride (daily, morning)**
- Readiness: ImReady4 advice, HRV vs band, RHR, sleep — with baseline status
- Load context: CTL / ATL / TSB, monotony if > 2.3
- Capability: durability 7d + trend, TID drift
- Today's planned session
- Decision: **Go / Modify / Skip** + 1–2 line rationale
- Reminder: Assioma zero-offset

**Post-ride**
- Per activity: type, duration (actual vs planned), distance, avg/NP power, power zones %, grey-zone %, quality (Z4+) %, HR avg/max, HR zones, cadence, **L/R balance**, decoupling, EF, VI, kJ / carbs, TSS actual vs planned
- DFA a1 summary if present
- Coach note (2–4 sentences)

**Weekly**
- Totals: hours, TSS (cycling vs total), CTL/ATL/TSB, ramp rate
- TID classification and grey-zone %, durability 7d/28d, adherence %
- Flags and next week's plan

**Block / monthly**
- FTP / eFTP timeline, power-duration curve changes, EF trend, aerobic threshold trend (DFA a1 ramps), phase transitions

Delivery: chat, plus optional push notification (via HA) for the morning report.

### 7.2 Home Assistant dashboard (confirmed)

**Entities to publish** via `scripts/publish_ha.py` (MQTT discovery preferred, so entities survive HA restarts; HA REST API `POST /api/states/<entity_id>` as fallback). The same run that computes readiness publishes it — one pipeline, no duplicate logic:

| Entity | Type | Content |
|---|---|---|
| `sensor.coach_readiness` | enum | `rest` / `easy` / `planned` / `hard` (+ attrs: source, confidence, baseline_status) |
| `sensor.coach_decision_today` | enum | `go` / `modify` / `skip` (+ attr: rationale) |
| `sensor.coach_today_session` | text | Session name (+ attrs: duration, TSS planned, main set) |
| `sensor.coach_ctl` / `_atl` / `_tsb` | number | Fitness / fatigue / form |
| `sensor.coach_hrv_7d` | number | 7-day ln rMSSD (+ attrs: band low/high) |
| `sensor.coach_rhr` | number | Resting HR vs baseline |
| `sensor.coach_week_progress` | number | % of planned weekly TSS/hours done |
| `sensor.coach_goal` | text | Goal event, days to go, current phase |
| `sensor.coach_flags` | text | Active alerts (e.g. "durability declining", "grey-zone drift") |
| `sensor.coach_last_report` | text | Short summary (+ attr: full markdown) |

**Dashboard layout (Lovelace)**
- Header: readiness chip + Go/Modify/Skip, today's session card
- Form: CTL/ATL/TSB history graph (statistics graph)
- Recovery: HRV 7d with band, RHR trend, sleep
- Week: progress gauge, sessions done vs planned
- Goal: event, countdown, phase
- Flags: conditional card shown only when alerts exist
- Optional: render a compact version on the Waveshare wall panels

**Automations**
- Morning: when bridge writes today's wellness → trigger assistant pre-ride report → update sensors → notify phone.
- Evening/after ride sync: post-ride report → update sensors.
- Sunday evening: weekly review + plan-week proposal → notification with approve action.

Note: HA should **not** call the Intervals.icu API itself (no REST sensors polling it). All API access goes through `icu_client.py` (cache + rate limiting); HA only receives published entities.

---

## 8. Implementation plan (suggested order)

1. **Data layer**
   - [ ] Install Garmin Intervals Bridge as HA app (enrich mode); verify activities + wellness land in Intervals.
   - [ ] Create `TrainingAdvice` wellness field; enable ImReady4.
   - [ ] Configure per-sport thresholds in Intervals.icu.
   - [ ] Configure AlphaHRV + HRV recording on Edge 850.
   - [ ] Define `HRV_CUTOVER_DATE` when the Garmin watch arrives; disable Apple wellness sync.
2. **Access layer (skill scripts)**
   - [ ] `icu_client.py`: auth from env, cache, 429 handling, User-Agent; record fixtures from real responses.
   - [ ] `get_readiness`, `get_load`, `get_activity`, `get_week`, `get_history` with unit-tested metric functions.
   - [ ] Smoke test: last ride, today's wellness, this week's calendar.
3. **Coach skill v1 (analysis only)**
   - [ ] Vendor Section 11 at a pinned version into `skills/coach/references/`.
   - [ ] Write `SKILL.md` with §4.5 access rules, §5 coaching rules, load hierarchy, thresholds, validation metadata.
   - [ ] Implement pre-ride and post-ride reports; test against recent real data.
4. **HA publishing**
   - [ ] `publish_ha.py` (MQTT discovery) for §7.2 entities.
   - [ ] Lovelace dashboard + morning automation.
5. **Planning**
   - [ ] Onboarding + dossier.
   - [ ] `push_workout.py` with dry run / `--confirm`; verify parsing and sync to Edge 850.
   - [ ] `/plan-week`, `/weekly-review`, race-week protocol.
6. **Hardening**
   - [ ] Eval set: 10–20 historical days with expected decisions; regression-test the skill on rule changes.
   - [ ] Switch bridge to upload mode if Intervals blocks Garmin API data.

---

## 9. Open questions / to verify during implementation

- Exact Intervals.icu API fields for wellness (`hrv`, `restingHR`, `ctl`, `atl`, custom `TrainingAdvice`) and activity RPE/session-RPE load handling.
- Whether Intervals.icu computes load from RPE natively for gym sessions or the coach must compute session-RPE itself.
- Intervals.icu API: auth scheme, endpoint names/params, rate limits, and how DFA a1 (AlphaHRV developer fields) is exposed per activity (activity fields vs streams).
- Whether the assistant runtime's script execution is sandboxed enough to hold the API key safely (env/secret store).
- Garmin watch model choice: must support overnight HRV Status and Connect IQ.
- Goal for the season (event-driven vs maintenance mode) — to be set in onboarding.
- Section 11 limitation reported by users: interval-level history kept only ~7 days in its JSON mirror → our scripts query Intervals directly (it keeps everything) and cache locally.

---

## 10. Disclaimers to embed in the skill

- The coach is an analytical companion, not a medical device or a substitute for a qualified coach or physician.
- HRV/readiness never overrides symptoms of illness or injury; chest pain, dizziness, unusual breathlessness → stop and seek medical advice.

---

## 11. References

- Section 11 protocol — https://github.com/CrankAddict/section-11
- Section 11 forum thread — https://forum.intervals.icu/t/section-11-open-protocol-for-ai-endurance-coaching-chatgpt-claude-grok-mistral/120602
- Intervals.icu API — Settings → Developer Settings (API key, docs link); forum "API access to Intervals.icu" thread
- futureweb Intervals MCP (considered, not used; useful reference for endpoints) — https://github.com/futureweb/intervals-mcp-server
- Garmin Intervals Bridge — https://github.com/futureweb/garmin-intervals-bridge
- Bridge forum thread (Garmin terms, ImReady4) — https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863
- jsattler intervals-cycling-coach plugin — https://skillselion.com/skills/jsattler/claude-plugins/intervals-cycling-coach
- Claude Coach (Rieseberg) — https://github.com/felixrieseberg/claude-coach
- AlphaHRV / DFA a1 discussion — https://forum.intervals.icu/t/alphahrv-update/27239
- HRV metric differences across wearables — https://www.tryterra.co/research/how-hrv-actually-works
- Scientific background: Seiler (TID), Banister (impulse-response), Foster (monotony, session-RPE), Coggan (power-duration), Skiba (CP/W′), Javaloyes et al. 2019 (HRV-guided training in cyclists), Impellizzeri et al. 2020 (ACWR critique), Mujika & Bosquet (tapering).

