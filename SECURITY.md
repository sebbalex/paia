# Security

Threat model and controls for an assistant that holds credentials, has file
tools, and is reachable from the internet.

## What we are defending against

1. **Prompt injection.** Content the assistant reads — a web page, a file in
   `context/`, later an email or calendar invite — carrying text designed to be
   read as instruction. This is the primary threat: it needs no access to the
   server, only for the assistant to read something.
2. **Privilege escalation via self-modification.** The assistant's rules live
   in files it can read (`IDENTITY.md`, `ROT.md`) and its behaviour in files it
   runs (`assistant/`, `.claude/settings.json`). If it can write them, one
   injected instruction becomes permanent, and survives into the next session.
3. **Exfiltration.** Credentials or private memory leaving via a URL, a search
   query, or a Telegram reply.
4. **Unauthorised access.** A stranger messaging the bot.

## Controls, weakest to strongest

**System prompt guidance** (`INJECTION_GUARD` in `assistant/agent.py`).
Tells the model that tool output is data, never instruction, and to report
attempts rather than comply. This is advisory: it shapes behaviour, it does not
constrain it, and text the model reads can argue against it. It held in
testing, but it is not what the design rests on.

**Tool surface** (`Config.tools`). The model is only offered Read, Write, Edit,
Glob, Grep, WebSearch, WebFetch, TodoWrite. `Bash` is not in the set, so no
amount of persuasion produces a shell — there is no shell to call.

**PreToolUse policy hook** (`assistant/policy.py`). Every tool call is checked
in Python before it runs, default-deny. It never consults the model, so it
cannot be argued with.

- Paths are canonicalised with `realpath` before comparison, so traversal
  (`../../etc/passwd`), `~` expansion, and symlinks pointing out of the tree all
  resolve to their real location and fail the containment check.
- Reads are confined to the assistant's own directory, minus `.env*`, `state/`,
  `.git/`, and key material.
- **Writes are confined to `memory/wiki/`, `routines/` and
  `scripts/proposals/`.** Nothing else is writable — not the rules, not the
  code, not the plugin settings. This is the control that makes injection
  non-persistent.
- `scripts/proposals/` exists so the assistant can propose a reusable script
  without being able to plant one that runs. `scripts/` itself holds
  `distill.py`, which the systemd timer executes nightly as a process with the
  model's full toolset; writable, it would be a scheduled foothold. The owner reads
  a proposal and moves it across themselves, and the assistant has no shell to run
  either copy.
- `WebFetch` is open by default — the assistant is meant to research freely —
  but every URL must be `https` (on plain http anyone in the middle rewrites
  the page the model then reads as fact) and every hostname must resolve to a
  **public** address. Setting `WEB_FETCH_ALLOWED_DOMAINS` narrows it back to an
  allowlist, matched on label boundaries so `evil-example.com` does not satisfy
  `example.com`.
- **Google Workspace reaches Google without a shell.**
  `assistant/gws.py` exposes fifteen in-process tools — `calendar_events`,
  `calendar_calendars`, `calendar_event`, `calendar_create_event`,
  `calendar_update_event`, `calendar_move_event`, `gmail_search`,
  `gmail_message`, `drive_search`, `drive_file`, `drive_read_file`,
  `drive_create_doc`, `drive_append_doc`, `drive_replace_text`,
  `drive_rewrite_doc` — which run the `gws` CLI through
  `subprocess` with an **argv list**, never a command string and never
  `shell=True`. The model supplies fields; Python picks the service, resource
  and method from a fixed table (`_ALLOWED_CALLS`) and validates every value,
  so there is no command line for injected text to extend and no way to reach
  a method that is not listed. The policy then pins the fifteen tool names, so
  adding a tool to `gws.py` does not by itself make it callable.
  This is deliberately *not* the `gws` agent skill: the skill drives the same
  CLI through Bash, and a bot that reads web pages, invitations and mail must
  not hold a shell.
