---
name: shopping-rules
updated: 2026-08-21
owner: owner
---

# Shopping rules

How I want purchases watched. The list itself is not here — it lives in
`memory/wiki/shopping.md`, which the assistant writes when I tell it to add
something. This file is the standing brief: it changes rarely, and only I
change it.

## Standing preferences

- Ship to: **TO FILL** (country, and which sellers are acceptable —
  e.g. "EU sellers only").
- Always state whether a price includes shipping.
- Never buy, never add to a cart, never open a checkout page, never fill in a
  form. Reading only.
- **Three levels of proof, and the label travels with the number.**
  - `verified` — the product page was opened and read. Only a verified price
    sets a historical low or lets you say a price "dropped".
  - `corroborated` — the price came out of search results on the seller's own
    domain, for the right variant, in two or more independent searches. Good
    enough to be the working number and to raise a `target` alert, but the
    alert reads "check by hand", never "it dropped".
  - `clue` — anything else: a single snippet, a comparison site, a
    marketplace listing page. Reported with its link, never as a figure.
- Search before fetching. Most of the shops worth watching block a direct
  fetch or render their price in JavaScript; the search index has already
  read them. Spend fetches on the pages that answer.

## Entry fields

Each item is one `## <item>` section in `memory/wiki/shopping.md` with:

- `task` — `watch-price` | `compare` | `research` | `availability`
- `target` — price below which to alert me (EUR). Empty = just report.
- `sources` — URLs to check, one per line. Empty = the assistant picks them.
- `criteria` — constraints in prose (budget, size, technical requirements).
- `state` — `active` | `paused` | `bought`. Only `active` is checked.
- `notes` — free text.

Default when I don't say: `task: watch-price`, `state: active`, everything
else empty.

## What a task means

- `watch-price` — check the price, alert if it drops below `target`.
- `compare` — find the same product across several sellers, report the lowest.
- `research` — no model chosen yet: find candidates that meet `criteria`.
- `availability` — alert when it is back in stock.
