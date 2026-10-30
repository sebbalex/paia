# Onboarding

From a fresh clone to an assistant that knows who you are. Four parts, in
order: collect the credentials, run the wizard, have the first conversation,
switch the routines on. About an hour, most of it in part 1.

What makes an instance yours is four things, and each has one way in:

| What | Holds | Written by |
|---|---|---|
| `.env` (or the add-on options) | tokens, skills, Drive folders | `scripts/onboard.py`, then you |
| `context/` | your standing briefs: how you want things done | `scripts/onboard.py`, then you |
| `memory/wiki/` | the lists and what the assistant learned | the assistant, when you tell it things |
| `routines/` | scheduled jobs, all disabled | defaults, then the assistant; you `/enable` |

The split matters: the assistant cannot write `context/` or `.env` and cannot
read `.env`. The wizard does the parts it can't; the first conversation does
the parts only it should.

## 1. Before you start

Collect these first. Only the first three are required; everything else
switches a feature on and can be added later by re-running the wizard.

**Required**

- **Telegram bot token** — talk to @BotFather, `/newbot`. The wizard checks it
  and finds your user id for you; @userinfobot does the same by hand.
- **Claude credential, one of two.** A subscription token from
  `claude setup-token` (`sk-ant-oat…`, billed to your plan), or an API key from
  console.anthropic.com (`sk-ant-api…`, metered). Never both: a key silently
  shadows the token.
- **Your timezone**, as an IANA name (`Europe/Rome`).

**Google: calendar, mail, Drive**

- The `gws` CLI installed and logged in **as the user the assistant runs as**:
  `gws auth setup`, then `gws auth login`. With it logged in, the wizard lists
  your calendars and asks which to surface.
- The Drive folders it may read. Open each folder in Drive; the id is the end
  of the URL (`…/folders/1AbCdEf_GhIjK`). The wizard takes the URL as is. Name
  the top of each tree — everything beneath is included. **No folders means
  the whole Drive**, which works but is not what you want.
- The folder for trip documents, if you use `adventure-log` / `trip-design`.
- Writing Docs there needs the Docs API (`docs.googleapis.com`) enabled in the
  same Cloud project `gws` uses.
- The one address routines may mail to.

**Cycling coach** (`coach` skill)

- Intervals.icu > Settings > Developer Settings: API key and athlete id.
  Connect Garmin to Intervals directly — Strava-synced activities are hidden
  from the API.
- The day a new watch took over overnight HRV, if that ever happened.
- Your goal event and date, your typical week, your devices.

**Price scraper** (optional, makes prices `verified`)

- Its URL: `http://127.0.0.1:8099` with `deploy/paia-scraper.service`, or
  `http://addon_sebbalex_paia-scraper:8099` with the scraper add-on. The
  wizard generates the shared token; give the worker the same one.

## 2. Run the wizard