- **The writes are shaped so a successful injection cannot reach a person.**
  The owner granted the calendar write scope on 2026-08-19 and asked for create,
  then for update and move on 2026-08-20. Four properties bound all three.
  Request bodies are constructed from named fields (`_event_body`,
  `_patch_body`), so an `attendees` list cannot arrive from the model's
  arguments however it is spelled — which matters, because an event with
  attendees makes Google send mail in the owner's name, and that is the capability
  an injected instruction would actually want. Every call passes
  `sendUpdates=none`. The two edits read the event first and refuse it
  (`_guard_edit`) when it has guests, whose own copies would change, or when
  it is a recurring master, where one sentence would rewrite every occurrence.
  And they report the before-values, so an unwanted edit can be reversed by
  hand.
  `delete` is not in the table and neither is `update` (the full-replacement
  method, as opposed to `patch`): an edit leaves a trace and can be put back,
  a deletion cannot. Worst case for a compromised turn is a junk or shifted
  event on the owner's own calendar. Everything else outward-facing — sending or
  replying to mail, answering an invitation, sharing a file — remains absent
  rather than denied.
- **Reading mail widens the injection surface more than anything else here.**
  A calendar invite needs the owner's address; an email needs nothing, so any
  stranger can put text into the model's context on demand. Three things
  bound it: the tools are reads, so the worst case is what the model *says*
  rather than what it does; every mail payload is prefixed with a warning
  naming it as third-party content; and bodies are truncated at 4000
  characters, so a long message cannot bury the rest of the turn. The residual
  risk is exfiltration through a tool that talks outward — `WebFetch` with a
  crafted URL — which is why URLs are still `https`-only and resolved to
  public addresses, and why nothing puts secrets in a URL. Mail arriving with
  instructions in it is a thing to report to the owner, and `ROT.md` says so.
- **Drive is reachable one folder tree at a time.** Read-only is not much of a
  bound on a Drive: the account holds contracts, payslips and scans of
  documents next to the shopping list, and a search reaches all of it at once.
  `DRIVE_ALLOWED_FOLDERS` names the folders the Drive tools may look
  inside, and `_DriveScope` in `assistant/gws.py` enforces it two ways.
  `drive_search` gets an `'<id>' in parents` clause per folder appended to the
  query, so a file outside them is never returned by Google in the first place
  — nothing to filter here, nothing to leak in a count. `drive_file` and
  `drive_read_file` take an id, which can arrive from a mail, a shared
  document or a Telegram message, so those walk the file's parents upwards
  until a whitelisted folder appears; anything unresolvable — a parent that
  errors, a chain past the hop limit, a file with no parents because it lives
  in someone else's Drive — counts as outside. The check runs before any
  download, so a refused file is never fetched. Because `in parents` means
  direct children only, the whitelist is expanded once into its full subtree
  and cached for ten minutes; a folder created since is caught by the upward
  walk, and an expansion past 200 folders fails loudly rather than silently
  answering from part of the tree. A malformed id in the variable raises at
  startup: dropping it would widen the scope to everything, which is the one
  failure direction that must not be quiet. Empty `DRIVE_ALLOWED_FOLDERS`
  leaves the whole Drive readable — the behaviour before this existed — and is
  logged as a warning at every start.
- **Reading a Drive file's contents is mail's problem with a file attached.**
  `drive_read_file` (2026-08-20) is a read, but a document shared with the owner by
  a stranger is as attacker-writable as an email and is a more comfortable
  place to bury a paragraph addressed to a model — nobody scrolls to page
  eleven. It is bounded the same way: contents are prefixed with a warning
  naming them third-party text, and truncated (20 000 characters by default,
  100 000 at most) so a document cannot bury the rest of the turn.
  The one new mechanism is the download. `gws` never puts file bytes on
  stdout: `--output` is the only way out, and it refuses a path that resolves
  outside the process's working directory. So `Gws._download` creates a
  temporary directory, runs the CLI with `cwd` set to it and a bare relative
  filename, reads the result and deletes the directory. The model supplies a
  file id, never a path — there is no argument through which it could ask for
  a write anywhere, and nothing survives the call on disk. On the server that
  directory is inside the unit's `PrivateTmp`, so a download is invisible to
  every other process on the box and gone at restart even if a crash skipped
  the cleanup. Downloads are capped at 10 MB, and only Google Docs, Sheets and Slides (converted by
  Google to Markdown, CSV and text), files already stored as text, and PDFs
  are read at all; an image, a `.docx` or an archive is refused by type rather
  than decoded into plausible-looking rubbish.
