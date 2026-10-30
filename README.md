# PAIA — Personal AI Assistant

A personal assistant that lives on a server, talks over Telegram, and
remembers. Built on the Claude Agent SDK with claude-mem for semantic recall.

## Shape

```
Telegram (long-poll, no inbound port)
   |
assistant/main.py ---- assistant/agent.py ---- Claude Agent SDK
   |                                              |
memory/raw/  (append-only log)              claude-mem hooks + MCP
                                                  |
                              ~/.claude-mem  (SQLite + ChromaDB)
                                                  |
scripts/distill.py (nightly) --------------> memory/wiki/ (authoritative)
```

## Memory tiers

Read `ROT.md` for the rules. Short version:

- `memory/wiki/` — curated facts, the source of truth (git-tracked only if you set up `.instance.git`).
- claude-mem — automatic semantic index over every session. Finds the thread;
  never trusted as fact.
- `memory/raw/` — daily plaintext log. Survives everything else. Deliberately
  **not** tracked by git: it is the unfiltered conversation, so it stays on the
  machine. The wiki is the memory that gets committed.

## How the session window works

One SDK session stays open for 15 minutes after your last message, then closes.
That boundary matters: claude-mem compresses an exchange into memory on session
*end*, so a session per message would shatter memory into fragments and a
session that never ends would never be remembered. Tune with
`idle_timeout_seconds` in `assistant/config.py`.

## Voice messages

Send the bot a voice note and it transcribes it locally, then answers as if you
had typed it. The transcript comes back first (`🎙 …`) so a misheard word is
obvious immediately rather than after a confusing reply.

Transcription runs on this machine — faster-whisper on CPU, PyAV decoding
Telegram's OGG/Opus, no ffmpeg binary needed and **no audio sent anywhere**.
The only network access is a one-time download of the model weights into
`models/` on first use; after that it runs offline.

Pre-fetch the model before deploying, so the first voice note isn't a
several-hundred-megabyte wait:

```sh
.venv/bin/python -c "from faster_whisper import WhisperModel; \
  WhisperModel('small', device='cpu', compute_type='int8', download_root='models')"
```

`WHISPER_MODEL` picks the size (`small` by default — good Italian on CPU;
`medium` wants more RAM than a 4 GB VPS has). `WHISPER_LANGUAGE` forces a
language; leave it unset to auto-detect.

## Files and images

Send a PDF, a photo or a text file and the assistant reads it. The caption is
the question — "quanto costa?" on an invoice — and without one it just says
what the file contains.

Attachments land in `inbox/` (gitignored) under a timestamped, sanitised name,
and the model opens them with `Read`, which handles PDFs and images natively.
They stay readable for `inbox_retention_days` (7 by default) so you can keep
asking about the same document instead of resending it; older ones are swept on
each new arrival.

Accepted types are an allowlist in `Config.allowed_file_types` — PDF, common
image formats, and plain text. Anything else is refused before it is
downloaded.

## Local run

New here? **`ONBOARDING.md`** walks the whole setup; `scripts/onboard.py`
asks every question and writes `.env`, the briefs and the instance for you.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 scripts/onboard.py    # or: cp .env.example .env and fill in the tokens
.venv/bin/python -m assistant.main
```

Prerequisites: Node (the SDK drives the `claude` CLI) and, for claude-mem,
Bun — `npx claude-mem install` sets it up. Voice needs nothing beyond
`requirements.txt`; drop the `faster-whisper` line and voice degrades to a
polite refusal while everything else keeps working.

## Your instance

The repository is the engine. What makes it yours -- `context/` (briefs),
`memory/wiki/` (what it learned), `routines/` -- is ignored by git and by
Docker, so it never reaches the public repo or the image. `defaults/` holds
the empty shapes: template briefs with **TO FILL** lines, an empty wiki, and
example routines, all disabled.

On a fresh clone, copy them in once without overwriting anything:

```sh
cp -Rn defaults/. .
```

The add-on does the same thing on first start (`docker/entrypoint.py`).

To version your own copy, keep a private repo beside the public one, sharing
the same working tree:

```sh
git init --bare .instance.git
git --git-dir=.instance.git config core.bare false
git --git-dir=.instance.git config core.worktree "$PWD"
printf '/*\n!/context/\n!/memory/\n!/routines/\n/memory/raw/\n' > .instance.git/info/exclude
git --git-dir=.instance.git config alias.snap \
  '!f() { git add -f context memory/wiki routines && git commit -m "${1:-snapshot $(date +%F)}"; }; f'
