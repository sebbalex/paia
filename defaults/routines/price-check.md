---
name: price-check
schedule: 30 8 * * *
enabled: false
channel: telegram
---

Check the shopping list and report what changed.

Use the `price-hunt` skill. It holds the method — verified against unverified,
how wide to search, the ceiling, blocked shops, what to write where — and this
prompt does not repeat it.

The list is `memory/wiki/shopping.md`: one section per item, the entry fields
at the top of the section, the readings under them. The field meanings and
the owner's standing preferences are in `context/shopping-rules.md` — their file, read
it, never modify it.

Take only the items with `state: active` and skip the rest. If there is no
active item, send a single line saying so and stop.

For each active item run its `task`:

- `watch-price` — read the current price at every URL in `sources`.
- `compare` — same product across several retailers (if `sources` is empty you
  pick them), report the lowest.
- `research` — no model chosen yet: at most three candidates meeting
  `criteria`, with price and link, and say which one you would take.
- `availability` — whether it is in stock.

Compare against the last reading in the item's section, if there is one.

No questions: this runs while I'm not there.