- **PDFs are parsed in-process, and the parser is the new attack surface.**
  `pypdf` (pure Python, no system dependency) turns a downloaded PDF into
  text; extraction runs on a worker thread, is capped at 50 pages, and a page
  that fails to parse is skipped rather than losing the other forty-nine. A
  malformed file is a `GwsError`, not a crash. Nothing in the PDF is executed:
  no JavaScript, no embedded file, no remote resource is fetched — the parser
  reads the text objects and nothing else, and the bytes never touch a viewer.
  The output is prefixed with the same third-party-content warning as any
  other file, because a PDF is a *more* comfortable place to hide a paragraph
  addressed to a model than an email is.
  Two failure modes are reported rather than papered over, which is a
  correctness property as much as a security one. A scanned PDF has no text
  layer, and there is no OCR here, so it is named as a scan instead of
  answered as an empty document. A PDF whose embedded font carries no usable
  character map extracts as control codes (`REPERTORIO` comes out as
  `5(3(5725,2`); `_unreadable` measures the proportion of control characters
  and refuses above 5%, because rubbish handed to a model gets a meaning
  inferred for it.
- **The first Drive write creates and cannot do anything else.**
  `drive_create_doc` (2026-08-20) exists so a trip the owner designs in a chat can
  become the Google Doc they would have written, and it is bounded on five
  sides. `drive.files.update`, `drive.files.delete` and every
  `drive.permissions` method are absent from `_ALLOWED_CALLS`, so a document
  that already exists cannot be edited, replaced, trashed or shared by
  anything the model says — the failure mode of a write tool on a Drive full
  of contracts is not a bad new file, it is a good old one overwritten. The
  parent folder comes from `DRIVE_WRITE_FOLDERS`, the one or more ids read from
  the environment: with several open the model passes `folder_id` to choose
  between them and the id is checked against that list before anything else
  happens, so a mail or a document can never aim the write at a folder the owner
  did not open, and empty (the default) means creating is simply unavailable. The folder is fetched and run through
  `_DriveScope.check` before any bytes are sent, so the write target must also
  be inside the read whitelist — a document the assistant cannot read back is
  a document nobody ever checks. A title already present in that folder is
  refused with the existing file's link rather than duplicated. And the
  content rides the same way a download comes back: `Gws._upload` writes the
  Markdown into a temporary directory it owns, runs the CLI with `cwd` set to
  it and a bare relative filename, and deletes the directory — the model
  supplies text, never a path, so there is no argument through which it could
  read something off the disk and post it to Google.
- **The second Drive write only ever adds to the end.**
  `drive_append_doc` (2026-08-21) exists because a trip document goes on being
  true after it is filed — the refuge that answered, the bus that actually
  runs — and the alternative was the owner pasting every later finding by hand. It
  reaches the Docs API, which is a general editing API, so the bound is not
  "which method" but "which requests": `_append_requests` builds the whole
  batch out of the text and the body's end index, and the only request kinds
  it can emit are `insertText` at the end, `updateParagraphStyle` and
  `createParagraphBullets` over the paragraphs that insert just created, and
  `deleteParagraphBullets` over the same range, which removes list formatting
  and not one character. No range in the batch comes from the model, and this
  builder emits nothing that deletes, so the worst outcome of a bad append is
  a paragraph the owner deletes by hand — never a line of their own rewritten.
  Which document is reachable is decided by Drive, not by the
  caller: the file id is resolved through `drive.files.get`, and the write is
  refused unless Drive reports a Google Doc, not trashed, sitting inside
  `DRIVE_WRITE_FOLDERS` — one of those folders or any folder beneath one, since
  a trip usually has a folder of its own. "Inside" is decided by `_inside`,
  which walks the file's parents **upwards** at Drive until one of the write
  folders turns up or the hop limit runs out, and counts anything unresolved
  as outside.
  That is the same walk, one implementation, that confines every Drive read to
  `DRIVE_ALLOWED_FOLDERS`, and `_DriveScope.check` still runs afterwards so
  the document is inside the read whitelist too. A file id arriving from a
  mail or a shared document therefore goes nowhere. `writeControl.requiredRevisionId` carries
  the revision the end index was read at, so a document edited between the
  read and the write fails the batch instead of landing at a stale offset.
  `drive.files.update` stays absent throughout: the Doc's own bytes are still
  unreachable from this process.
