---
name: weekly-summary
schedule: 0 8 * * 1
enabled: false
channel: telegram
---

Give me the summary of the week just closed: from last Monday to yesterday
(Sunday). Work out the exact dates from today.

Sources, in this order:

1. `memory/raw/YYYY-MM-DD.md` for each of the seven days — it is the
   authoritative log of what was actually said. Missing files mean a day
   without conversations, not an error: skip them.
2. claude-mem: `search` with dateStart/dateEnd over the same window, then
   `get_observations` only on the ids worth it. It serves to find threads the
   log doesn't make obvious, not as a source of truth.
3. `memory/wiki/` for the facts that changed or were born during the week.

Message structure:

- **Done**: what was finished, one line each.
- **Open**: what was left half-way, with the point it stopped at.
- **Decided**: decisions taken that still hold, with the why in half a line.

Compressed prose. No questions, no "do you want me to". If the week was empty,
say so in one line and stop.