VPS or local checkout, from the repo root:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 scripts/onboard.py
```

Home Assistant add-on: skip the wizard. Fill in three options — bot token,
`llm_provider`, `llm_credential` — start it, and send the bot the
`PAIRING CODE` from the Log tab. Pairing starts part 3 by itself, and your
answers come back as brief proposals you `/review` and `/accept`. Details
in `addon/DOCS.md`.

It asks, in this order. Enter keeps the bracketed default or skips; a skipped
brief line stays `TO FILL`, which the assistant treats as unknown and asks
about rather than guessing.

| Section | Questions | Lands in |
|---|---|---|
| You | first name, answer language, timezone | `OWNER_NAME`, `OWNER_LANGUAGE`, `context/calendar-rules.md` |
| Model | Claude subscription, Claude API key or OpenRouter, the credential, model | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`, `ASSISTANT_MODEL` |
| Telegram | bot token, who may talk to it | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` |
| Skills | each method in `.claude/skills/`, on or off | `ASSISTANT_SKILLS` |
| Google | `gws` path, each calendar: show / background / ignore, mail address, trip folder and whether it is writable, other readable folders | `GWS_BIN`, `context/calendar-rules.md`, `memory/wiki/calendar.md`, `MAIL_TO`, `context/adventures-rules.md`, `DRIVE_ALLOWED_FOLDERS`, `DRIVE_WRITE_FOLDERS` |
| Shopping | ship-to country and acceptable sellers | `context/shopping-rules.md` |
| Coach | Intervals key and athlete id, HRV cutover; sports and sessions, main sport, age/weight/years, injuries, typical week, strength work, constraints, goal event, its sport, distance and target, devices, pre-session reminder | `INTERVALS_*`, `HRV_CUTOVER_DATE`, `context/training-rules.md` |
| Scraper | URL | `SCRAPER_URL`, `SCRAPER_TOKEN` (generated) |
| Voice | Whisper model, forced language | `WHISPER_MODEL`, `WHISPER_LANGUAGE` |

Then it shows a summary with secrets masked and writes nothing until you say
yes. On yes it:

- copies `defaults/` into place, never over a file that exists;
- writes `.env` at mode 600, keeping every comment from `.env.example`, after
  backing the old one up as `.env.bak-<timestamp>`;
- fills the brief lines you answered and bumps their `updated:`;
- offers to start `.instance.git`, the private history of your briefs and wiki
  (see "Your instance" in `README.md`).

It is safe to run again: the old `.env` supplies the defaults, and brief lines
already filled are not asked twice. Write your answers to the brief questions
in English — the briefs are infrastructure (ROT rule 7); the assistant still
answers you in your language.

The only network call it makes is to `api.telegram.org`, with your bot's own
token, and only after asking.

**Read the briefs once anyway.** The wizard fills the blanks; the rest of each
file in `context/` is a default opinion — what counts as a verified price,
how the calendar is read, what a home duty is. They are yours: change what
does not fit you.

Start the assistant (`systemctl enable --now paia`, `.venv/bin/python -m
assistant.main`, or start the add-on) and send it `/help`.

## 3. The first conversation

The lists in `memory/wiki/` are the assistant's to write, and the way in is
talking to it — never a file you edit. Send `/onboard` (on the add-on it
starts by itself right after pairing): the assistant interviews you one topic
at a time, in your language, files the lists as it goes, and turns answers
that belong in a brief into proposals — `/review <brief>` shows the diff,
`/accept <brief>` applies it. `/onboard training` covers one topic; `skip`
and `stop` work mid-interview, and `/help` says how many `TO FILL` lines are
still open. The prompt it runs is `assistant/onboarding.md`.

On a VPS `/accept` cannot write `context/`: copy the proposal from
`memory/wiki/proposals/` over the brief by hand.

Or seed them yourself, one message at a time. What is worth saying on day one:

**Daily tasks** — the open loops in your head right now.
"Remind me to renew the passport by 15 March." "I need to call the garage."

**Home duties** — what comes back on a cadence, with the last date you did it
and the part number. The part number is the whole point.
"Water filter every 6 months, last changed 2026-09-01, cartridge Brita P1000."
"Boiler service every year, plumber does it, last in October 2025."

**Shopping** — things you are waiting to buy, with a target price if you have
one. "Watch the Garmin Edge 1050, alert me under 550 euro."

**Watchlist** — what you follow for news, and why.
"Follow the Stelvio pass opening date, I want to know when it opens."

**Adventures** — trips done and planned. With Drive set up:
"Read my trip documents and build the adventures list." Then "pin the
Dolomites trip" for the one you are working on.

**Training** — with the coach on: "How am I today?" The coach reads
Intervals, writes `memory/wiki/training.md`, and asks whatever
`context/training-rules.md` left unknown.

**Calendar** — "Which calendars can you see?" fills the ids in
`memory/wiki/calendar.md` if the wizard could not.

Anything the assistant learns along the way it writes down itself; the
nightly `scripts/distill.py` promotes what the day's log held.

## 4. Switch on the routines

Every routine ships disabled. `/routines` lists them; `/run <name>` tries one
now without enabling it — do that first and read what it sends.
`/enable <name>` switches it on.

| Routine | When | Needs |
|---|---|---|
| `morning-digest` | daily 07:00 | `gws`; tasks and duties lists |
| `weekly-summary` | Monday 08:00 | — |
| `price-check` | daily 08:30 | `price-hunt`; shopping list; scraper for `verified` prices |
| `watchlist-check` | daily 08:35 | watchlist |
| `adventure-watch` | Thursday 18:00 | `adventure-log`; a pinned trip |
| `coach-morning` | daily 07:30 | `coach`; Intervals |
| `coach-weekly` | Sunday 19:00, by mail | `coach`; Intervals; `MAIL_TO` or SMTP |

To change one — another time, another channel — ask the assistant in chat. It
rewrites the file disabled; you `/enable` it again. A new routine works the
same way: "every Friday at 17:00 tell me what is due next week".

## When something is missing

- Re-run `scripts/onboard.py` to add a feature: it keeps what is set.
- The wizard's last screen lists every `TO FILL` still open.
- `git --git-dir=.instance.git snap` after a good session keeps your
  briefs and wiki recoverable.