- **The two writes that change existing text are bounded by where their range
  comes from.** `drive_replace_text` and `drive_rewrite_doc` (2026-09-01)
  exist because a document that can only grow eventually says something false
  at the top, and the alternative was the owner retyping a plan whenever a route
  changed. They are the first tools here that can destroy something they wrote,
  so what limits them is not the API method — both send an ordinary
  `batchUpdate`, like the append — but the fact that no range in either batch
  is chosen by the model. A replace sends one `replaceAllText` built from the
  two strings it was given, with `matchCase` on so it cannot reach text that
  was not quoted, and only after the document has been read and the matches
  counted: no match, or more than one where the caller did not say it meant
  all of them, and it refuses **before** writing rather than reporting how
  many it changed afterwards. Both strings are single-line and capped, because
  `replaceAllText` cannot cross a paragraph break and a multi-line find would
  look like an ordinary miss. A rewrite sends exactly one
  `deleteContentRange` over `[1, end - 1)` — the whole body, computed by
  `_doc_end_index` from the document Google just returned — followed by the
  same insert-and-style the append uses; there is no argument through which a
  narrower or wider range could be requested, so it can replace everything or
  nothing, never a paragraph of the model's choosing. A document holding a
  table, a table of contents or an image is refused outright
  (`_structured`), since none of those survive the round trip out as Markdown
  and back, and a rewrite would otherwise delete them and report success. Both
  require `writeControl.requiredRevisionId` and refuse a document that returns
  no revision — the append merely uses one when present, but a stale append
  puts a paragraph in the wrong place while a stale rewrite would delete an
  edit made a second earlier. Both go through the same `_doc_for_write` gate
  as the append: a Google Doc, not trashed, inside `DRIVE_WRITE_FOLDERS` by
  Drive's own account of its parents, and inside the read whitelist as well.
  The residual risk is honest: a successful injection that also gets past
  the owner reading the text in chat could blank a trip document. It cannot reach
  another file, another person, or anything outside those folder trees, and
  Google's version history keeps the replaced text, which the tool tells the
  model to pass on when it reports.
- Unknown tools are denied, so an SDK update that adds a capability does not
  silently grant it. MCP servers are matched on server and operation, not on
  the full tool name, because the prefix depends on how each one is mounted.

Note that this hook is a *hook*, not a `can_use_tool` callback, and that
`Config.allowed_tools` is empty. Both are load-bearing: an entry in
`allowed_tools`, an allow rule in a settings file, or
`permission_mode="bypassPermissions"` auto-approves a call *before* a
`can_use_tool` callback runs. The SDK signals this with
`CanUseToolShadowedWarning`. A PreToolUse hook is not shadowed.

**Telegram allowlist** (`assistant/telegram.py`). Messages from any user id not
in `TELEGRAM_ALLOWED_USER_IDS` are dropped before the model is invoked.
Numeric ids only — usernames are re-assignable.

**Pairing** (`assistant/pairing.py`). With `TELEGRAM_ALLOWED_USER_IDS` empty
and no owner paired before, the process logs a six-digit code from
`secrets` and the first *private* chat that sends it becomes the owner,
saved to `state/owner.json`. The argument for it: reading the process log
needs the same access as editing the allowlist, so pairing hands nobody a
power they lacked. What holds it to that:

- the code exists only in the log: never sent to Telegram, never in
  `memory/raw`, never in a prompt; the stranger's text is compared with
  `hmac.compare_digest` and dropped;
- single use: the first success closes pairing for the install, a restart
  does not reopen it while `state/owner.json` holds an id;
- five wrong six-digit guesses close it until restart; text that is not six
  digits costs no attempt and gets one fixed hint per sender per minute;
