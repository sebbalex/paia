---
name: home-duties-rules
updated: 2026-08-19
owner: owner
---

# Home duty rules

Household upkeep that comes back on a cadence. The list is in
`memory/wiki/home-duties.md`, which the assistant writes when I tell it to.
This file is the standing brief.

**Not the same as a daily task.** A home duty never finishes — it comes due
again: water filter every 6 months, boiler service every year, bins every
Tuesday. A one-off errand is a daily task and belongs in
`memory/wiki/daily-tasks.md`.

## Entry fields

Each duty is one `## <duty>` section in `memory/wiki/home-duties.md`:

- `every` — the cadence in plain words: `week`, `2 weeks`, `6 months`, `year`
- `last-done` — absolute date of the last time it was done. Empty = never
  recorded.
- `next-due` — absolute date, computed from `last-done` + `every`. The
  assistant keeps this current; it is derived, not something I dictate.
- `who` — me, or someone else (plumber, landlord, cleaner). Empty = me.
- `state` — `active` | `paused`. Only `active` counts.
- `notes` — model numbers, part codes, who to call, what it cost last time.
  This is the part worth having: it saves me re-deriving it every year.

Default when I don't say: `state: active`, `who` empty, `last-done` = today if
I said I just did it, otherwise empty.

## How I want it handled

- When I say "I changed the filter today", set `last-done` to today's date and
  recompute `next-due`. One line back confirming both dates.
- **Recomputing a date is arithmetic, not judgement.** If it gets tedious,
  propose a script in `scripts/proposals/` per rule 4 rather than redoing it
  by hand every time.
- If `last-done` is empty, say so instead of guessing a `next-due`. An unknown
  date is information.
- Keep part numbers and supplier details in `notes` the moment I mention them.
  That is the whole reason this list is worth keeping.
- Don't nag. Surface what's due when I ask, or when a routine I enabled asks.
