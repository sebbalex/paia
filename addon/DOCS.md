# The owner Assistant

A personal assistant that runs on this machine, talks over Telegram, and
remembers. Same process as the VPS deploy — Claude Agent SDK, a curated wiki,
scheduled routines — packaged so the Supervisor keeps it alive instead of
systemd.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, add
   `https://github.com/sebbalex/paia`.
2. Install **PAIA**, fill in the options below, start it.
3. Watch the **Log** tab. First start seeds `/data` and installs claude-mem,
   so it takes a couple of minutes before the bot answers.

Requires a 64-bit system. A 32-bit Raspberry Pi OS is not supported: neither
the transcription wheels nor Bun exist for armv7.

## Options

| Option | Required | What it is |
|---|---|---|
| `telegram_bot_token` | yes | From @BotFather. |
| `telegram_allowed_user_ids` | yes | Numeric ids from @userinfobot. **This list is the entire access control model** — a message from any other id is dropped before the model sees it. |
| `claude_oauth_token` | one of two | `claude setup-token` on your laptop, `sk-ant-oat…`. Billed against a Claude plan. |
| `anthropic_api_key` | one of two | `sk-ant-api…` from console.anthropic.com. Metered. |
| `model` | no | Defaults to `claude-opus-5`. |
| `skills` | no | Which methods in `.claude/skills/` are live. A skill not named here is invisible to the assistant and refused if it names one anyway. |
| `drive_allowed_folders` | no | Google Drive folder ids the assistant may read, plus everything beneath them. **Empty means the whole Drive**, which is the old behaviour and not a safe default. |
| `drive_write_folders` | no | The folders Google Docs may be written in, one id or a comma-separated list: a new Doc lands in one of the folders itself (`drive_create_doc`, which names the folder in `folder_id` when several are listed), and a Doc in one of them or in any folder beneath one can be added to at its end (`drive_append_doc`), corrected one exact string at a time (`drive_replace_text`) or rewritten whole from new Markdown (`drive_rewrite_doc`). The last two are the only ones that destroy text: the range they delete is computed from the document rather than passed in, a rewrite refuses a document holding a table or an image, both are pinned to the revision they read, and Google's version history keeps what they replaced. No file is ever deleted, renamed, moved or shared. Must also appear in `drive_allowed_folders`. Everything but creating also needs the Docs API (`docs.googleapis.com`) enabled in the Cloud project. Empty means nothing is ever written at Drive. |
| `web_fetch_allowed_domains` | no | Empty is open web. |
| `whisper_model` | no | `base` on a Pi 4. `small` is better Italian and noticeably slower. |
| `whisper_language` | no | Empty auto-detects. `it` forces Italian. |
| `distill_hour` | no | Hour of the night the raw log is promoted into the wiki. Delete the option to turn it off. |
| `smtp_*` | no | For routines with `channel: email`. Without them such a routine falls back to Telegram rather than losing the result. |

Setting **both** Anthropic credentials fails at start rather than letting the
API key silently shadow the token — they are different credential types and
the key takes precedence.

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
4. Restart the add-on. Edits take effect only on restart.

Or let the wizard do steps 2–3 from a laptop with a clone of the repo and the
Samba share mounted. It asks the questions, fills the briefs on the share, and
prints these options as YAML for the Configuration tab:

```sh
python3 scripts/onboard.py --addon --root /Volumes/addon_configs/<hash>_paia
```

The wiki lives in `/data`, out of reach of the share. You fill it by talking
to the bot — see part 3 of `ONBOARDING.md`.

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