- the code rotates after 10 minutes;
- group chats cannot pair;
- `state/` is unreadable and unwritable to every tool (`_SECRET_DIRS`);
- a non-empty `TELEGRAM_ALLOWED_USER_IDS` replaces the paired owner rather
  than adding to it, so the options stay the one place to revoke access.

What it does not cover: an install that sits unpaired with its log readable
by someone who should not own it. Pair right after the first start, or set
the ids in the options.

**systemd sandbox** (`deploy/paia.service`). The same shape enforced
by the kernel rather than by our own code: `ReadOnlyPaths` covers the whole
install, `ReadWritePaths` grants only `memory/` and `state/`. If the policy
layer has a bug, the process still cannot write its own code or unit file.

**Audit log** (`state/audit.log`). Every decision, allowed and denied. A run of
denials against paths the assistant has no business touching is what an
injection attempt looks like from outside.

## Voice messages

Audio never leaves the machine. faster-whisper runs the model locally on CPU;
no transcription service, no API key, nothing uploaded. The one network call is
a one-time fetch of the model weights from HuggingFace into `models/`, which
happens at install and never again — `Transcriber` can be constructed with
`offline=True` to make a missing model fail loudly rather than reach for the
network mid-conversation.

The downloaded audio is written to a `tempfile.TemporaryDirectory` outside the
install, not into the tree, so it is never at a path the model's own file tools
are permitted to read, and it is removed even if transcription raises.

The allowlist check happens before the download: an unauthorised sender costs
us nothing, not even a file fetch. Duration and size are capped
(`max_voice_seconds`, `max_voice_bytes`) so a long recording cannot pin the CPU
— transcription runs at roughly a third of real time on 2 vCPU.

A transcript is treated as ordinary user input, exactly like typed text, since
it comes from the one allowlisted sender. Note the implication: audio *forwarded*
to you from someone else becomes user-level input if you pass it on. Prefer
typing when relaying something you did not say yourself.

## Attachments

The filename comes from the sender and is treated as hostile input:
`_safe_name` strips every path component, replaces anything outside
`[A-Za-z0-9._-]`, and truncates the stem while keeping the extension — so
`../../.env` cannot escape `inbox/`, and the extension that decides whether a
file is accepted cannot be truncated away.

Type and size are checked *before* the download, against an allowlist. An
executable or archive costs no bandwidth and never touches disk.

`inbox/` is readable by the model and writable only by the transport: the
policy still confines the model's own writes to `memory/wiki/`. Attachments are
swept after `inbox_retention_days` so a document received once does not stay
readable forever.

**A PDF is untrusted content.** Anything inside it — including text invisible
to a human reader — is data, not instruction. This is the same channel the
`context/` injection test exercised, and it is the most likely way a hostile
document reaches the assistant. The defence is `INJECTION_GUARD` plus the
policy: even a fully persuasive document cannot make the assistant write
outside `memory/wiki/`, read `.env`, or reach a shell.

## Operational rules

- `.env` is `chmod 600` and never committed. Rotate the Telegram token if it
  ever reaches a log or a terminal.
- Never add `Bash` to `Config.tools` without first deciding what a compromised
  message may run. There is no safe generic answer.
- Never add a tool to `Config.allowed_tools`. That field exists to be empty;
  use the policy.
- Re-run `tests/test_policy.py` after touching `policy.py`.

## Routines and persistence

A routine is a prompt that runs on its own. Being able to create one already
active would mean recurring execution without a human in the loop — which is
precisely the persistence the rest of this policy exists to deny. It would turn
a single successful injection into a standing foothold: a document that
convinces the assistant once could leave behind something that runs every hour
afterwards.

So the write is allowed and the activation is not. `routines/` joins
`memory/wiki/` in `_WRITE_ROOTS`, but any Write or Edit whose content matches
`enabled:\s*true` is refused. Activation happens through `/enable`, handled in
`assistant/main.py` in Python — the model has no way to emit a Telegram command
on the owner's behalf.

