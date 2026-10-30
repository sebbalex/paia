# The owner Assistant

A personal assistant that runs on this machine, talks over Telegram, and
remembers. Same process as the VPS deploy — Claude Agent SDK, a curated wiki,
scheduled routines — packaged so the Supervisor keeps it alive instead of
systemd.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, add
   `https://github.com/sebbalex/paia`.
2. Install **PAIA**, fill in the bot token, `llm_provider` and
   `llm_credential`, start it.
3. Watch the **Log** tab. First start seeds `/data` and installs claude-mem,
   so it takes a couple of minutes before the bot answers.
4. Find the line `PAIRING CODE: 123456` in the Log and send those six digits
   to your bot in a private chat. That chat becomes the owner and pairing
   closes for good. The code lasts 10 minutes (a message after that puts a
   fresh one in the Log); five wrong codes close pairing until the next
   restart.

Requires a 64-bit system. A 32-bit Raspberry Pi OS is not supported: neither
the transcription wheels nor Bun exist for armv7.

## Options

| Option | Required | What it is |
|---|---|---|
| `telegram_bot_token` | yes | From @BotFather. |
| `telegram_allowed_user_ids` | no | Leave empty and pair with the code from the Log (Install, step 4). Filled in, these numeric ids are the allowlist and pairing never opens; they also **replace** a paired owner, which is how you take access away. Either way **the allowed ids are the entire access control model** — a message from any other id is dropped before the model sees it. |
| `llm_provider` | yes | Which model service: `claude_subscription`, `anthropic_api` or `openrouter`. |
| `llm_credential` | yes | That service's one credential. `claude_subscription`: `claude setup-token` on your laptop, `sk-ant-oat…`, billed against a Claude plan. `anthropic_api`: console.anthropic.com, `sk-ant-api…`, metered. `openrouter`: openrouter.ai, `sk-or-…`, runs the assistant on any tool-calling model there instead of Claude — same tools, same policy gate, no claude-mem — and `model` must then be an OpenRouter slug, `vendor/model-name`. A credential whose prefix belongs to another provider fails at start and the Log says which provider it is. |
| `openrouter_search_model` | no | With OpenRouter: the model WebSearch asks. Defaults to `perplexity/sonar`; empty turns WebSearch off. |
| `openrouter_images` | no | With OpenRouter: off for a model that takes no image input. |
| `model` | no | Defaults to `claude-opus-5`. With OpenRouter, required and a slug. |
| `skills` | no | Which methods in `.claude/skills/` are live. A skill not named here is invisible to the assistant and refused if it names one anyway. |
| `drive_allowed_folders` | no | Google Drive folder ids the assistant may read, plus everything beneath them. **Empty means the whole Drive**, which is the old behaviour and not a safe default. |
| `drive_write_folders` | no | The folders Google Docs may be written in, one id or a comma-separated list: a new Doc lands in one of the folders itself (`drive_create_doc`, which names the folder in `folder_id` when several are listed), and a Doc in one of them or in any folder beneath one can be added to at its end (`drive_append_doc`), corrected one exact string at a time (`drive_replace_text`) or rewritten whole from new Markdown (`drive_rewrite_doc`). The last two are the only ones that destroy text: the range they delete is computed from the document rather than passed in, a rewrite refuses a document holding a table or an image, both are pinned to the revision they read, and Google's version history keeps what they replaced. No file is ever deleted, renamed, moved or shared. Must also appear in `drive_allowed_folders`. Everything but creating also needs the Docs API (`docs.googleapis.com`) enabled in the Cloud project. Empty means nothing is ever written at Drive. |
| `web_fetch_allowed_domains` | no | Domains WebFetch and the page tools may open, subdomains included. Empty or `*` is the open web. |
| `whisper_model` | no | `base` on a Pi 4. `small` is better Italian and noticeably slower. |
| `whisper_language` | no | Empty auto-detects. `it` forces Italian. |
| `distill_hour` | no | Hour of the night the raw log is promoted into the wiki. Delete the option to turn it off. |
| `smtp_*` | no | For routines with `channel: email`. Without them such a routine falls back to Telegram rather than losing the result. |

Installs from before `llm_provider` may still have the old separate fields —
`claude_oauth_token`, `anthropic_api_key`, `openrouter_api_key`. They keep
working as long as `llm_credential` is empty. Filling in `llm_credential`
beside any of them fails at start: move the value over and empty the old one.

## Where things live

`/data` is the add-on's persistent volume and survives restarts, upgrades and
reinstalls-with-keep-data. The assistant's root is `/data/paia`:

```
memory/wiki/     what it understood — authoritative, and yours to read
memory/raw/      every exchange, verbatim, append-only
routines/        scheduled prompts, with the enabled flag you set via /enable
.claude/skills/  methods it wrote
models/          Whisper weights, downloaded on the first voice note
inbox/           attachments you sent it, swept after 7 days
state/           session id and the tool audit log
```