git --git-dir=.instance.git snap
```

`add -f` is needed because the public `.gitignore` hides those paths from
both repos. Give `.instance.git` a private remote if you want it off the
machine. It cannot be a symlink to somewhere else: `assistant/policy.py`
resolves every path with `realpath` and would refuse every write.

## VPS deploy

2 vCPU / 4 GB is plenty; the process is I/O bound on API calls.

```sh
adduser --system --group --home /home/assistant assistant
git clone <repo> /opt/paia && chown -R assistant:assistant /opt/paia
sudo -u assistant python3 -m venv /opt/paia/.venv
sudo -u assistant /opt/paia/.venv/bin/pip install -r /opt/paia/requirements.txt
install -m 600 -o assistant -g assistant .env /opt/paia/.env
cp deploy/*.service deploy/*.timer /etc/systemd/system/
systemctl enable --now paia paia-distill.timer
journalctl -u paia -f
```

claude-mem must be installed on the box as well — `.claude/settings.json`
enables it, but does not fetch it:

```sh
sudo -u assistant npx claude-mem install
```

## Home Assistant add-on

The same process, packaged so a Supervisor keeps it alive instead of systemd.
Add `https://github.com/sebbalex/paia` as an add-on repository, fill
in three options (bot token, `llm_provider`, `llm_credential`), start it,
and send the bot the pairing code from the Log tab; it interviews you from
there. Install and configuration: **`addon/DOCS.md`**.

```
Dockerfile              node:22-bookworm-slim + Python venv + claude/gws/claude-mem CLIs
docker/entrypoint.py    options -> environment, seed /data, drop privileges, supervise
addon/config.yaml       the manifest the Supervisor reads (version == image tag)
repository.yaml         makes this repo an add-on repository
.github/workflows/      native amd64 + arm64 runners -> one manifest on ghcr.io
addon/CHANGELOG.md      what the add-on's Changelog tab shows before Update
```

**Every change to an image's inputs ships with a version bump and a changelog
entry**, because the Supervisor offers an update only when `version` changes
and CI never rebuilds a published tag. A pre-commit hook enforces it; enable it
once per clone with `git config core.hooksPath .githooks`. When a staged change
touches an add-on's inputs and the commit lacks the bump or the entry, the hook
runs `tools/addon_release.py`, which bumps the patch version and writes the
entry from `$ADDON_CHANGELOG` or a prompt on the terminal, and stages both. For
a minor or major bump, run it yourself first:
`tools/addon_release.py bump paia --minor -m "what changed"`.

Two things are worth knowing before reading further.

**The code is copied onto `/data`, not symlinked into it.** `assistant/policy.py`
canonicalises every path with `os.path.realpath` before deciding, so a symlink
pointing out of `ASSISTANT_ROOT` collapses to a path outside it and every write
is refused. The root has to be one real tree.

**Ownership replaces `ReadOnlyPaths`.** The systemd unit stops the model
rewriting its own instructions by mounting the tree read-only. In a container
the entrypoint does it with `chown`: the tree is root-owned and mode 0444/0555,
only the directories `_WRITE_ROOTS` names belong to the unprivileged user the
assistant runs as, and `/data/options.json` — which holds the tokens — sits
outside the root entirely.

64-bit only. `ctranslate2` and `onnxruntime` publish no armv7 wheels and Bun has
no 32-bit ARM build, so a 32-bit Raspberry Pi OS cannot run it.

## Skills

Method, written once and followed every time. A routine says what to do and
when; a skill says how — what counts as a verified price, what never to do
while checking one. They live in `.claude/skills/<slug>/SKILL.md`, and the
assistant reaches for one before improvising.

```markdown
---
name: price-hunt
description: >
  Find what a product actually costs right now, across sellers, and say
  which price is real. Use whenever the job is a price or availability.
---

# Price hunt
...
```

Which ones are switched on is a list of names in `.env`:

```
ASSISTANT_SKILLS=price-hunt,adventure-log,trip-design
```

That list is the enable switch, the same idea as `enabled: false` on a
routine. The assistant may write a skill; it stays inert until the name is
added, and a live one is frozen against edits. `.env` is a file it can neither
read nor write, so the switch is always a human act. Changing the list needs a
restart.

Don't confuse `.claude/skills/` with `skills/` at the repo root — the latter is
vendored `gws` CLI documentation, reference only, never loaded.

Three are shipped. `price-hunt` proves a price. `adventure-log` reads the trip
documents in Drive and reuses what past trips taught. `trip-design` turns a
one-line idea — "Svalbard next year by bike?" — into a plan in chat and, once
the owner says go, into a new Google Doc in their adventures folder through
`drive_create_doc`. That, `drive_append_doc`, `drive_replace_text` and
`drive_rewrite_doc` are the only writes the assistant has at Drive: all four
confined to the write folders, one creating a document, one adding to the end
of one, one swapping a single exact string inside one, and one replacing a
whole body with new Markdown. The last two are the only things here that
destroy text, and they are bounded accordingly — the range they delete is
computed from the document rather than passed in, a rewrite refuses a document
holding a table or an image, every write is pinned to the revision it read,
and Google's version history keeps whatever was replaced. None of the four can
delete, rename, move or share a file.

## Routines

Scheduled prompts. Ask for one in words — "every Monday at 8 send me a
summary" — and the assistant writes it to `routines/<slug>.md`:

```markdown
---
name: weekly-summary
schedule: 0 8 * * 1
enabled: false
channel: telegram
---

Summarise what happened last week.
```

Commands, handled in Python and never by the model:

```
/routines          list and state
/enable <name>     enable
/disable <name>    suspend
/run <name>        run now
/help              this list
```

The list lives in `COMMANDS` in `assistant/main.py` and feeds both `/help` and
the chat menu: on startup the bot calls `setMyCommands`, so the menu Telegram
shows is always what the dispatcher can handle.

`channel` is `telegram` or `email`. Email needs the `SMTP_*` variables; without
them a routine that asks for email falls back to Telegram rather than losing
the result.

**The assistant cannot activate a routine.** It writes them `enabled: false`
and the policy rejects any write containing `enabled: true`. Activation is
yours, via `/enable` — see SECURITY.md for why that boundary exists.

## Research

The assistant searches the web and opens what it finds. `WebFetch` is open by
default; `WEB_FETCH_ALLOWED_DOMAINS` narrows it to an allowlist if you ever
want that. https only, and hostnames must resolve to public addresses — see the
SSRF section of SECURITY.md for why that matters on a VPS.

## The scraper worker

`WebFetch` is a plain GET with a Python TLS fingerprint, and shops notice.
`memory/wiki/price-sources.md` is the receipt: most of the domains worth
watching answered it with a 403 or with a page whose price had not been painted
yet. A browser User-Agent was tried and changed nothing, because the User-Agent
was never what was being matched on.

`scraper/` is a second process that opens pages properly, climbing only as far
as it has to:

| tier | what it is | when it is needed |
|---|---|---|
| `http` | a request with Chrome's TLS fingerprint (`curl_cffi`) | most shops |
| `headless` | Playwright Chromium, stealth patches, a profile per domain | the price is painted by JavaScript |
| `headful` | headed under Xvfb with real WebGL, over CDP | the shop fingerprints the rendering stack |
| `human` | the same, clicked through `/dev/uinput` with `ydotool` | a challenge that tests `event.isTrusted` |

A run starts at whichever tier last worked for that domain and stops at the
first that opens the page, so the ladder gets cheaper with use. Extraction is
deterministic and tries the shop's own structured data first — JSON-LD, then
microdata, then OpenGraph, then per-domain selectors — because a schema.org
`Offer` survives a redesign and `.price-box__amount--3f2a` does not. Only when
all of that fails on a page that demonstrably loaded does a model get asked for
a new selector, and its answer is re-run against that page and must produce
something recognisably money before it is kept.

The assistant reaches it through three read-only tools — `page_price`,
`page_read`, `page_sources` — and `price-hunt` uses them as its primary
instrument, with search demoted to finding out who sells the thing.

Run it:

```bash
python3 -m venv .venv-scraper
.venv-scraper/bin/pip install -r scraper/requirements.txt
SCRAPER_TOKEN=$(openssl rand -hex 32) .venv-scraper/bin/python -m scraper.app
```

Then set `SCRAPER_URL` and `SCRAPER_TOKEN` in `.env`. `GET /health` lists which
tiers this machine can actually run and why not, for the ones it cannot.

Deployment: `docker/scraper.Dockerfile` plus `addon/scraper/config.yaml` for the
add-on, `deploy/paia-scraper.service` for a VPS. Neither publishes a
port. Tier 4 needs `/dev/uinput` mapped in; without it the ladder reports tier 4
unavailable and stops at tier 3, which is a degradation rather than a failure.
Leave `SCRAPER_URL` empty and the page tools are simply not offered — price
hunting falls back to search, exactly as it worked before.

**Playwright ships no Chromium for linux/arm64.** On a Pi the image installs
Debian's `chromium` and points `CHROMIUM_PATH` at it. This is the thing most
likely to break a build.

## Security

Threat model and the full control stack: **SECURITY.md**. Summary:

- `TELEGRAM_ALLOWED_USER_IDS` is the whole access control model. It is checked
  against numeric ids before the model is invoked. Get yours from @userinfobot,
  or leave the list empty and pair with the code the log prints (`assistant/pairing.py`).
- `Bash` is in `disallowed_tools`. Enabling it turns any message into remote
  code execution on the box — do that only behind a `can_use_tool` callback.
- `.env` is `chmod 600` and never committed.
- Set a monthly spend cap in the Anthropic console. claude-mem runs its own
  SDK agent to compress sessions, so token use is roughly double the visible
  conversation.