Scanning the written text is not enough on its own. It sees content going in,
not the file already on disk, so editing the *body* of a routine that is
already enabled carries no `enabled: true` and would pass — and the next tick
runs the new body. Same foothold, reached by editing instead of creating. So
an enabled routine is frozen: `_is_enabled_on_disk` refuses every write to a
routine file that currently reads `enabled: true`, and an unreadable file
counts as enabled. Changing a live routine takes `/disable`, the edit, a read,
and `/enable` — three human acts. `set_enabled` writes from Python and never
passes through the policy, so the commands keep working on a frozen file.

A routine still runs as the same agent under the same policy, so it can do
nothing the assistant cannot already do: no shell, no writes outside
`memory/wiki/` and `routines/`, no reading `.env`. What it adds is repetition
and an outbound channel, which is why activation is the boundary worth
guarding.

## Briefs: proposed by the model, accepted by the owner

A brief in `context/` is a rule every later turn follows, so the model writing
one would be the same standing foothold as a live routine. It still cannot:
`context/` is not in `_WRITE_ROOTS`, and in the container `ROOT/context` is
root-owned and read-only.

What it can do is propose. `memory/wiki/proposals/<brief>.md` is inside the
wiki write root, and `_check_wiki_write` narrows it further: the file must be
named after a brief that exists in `context/`, one level deep, Markdown. The
owner decides with `/review`, `/accept` and `/reject`, handled in
`assistant/main.py` before the model sees the message
(`assistant/proposals.py`):

- `/review` sends the diff and stores the proposal's sha256 in
  `state/reviewed.json`, which no tool can read or write;
- `/accept` applies only a proposal whose current hash is the one reviewed,
  so an edit after the owner read it — an injection racing the owner —
  means review again;
- the text must be a whole brief with its own `name:` in the frontmatter.

`/accept` writes the add-on config share's `context/`, which `own()` hands
to the `assistant` account; the model cannot reach it, because every path
outside ROOT is secret to policy. The supervisor, as root, syncs the share
into `ROOT/context` when it changes and resets its modes. Outside the add-on
there is no target: `/accept` says so, and `context/` stays read-only to
the process under systemd as before.

What this does not stop: an owner accepting a diff they did not read. The
diff is the defence; it is shown in full.

## Skills

A skill is a method the assistant reads and then follows: how a price is
verified, what counts as proof, what never to do while checking one. It is
loaded into the model's context on invocation, which puts it in the same class
as a routine's prompt — text with authority over what happens next — and gives
it the same failure mode. A skill the model could write *and* adopt is a way
to write itself a new rulebook, and an injected page that talks the model into
authoring one would be laundering an instruction into something that looks
like method.

So the split is the routine split again: writing is allowed, enabling is not.
`.claude/skills/` joins `_WRITE_ROOTS`; `.claude/` itself does not, because
`settings.json` there carries the enabled plugins and the permission rules the
SDK reads at connect time. Writes under the skills folder must be Markdown — a
bundled script would be model-authored code, and while there is no shell here
to run it, keeping it out means that stays true if one ever appears.

Enabling is `ASSISTANT_SKILLS` in `.env`: a list of names, read at startup,
passed to the SDK as `ClaudeAgentOptions.skills` and to `Policy` as the set it
checks. Editing `.env` is something the model cannot do and cannot read, so
the act is the owner's by construction — one step heavier than `/enable` on a
routine, deliberately, because a skill changes how the assistant works rather
than when.

The SDK's own list is a **context filter, not a gate**: an unlisted skill is
hidden from the listing, but the model can still name one it learned about by
reading the folder. `Policy._check_skill` is the gate — every `Skill` call is
matched against the same set, and a call that names nothing at all is refused
rather than guessed at. A live skill is also frozen against edits, for the
reason an enabled routine is: the model rewriting the method it will follow
tomorrow is the same foothold reached by a different door.

Passing `skills` as a list matters beyond scope. `skills="all"` makes the SDK
append a bare `Skill` to `allowed_tools`, and an entry there auto-approves
ahead of `can_use_tool`; a list produces `Skill(<name>)` rules instead, which
do not. Either form still passes through the `PreToolUse` hook, which is not
shadowed — but the narrower one keeps `allowed_tools` empty in spirit as well
as in the config file.

