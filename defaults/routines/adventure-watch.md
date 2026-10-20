---
name: adventure-watch
schedule: 0 18 * * 4
enabled: false
channel: telegram
---

Check the pinned trip and tell me what needs doing before the weekend.

Use the `adventure-log` skill. It holds the method — how the document is
found and read, the two levels of proof, what is harvested, and the rule that
conditions are never recalled — and this prompt does not repeat it.

The list is `memory/wiki/adventures.md`. The owner's brief is
`context/adventures-rules.md`: their file, read it, never modify it.

Work only on the trip with `pin: yes`. There is at most one. If no trip is
pinned, send a single line saying so and stop — do not pick one yourself, do
not fall back to the rest of the list.

Work out today's date first, absolutely. Everything below is relative to it.

Re-read the trip's Drive document, then report, in this order:

- what falls due in the next three weeks: bookings, permits, transport,
  anything with a date attached that is still not done
- the open points in `state of play`, and whether the document has resolved
  any of them since `last-read`
- what my own past trips say that applies here — matched on type, terrain and
  season, with the trips and their dates
- conditions, and only if the trip starts within ten days: snow, avalanche
  bulletin, fords, glacier, refuges open, passes and roads. Each one either
  from a page opened today, quoted with its source and the date, or reported
  as unknown. Never from a past document, never estimated. Write this part in
  full sentences.

Then update the trip's section — `state of play` replaced, `last-read` set to
today — and add the log line. Do not move the pin: that is the owner's, from chat.

Nothing due and nothing changed is one line saying so.

Book nothing, write to nobody. Anything that needs sending, draft it and say
it is waiting for me.

No questions: this runs while I'm not there.
