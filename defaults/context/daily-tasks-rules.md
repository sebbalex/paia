---
name: daily-tasks-rules
updated: 2026-08-19
owner: owner
---

# Daily task rules

Short-lived things I have to do. The list is in `memory/wiki/daily-tasks.md`,
which the assistant writes when I tell it to. This file is the standing brief.

**Not the same as home duties.** A daily task has an end: call the garage, send
the invoice, book the flight. Something that comes back on a cadence forever —
filters, bins, boiler — is a home duty and belongs in
`memory/wiki/home-duties.md`. If it is unclear, ask me once and file it.

## Entry fields

Each task is one `## <task>` line-item section in `memory/wiki/daily-tasks.md`:

- `due` — absolute date, or `today`, or empty for "no date, just don't lose it"
- `repeat` — cron-style words if it recurs (`every monday`, `monthly`).
  Empty = one-off.
- `state` — `active` | `done` | `paused` | `dropped`. Only `active` shows up.
- `blocked-by` — what has to happen first, if anything.
- `notes` — free text, including what I said when I asked for it.

Default when I don't say: `state: active`, everything else empty.

## How I want it handled

- When I say "remind me to X" or "I need to X", write the entry. Don't ask me
  to confirm, don't ask for a due date I didn't give.
- When I say it's done, set `state: done` and keep it — I want to see what got
  finished, not have it vanish. Clear out `done` items older than a month.
- Never invent a due date. Empty is a real answer.
- If I ask "what's on today", give me the `active` items due today or overdue,
  overdue first, one line each. Don't list the whole backlog.
- Don't nag. You surface tasks when I ask, or when a routine I enabled asks.