In the container, skills the image ships are replaced at every start
(`SHIPPED_WIN` in `docker/entrypoint.py`); skills that exist only on the
volume — the ones the model wrote — are kept. The repo is where a shipped
skill changes, so an edit the model made on the volume to one of those is
lost on restart.

One method is enforced in code rather than left to the text: the `coach_*`
tools are refused in a turn until the model has `Read` a file under
`.claude/skills/coach/references/section-11/` in that same turn
(`Policy.begin_turn`, called by `Brain.ask`). This is a quality gate, not
a security one — it keeps the coaching protocol in front of the model after
context compaction — but it lives here because it is the policy's to hold.

`coach_plan_workout` is the coach's one write: it puts a planned workout on
the Intervals.icu calendar, which Intervals pushes to Garmin Connect and the
head unit. What it can touch is held in `assistant/intervals.py`: an event body
built from four named fields (date, type, name, description — category,
athlete and tag are set in code), dates from today to 28 days ahead, a rewrite
(`PUT`) only of an event whose `external_id` carries the `paia-` tag
it puts on everything it writes, never one already ridden, and no delete call
anywhere in the module. When the owner's own decision is required is held in
`Policy._check_plan`: a call with `confirm` is refused unless the same
workout, by content, was previewed without `confirm` in an earlier turn (so a
message of theirs always sits between preview and write), each preview allows
one write, and every call is refused in a routine turn (`Brain.ask(...,
routine=True)` from `_run_routine`). The previews live in process memory; a
restart means previewing again.

Routine mail (`channel: email`) goes out through `gws` as the logged-in
Google account (`GwsMailer` in `assistant/channels.py`). It is transport
code: it runs after the model's turn, sends to the single `MAIL_TO` address
from the environment, and is not in `gws.py`'s `_ALLOWED_CALLS` or any tool
the model holds. The model still cannot send mail.

## The scraper worker

`scraper/` is a second process that fetches web pages properly: a request with
Chrome's TLS fingerprint, then a headless browser, then a headed one, then a
headed one driven through `/dev/uinput`. The assistant reaches it through three
in-process tools in `assistant/scrape.py` -- `page_read`, `page_price`,
`page_sources` -- mounted as `mcp__scrape__*`.

It exists because `WebFetch` is a plain GET with a Python TLS fingerprint and
`memory/wiki/price-sources.md` records what that gets you. It also widens the
attack surface in three specific ways, and each has an answer.

**SSRF, which the worker makes worse before it makes it better.** `check_host`
already refused a URL resolving to a private address. Route a fetch through a
worker and that guarantee inverts: the gate sees the worker's own loopback
address, approves it, and the worker -- which sits *inside* the home network --
opens whatever it was handed. Two independent checks, and the second is the one
that always runs:

- `Brain._gate` in `assistant/agent.py` now runs `Policy.check_host` for
  `mcp__scrape__*` as well as `WebFetch`, on the `url` the model passed.
- `scraper/safety.py` re-resolves and re-checks the target itself, before the
  socket opens and again after every redirect. Redirects are followed by hand
  rather than by the HTTP client precisely so each hop passes this. Routines
  reach the worker without going through the assistant's gate at all, so the
  worker's own check is the only one guaranteed to happen.

`WEB_FETCH_ALLOWED_DOMAINS`, when set, narrows the scraper as well. One
allowlist for every way out of this process, or the narrower path is theatre.

**It reads, and there is no code path that does anything else.** `safety.check`
refuses any method but GET and HEAD before a socket is opened, so "buy nothing,
add nothing to a cart, fill in no form" is a property of the code rather than a
sentence in a skill the model reads. Tier 4 moves a real pointer, which is the
one place that guarantee could be undone by accident, so its click targets are
a short named allowlist of challenge and consent widgets -- not "anything
clickable" -- and `scraper/tests/test_worker.py` asserts no cart, buy, checkout,
submit or order selector is ever in it. `policy.py` pins the three tool names,
so registering a fourth in `scrape.py` is not enough to make it callable: the
same two-lock arrangement `gws.py` has.