Everything else in that tree — the code, `IDENTITY.md`, `ROT.md`, `CLAUDE.md`
— is copied fresh from the image at every start, owned by root and mode 0444.
The assistant runs as an unprivileged user, so it cannot rewrite what it is or
what it runs next boot even if the in-process policy were bypassed. Editing
those files means shipping a new image version, which is the point.

Your briefs are the exception. On first start, template briefs are seeded
into **`/addon_configs/<hash>_paia/context/`** — `<hash>` is the short prefix
Home Assistant gives this add-on repository; it is the only `*_paia` folder
there — and copied in read-only at every start. That folder is the authority
from then on; the assistant can read the copy but never write either one.
The image ships no one's memory: the wiki starts as empty pages and every
example routine starts disabled.

### Filling in your briefs

1. Start the add-on once, so the folder is seeded.
2. Open it, whichever way you have:
   - **Samba share** add-on: the `addon_configs` share. From a Mac, Finder →
     ⌘K → `smb://homeassistant.local/addon_configs`.
   - **Studio Code Server** add-on: `/addon_configs` is in its file tree.
   - **File editor** add-on: it shows only `/homeassistant` by default; turn
     off `enforce_basepath` in its configuration to browse up to
     `/addon_configs`.
3. Fill in the lines marked **TO FILL** in each `.md` file, in English, and
   read the rest — every line is a default you may change.
4. Save. The add-on picks the change up within a few seconds, no restart.

Or never open the share: tell the bot what should change. It writes the new
brief as a proposal, you read the diff with `/review <brief>` and apply it
with `/accept <brief>` (`/proposals` lists what is waiting, `/reject`
discards). Accepting writes the same file on the share.

Or let the wizard do steps 2–3 from a laptop with a clone of the repo and the
Samba share mounted. It asks the questions, fills the briefs on the share, and
prints these options as YAML for the Configuration tab:

```sh
python3 scripts/onboard.py --addon --root /Volumes/addon_configs/<hash>_paia
```

The wiki lives in `/data`, out of reach of the share. You fill it by talking
to the bot — see part 3 of `ONBOARDING.md` — or bring it over from an
instance you already run.

### Moving an existing instance in

Put its memory and routines under `import/` in the same config folder, then
restart:

```
/addon_configs/<hash>_paia/
  context/            your briefs, as above
  import/
    memory/wiki/      *.md
    memory/raw/       *.md, optional: the verbatim history
    routines/         *.md
```

```sh
# from the old instance's root, with the SSH add-on (scp -O: it has no sftp)
ssh root@homeassistant.local mkdir -p /addon_configs/<hash>_paia/import/memory
scp -O -r memory/wiki memory/raw root@homeassistant.local:/addon_configs/<hash>_paia/import/memory/
scp -O -r routines           root@homeassistant.local:/addon_configs/<hash>_paia/import/
```

On the next start the add-on copies them into `/data/paia`, overwriting the
empty pages of the same name, and deletes `import/` — so your memory does not
sit on a share, and a later restart does not import the old snapshot over
newer memory. The Log tab lists what came in. Only `.md` files are taken;
anything else under `import/`, a `context/` folder included, is dropped.
Routines keep their `enabled` flag: if the old instance is still running, set
them to `false` first, or both will deliver. Do it before your first chat, or
your files replace what the bot has already written in those pages.

`/data/options.json` holds your tokens and sits outside the assistant's root,
so it is not readable by any tool the model has.

## Google Workspace

Calendar, Gmail and Drive go through the `gws` CLI, which needs a one-time
interactive login. It cannot be done from the add-on options, because it is an
OAuth flow in a browser. From the Terminal & SSH add-on:

```sh
container=$(docker ps --format '{{.Names}}' | grep paia)
docker exec -it "$container" su -s /bin/bash assistant -c "gws auth setup"
docker exec -it "$container" su -s /bin/bash assistant -c "gws auth login"
```

The credentials land under `/data/home` and survive restarts and upgrades.
Until you do this the Workspace tools are simply absent — the rest works.

## Upgrading

The Supervisor pulls the tag named by `version` in the manifest. Bump it,
push, and the update appears once the workflow has published the image. Your
`/data` is untouched: memory, routines, skills and enabled flags all carry
over, and anything the new image adds — a new routine, a new skill — is copied
in beside them.

What an upgrade does **not** do is overwrite a routine or a skill that already
exists on the volume: those are state, and replacing them would throw away the
enabled flag you set and whatever the assistant wrote. To force a shipped
version back in, delete that one file or folder under `/data/paia`
and restart.

## Commands

In the Telegram chat:

```
/routines          list and state
/enable <name>     enable a routine
/disable <name>    suspend one
/run <name>        run it now
/help              this list
```

The assistant writes routines with `enabled: false` and cannot flip that flag.
Activation is yours.

## Resources

A Pi 4 with 4 GB runs it, and with 8 GB runs it comfortably. The heavy moments
are transcription, which pins a core for roughly the length of the note, and
the `claude` CLI's Node process. Nothing listens on a port: Telegram is
long-polled, so no forwarding and no exposure.
