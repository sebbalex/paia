# paia

Personal assistant process. Read these in order, they are your operating system:

1. @IDENTITY.md — who you are, how you speak
2. @ROT.md — operating rules, especially which memory tier to use

## Layout

```
.claude/skills/ methods you follow: one folder per skill  (you propose, the owner enables)
memory/wiki/   authoritative facts, one entity per file  (you write these)
memory/raw/    append-only conversation log, one file per day  (transport writes)
context/       the owner's briefs  (read-only to you: propose changes in memory/wiki/proposals/)
scripts/       executable tools you may run
skills/        vendored gws CLI docs  (reference only — not the skills above)
assistant/     your own runtime — Telegram transport + SDK loop
deploy/        systemd units
```

## Memory tools

claude-mem is active as an MCP server. It gives you semantic recall over every
past session: `search`, `smart_search`, `timeline`, `get_observations`.
It also injects relevant past context automatically at session start.

Read `ROT.md` for when to trust it. Short version: it finds the thread, the
wiki holds the truth.

## Current state

Bootstrapping. Telegram transport works. Google Calendar, Gmail and Drive read
through `assistant/gws.py` (fifteen in-process tools over the `gws` CLI:
eight reads, including Drive file contents and PDF text, three calendar
writes, and four Drive writes that all stay inside the folders
`DRIVE_WRITE_FOLDERS` names — `drive_create_doc` creates one new Doc in one of
them, naming it in `folder_id` when several are open, `drive_append_doc` adds
to the end of one already in one of them, `drive_replace_text` swaps one exact
string inside one, `drive_rewrite_doc` replaces the whole body of one, and
none of them can delete, rename, move or share a file).
Drive reads are confined to the
folder ids in `DRIVE_ALLOWED_FOLDERS` and everything beneath them. Intervals.icu reads through
`assistant/intervals.py` (eight `coach_*` reads, metrics in
`assistant/coach_metrics.py`) for the endurance coach (cycling, running), plus one write,
`coach_plan_workout`, which puts a planned workout on the Intervals calendar
(and so on Garmin Connect and the head unit) only after a preview in an earlier
turn, never from a routine, and rewrites only workouts it planned itself; routines with
`charts: coach-week` get six training charts drawn by
`assistant/coach_charts.py` attached to the message, then a power profile
for each of next week's calendar workouts. The weekly routine files its
report through `coach_report` (short fields), and `assistant/coach_report.py`
lays it out as a designed email with numbers, trend charts and a profile per
key session. Skills are live: `.claude/skills/` holds the
methods, `ASSISTANT_SKILLS` says which ones are switched on: `price-hunt` for
prices, `adventure-log` for the trip documents in Drive, `trip-design` for
turning a trip idea into one of those documents, `coach` for training.
Setup is chat-first: an install with no allowlist pairs its owner with a code
from the log (`assistant/pairing.py`), pairing starts the onboarding
interview (`assistant/onboarding.py`, `/onboard`), and brief changes are
proposals in `memory/wiki/proposals/` that the owner applies with
`/review` and `/accept` (`assistant/proposals.py`; add-on only, a VPS copies
by hand). Notes not built yet.
