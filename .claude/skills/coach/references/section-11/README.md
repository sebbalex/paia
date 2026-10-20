# Section 11, vendored

Upstream: https://github.com/CrankAddict/section-11 (MIT, see `LICENSE.md`).
Pinned at commit `2a33de0444b113f63ad36a9b8f427eb315c71a28` (2026-10-19),
protocol version **11.77**. Copied unmodified; update by replacing the files
from a newer commit and bumping the line above.

This is **reference**, not the method. The method is `../../SKILL.md`, which
says where this assistant departs from the protocol. Where the two disagree,
SKILL.md wins.

Section 11 is written for its own data pipeline (`sync.py` producing
`latest.json`, `history.json`, `intervals.json`). This assistant does not run
that pipeline: its numbers come from the `coach_*` tools. Field names in these
files (`readiness_decision.signals`, `derived_metrics.acwr_start_of_day`,
`capability.durability`) do not exist here; the mapping is in SKILL.md.

`SECTION_11.md` is 430 KB. Never read it whole: `Grep` for the heading you
need (`^#+ `), then `Read` that range.