**A rendered page is more dangerous than a `WebFetch` summary, not less.**
`WebFetch` handed the model a model-written précis. The worker hands it the
actual text of a page that ran the shop's JavaScript -- more of it, at higher
fidelity, chosen by a stranger with a commercial interest. Everything returned
carries the `_UNTRUSTED` banner from `assistant/scrape.py`, which is sharper
than the calendar's for that reason, and `INJECTION_GUARD` already covers the
rest. Nothing read this way is ever instruction.

**Where the state lives, and who may write it.** Recipes -- the per-domain tiers
and selectors -- are under `state/scraper/`, which `policy.py` makes unreadable
and unwritable to the model. That is deliberate: a selector is machine state
the worker owns, so no injected page can rewrite one, and the model never edits
one by hand. `memory/wiki/price-sources.md` stays the human-readable mirror the
assistant does write.

**The one place a model touches the deterministic path.** When a page opens but
no price can be read out of it, `scraper/repair.py` asks the model for a
replacement selector. It is fenced: the input is a pruned DOM (money-looking
text and its ancestor chain, typically under 10 KB of a 2 MB page); the output
must be a *selector*, never a price, so a hallucinated number produces nothing;
the proposal is re-run against the same page and must yield text that is
recognisably money -- a currency marker, not merely a plausible magnitude,
because "482 reviews" parses to 482.0 and 482.0 is a fine price; the previous
recipe is kept as `.json.prev`; and a daily budget caps how often it can
happen. It shells out to the `claude` CLI with an argv list, never a command
line, so nothing in the page text can extend the command.

**What it still cannot do.** Bind anything the internet can reach (no published
port on either deployment), write outside `state/scraper/`, run as root, or take
the assistant down with it -- a wedged Chromium is a `Restart=always` on a
separate unit, and an empty `SCRAPER_URL` simply means the page tools are not
offered.

## SSRF

Opening `WebFetch` on a VPS means the assistant can be asked to fetch a URL,
and a hostname is not a promise about where it points. A public name can
resolve to `127.0.0.1`, to the provider's `10.x` network, or to
`169.254.169.254` — the cloud metadata endpoint that hands out the instance's
credentials to anything that asks. "Open this link for me" is precisely how an
agent gets used as a proxy into a network it should not reach.

`Policy.check_host` resolves the hostname and rejects the fetch if *any*
returned address is private, loopback, link-local, reserved, multicast or
unspecified. Requiring all addresses to be public is what makes DNS rebinding
awkward rather than trivial. Resolution runs in a thread: it blocks, and the
Telegram long-poll must not stop for a slow lookup.

This is a check on where the name points, not on whether the site is
trustworthy. Nothing fetched is ever instruction — see the injection guard.

## Known gaps

- The assistant can still *say* anything it knows in a Telegram reply. The
  defence against exfiltration-by-reply is that it cannot read secrets in the
  first place, not that its output is filtered.
- `WebSearch` queries are not inspected, and with `WebFetch` now open, a URL is
  a wider channel out than a query. What limits exfiltration is that the model
  cannot read `.env` or `state/` at all — not that its outbound traffic is
  filtered. Anything in `memory/wiki/` or in the conversation could in
  principle leave this way.
- The SSRF check resolves the hostname, but the fetch resolves it again. A name
  that changes answer between the two lookups is not caught. The scraper worker
  narrows this without closing it: it re-checks at its own end and at every
  redirect, so a rebind has to win two races rather than one.
- Tiers 3 and 4 run a real browser with a real GPU context and, for tier 4, a
  virtual input device the kernel treats as a keyboard. That is a materially
  larger process to have on the network than an HTTP client, and a Chromium RCE
  would land in a container that can write `/dev/uinput`. What bounds it is
  that the container publishes no port, runs as a non-root user, and can write
  nothing but `state/scraper/` -- not that Chromium is trusted.
- A repaired selector is validated against exactly one page: the one that
  failed. A selector that is right for that page and wrong for the rest of the
  domain passes, and shows up later as a wrong price rather than as an error.
  The `.json.prev` rollback exists for this case.
- No rate limiting. One authorised user bounds the blast radius, but a
  compromised account could run up spend.
