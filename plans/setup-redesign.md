# Setup redesign: from four manual chores to two fields and a chat

Status: plan, not started. Written 2026-10-30.

## Why

Installing the add-on today takes four manual steps, and the last of them comes back over and over:

1. **LLM.** Three credential fields, plus OpenRouter extras. Setting two of them is an error. `claude setup-token` needs a laptop with the CLI on it.
2. **Telegram.** Create the bot with @BotFather, look up your own id with @userinfobot, then type it into a list field. The allowlist has to exist before the bot can see anyone.
3. **Briefs.** Mount `addon_configs/<hash>_paia` over Samba, edit the `TO FILL` lines, restart, or run `scripts/onboard.py --addon` from a laptop.
4. **Brief changes later.** The coach intake ends with "copy these lines into `context/training-rules.md`" (`.claude/skills/coach/SKILL.md:281-284`), because the model cannot write `context/`.

Target state:

- Options ask for **three things**: provider, credential, bot token.
- The owner **pairs** by sending a code from the Log tab.
- The bot **interviews** the owner on first contact.
- Every brief change is a **proposal the owner accepts in chat** with a transport command the model cannot call. This is the same pattern as `/enable` for routines.

## Invariants that must survive (SECURITY.md)

- The model never writes `context/`, `.env`, `state/` or the allowlist. Policy (`assistant/policy.py`) stays the gate. The new write path is **transport Python only**, triggered by a human command, as with `/enable` (`assistant/main.py:130-200`, docstring at :132-136).
- In the container, `ROOT/context` stays root-owned and read-only (`docker/entrypoint.py` `own()` :398-422). Only the root supervisor writes it.
- The allowlist is still the whole access model. Pairing must never add an id without a secret that only an HA admin can see.
- No new listening port. No Ingress.

---

## Phase 0 — Discovery (done 2026-10-30, consolidated)

### Allowed APIs and locations (verified by reading)

| Concern | Exact location |
|---|---|
| Incoming message shape | `assistant/telegram.py:43-49` `Incoming(chat_id, user_id, text, voice=None, document=None)` |
| Allowlist gate | `assistant/telegram.py:101-103`, `self._allowed` frozenset set in `__init__` :53-56 and never reassigned |
| Sending | `Telegram.send(chat_id, text)` :140-162. It never raises |
| Bot menu | `Telegram.set_commands(list[tuple[str,str]])` :212-234 |
| Token redaction in logs | `telegram._redact(exc)` :263 |
| Command registry | `assistant/main.py:114-120` `COMMANDS`, plus `HELP` :122-127. `test_routines.py:185-197` asserts every entry is handled |
| Command dispatch | `main._command(cfg, telegram, brain, delivery, incoming) -> bool` :130-200. Parse at :138-140 |
| Owner chat for delivery | `main.py:53` `chat_id = min(allowed)`, passed to `Delivery.__init__(telegram, chat_id, smtp, ...)` `assistant/channels.py:82`. Stored as `self._chat_id` |
| Flag-flip writer to copy | `assistant/routines.py:117-122` `set_enabled` |
| Config | `assistant/config.py` `Config` frozen dataclass :28. `load()` :268. Ids parsed at :322. `_require` :21-25 |
| Option → env map | `docker/entrypoint.py:89-119` `OPTION_ENV`. `_flatten` :131-146. Checks :182-219 |
| Brief sync | `entrypoint._seed_briefs()` :299-317 runs `_sync(/config/context → ROOT/context)` at start only |
| Supervisor loop | `entrypoint.supervise()` :480-524. Ticks every 2 s, runs as root |
| Process user dirs | `KEEP`/`WRITABLE` :61-82. `state/` is in it and is unreadable to the model (`policy._SECRET_DIRS` :88) |
| Wiki write rules | `policy._check_wiki_write` :554-591. A `Write` to an existing non-empty page is refused, so `Edit` is required |
| Write roots | `policy._WRITE_ROOTS` :56-58 = wiki, routines, scripts/proposals, .claude/skills |
| TO FILL token and fill logic | `scripts/onboard.py:66` `_TOKEN`, `unfilled` :195, `fill` :199, `Briefs.remaining` :261 |
| Turn entry | `Brain.ask(prompt, timeout=None, routine=False)` (`agent.py:316`), same signature in `openrouter.py:735` |
| Suggested onboarding prompt | `ONBOARDING.md:136-142` |
| VPS sandbox | `deploy/paia.service:25-26`. `context/` is **not** in `ReadWritePaths` |

### Test style
Tests are plain scripts, not pytest. Each has a `check(name, ok, detail)` helper and a `FAILURES` list, and runs as `.venv/bin/python tests/test_X.py`.
- Entrypoint scaffold: `tests/test_entrypoint.py:13-33`. It puts `docker/` on `sys.path` and repoints `E.ROOT`, `E.CONFIG` and `E.OPTIONS`.
- Command scaffold: `tests/test_routines.py:26-45, 80-95`, using `FakeTelegram` and `msg()`.
- Real `Telegram` with a stubbed `_call`: `tests/test_routines.py:199-224`.

### Gaps found (no coverage today)
- Nothing tests `poll()` dropping strangers or `build_env()`.
- `config.load()` raises a bare `ValueError` on a non-numeric id.
- `_command` does not strip a `/cmd@botname` suffix.

### Anti-patterns (things that do not exist)
- `gws auth login` has **no** `--no-browser` and no device-code flag (gws 0.22.5 `--help`). Do not design around one.
- There is no first-contact hook in the brain. Onboarding must be started by the transport.
- `Config` is frozen. Do not mutate `cfg.allowed_user_ids`. Keep mutable allowlist state on `Telegram`.
- The briefs are **not** in the system prompt. CLAUDE.md imports only IDENTITY and ROT, and the model reads `context/*.md` with Read on demand. So a brief can change under a running process with no restart.

---

## Phase 1 — Options: provider + one credential (done 2026-10-30)

**Implement**

1. `addon/config.yaml`:
   - Add `llm_provider: list(anthropic_api|claude_subscription|openrouter)?` and `llm_credential: password?`.
   - Keep `claude_oauth_token`, `anthropic_api_key` and `openrouter_api_key` in the schema for existing installs.
   - Remove them from `options:` defaults so new installs don't see them.
2. `docker/entrypoint.py` `build_env()`. When `llm_provider` is set, map `llm_credential` to exactly one env var:
   - `anthropic_api` → `ANTHROPIC_API_KEY`
   - `claude_subscription` → `CLAUDE_CODE_OAUTH_TOKEN`
   - `openrouter` → `OPENROUTER_API_KEY`
   - Setting both the new pair and any legacy field is a `SystemExit` that names both. Legacy-only installs behave exactly as today.
3. Prefix sanity check, offline only, with a `SystemExit` that names the fix:
   - `sk-ant-api` with `claude_subscription` → "this is an API key: set llm_provider to anthropic_api".
   - Same idea for `sk-ant-oat` and `sk-or-`.
   - No network validation in this phase. A boot without internet must not fail.
4. Default `model` per provider when empty:
   - Claude providers → the current default in `config.py:323`.
   - `openrouter` → still required. Keep the existing `SystemExit` at `config.py:280-286`.
5. ~~Relax the empty-allowlist check~~ moved to Phase 2: `config.load()` still `_require`s the ids, so relaxing only the entrypoint would crash-loop the child.
6. Also added: `build_env` refuses OpenRouter with a non-slug `model`, so the error shows up in the Log once instead of as a restart loop.

**Verify**
- New `tests/test_entrypoint.py` cases, following its scaffold:
  - each provider maps to exactly one var;
  - new pair plus a legacy field exits;
  - a wrong prefix exits and the message names the right provider;
  - legacy-only options produce the same env as before (snapshot one);
  - an empty allowlist does not exit.
- `grep -n "llm_provider" addon/config.yaml docker/entrypoint.py addon/DOCS.md` shows all three.

**Guards**
- Do not remove the legacy fields from `schema:`. The Supervisor rejects saved options that no longer validate.
- Do not log the credential, not even masked beyond its prefix.

---

## Phase 2 — Telegram pairing (done 2026-10-30)

Deviations from the steps below, as built:
- A non-empty option allowlist **replaces** `owner.json` rather than being unioned with it, so the options stay the one place to revoke access.
- Text that is not six digits costs no attempt ("hi" from the owner must not burn the five).
- An expired code rotates (a new one is logged) instead of closing.
- The pairing message is yielded as `Incoming(paired=True, text="")`. `_pump` sets the delivery chat and greets; Phase 4 hooks onboarding there.
- Also fixed: `tests/test_openrouter.py` expected the old "cycling coach" description. `test_policy.py` "public host allowed" fails only without DNS: environmental, left as it is.

**Implement**

1. New `assistant/pairing.py`, pure with no I/O except one state file:
   - `Pairing(state_dir: Path, ttl_seconds=600, max_attempts=5)`.
   - `code` is 6 digits from `secrets.randbelow`, generated once per process start.
   - `try_pair(user_id, text) -> bool` uses a constant-time compare (`hmac.compare_digest`). A wrong code counts an attempt. After `max_attempts` or once the TTL expires, pairing is closed until restart.
   - `owners() -> frozenset[int]` reads `state/owner.json` as `{"user_ids": [..], "paired_at": iso}`.
   - `save(user_id)` writes atomically (tmp + `os.replace`).
   - Pairing is **open only while the option allowlist and `owner.json` are both empty**. After the first success it closes for good. More users come only through the option.
2. `addon/config.yaml`: `telegram_allowed_user_ids` becomes optional (`- str?`, default `[]`), following the `web_fetch_allowed_domains` pattern. `docker/entrypoint.py` `build_env`: an empty `TELEGRAM_ALLOWED_USER_IDS` no longer exits (an empty bot token still does). Add an entrypoint test for it.
   `assistant/config.py`:
   - `TELEGRAM_ALLOWED_USER_IDS` becomes optional.
   - A non-digit raises `SystemExit` with the @userinfobot hint instead of a `ValueError`.
   - Union with the `owner.json` ids is done in `main.run()`, not in `load()`, so `Config` stays a pure env read.
3. `assistant/telegram.py`:
   - `self._allowed` becomes a `set[int]`.
   - Add `allow(user_id)` and an optional `pairing` constructor argument.
   - In `poll()` at :101-103, an unknown sender while pairing is open:
     - If `try_pair` succeeds: `allow()`, `send` "paired", yield nothing.
     - If it fails: `send` one fixed line ("Send the code shown in the add-on Log tab"), at most once per sender per 60 s.
   - When pairing is closed, behave exactly as today: drop and log.
   - The text never reaches the brain in any of these cases.
4. `assistant/main.py`:
   - On start with an empty union, log the code **loudly**, alone on its own line, with its expiry. That's the only place it appears.
   - `Delivery`'s chat id must follow the pairing. Add `Delivery.set_chat(chat_id)` to `assistant/channels.py`, call it on pairing success, and skip the scheduler until a chat exists. Copy the guard style from `Delivery.available`.
5. `docker/entrypoint.py`: nothing more. `state/` is already in `WRITABLE`.
6. `deploy/paia.service`: nothing. `state` is already in `ReadWritePaths`.

**Verify**
- New `tests/test_pairing.py`:
  - right code pairs and persists;
  - wrong code ×5 closes pairing;
  - expired code is refused;
  - a second stranger after pairing is dropped without a reply;
  - a non-empty option allowlist means pairing never opens.
- Telegram `poll()` with `_call` stubbed (scaffold `test_routines.py:199-224`):
  - a stranger is dropped, no yield;
  - a stranger with the right code is allowed, then their next message yields.
- `policy` test: Read on `state/owner.json` is refused. This should already be true; add an assertion.

**Guards**
- No code in the bot reply, in `memory/raw`, or anywhere the model can read.
- Do not pair from a group chat. Require `chat.type == "private"`, which means adding `chat_type` to the fields `poll()` reads.

---

## Phase 3 — Brief proposals, accepted in chat (done 2026-10-30, add-on only)

As built, against the steps below:
- **VPS: not wired, by decision (2026-10-30).** `ASSISTANT_CONTEXT_TARGET` is set only by the add-on entrypoint. Without it, `/accept` replies that context/ is read-only and to copy by hand. `deploy/paia.service` is unchanged. Step 5 below is deferred.
- The fingerprint lives in `state/reviewed.json`. The `TO_FILL` regex is copied into `assistant/proposals.py` (`open_blanks`), ready for Phase 4.
- `scripts/distill.py` now leaves `proposals/` alone.

**Shape**
- The model writes a **complete proposed brief** to `memory/wiki/proposals/<brief>.md`, where `<brief>` is the name of an existing file in `context/`. A new proposal is created with `Write` and revised with `Edit`, which the existing wiki rule already enforces.
- The transport owns three commands:
  - `/proposals` lists open proposals.
  - `/review <name>` sends a unified diff (`difflib.unified_diff`) of current against proposed and stores the proposal's sha256 in `state/reviewed.json`.
  - `/accept <name>` applies only if the file's current hash equals the reviewed one. Otherwise it replies "changed since review, /review again". This gate copies `coach_plan_workout`'s preview/confirm.
  - `/reject <name>` deletes the proposal.
- Apply goes to the **authoritative copy** and then deletes the proposal:
  - Container: `/config/context/<name>`.
  - VPS: `ROOT/context/<name>`.

**Implement**

1. `assistant/policy.py`, inside the wiki branch: a path under `memory/wiki/proposals/` must be `<name>.md`, and `<name>.md` must exist in `ROOT/context/`. Anything else is refused with a reason. Add the matching tests in `tests/test_policy.py`.
2. New `assistant/proposals.py`:
   - `list_open(root)`, `diff(root, name)`, `digest(path)`, `apply(cfg, name)`.
   - Validation before apply: non-empty, starts with `---\n`, has a `name:` frontmatter line equal to the brief's.
   - Bump `updated:` with the same regex as `scripts/onboard.py:214` `bump`. Copy it, don't import from `scripts/`.
3. `Config`: add `context_target: Path`. It is `/config/context` if that directory exists and is writable, else `root/"context"`. Read it from a new env var `ASSISTANT_CONTEXT_TARGET`, set by `entrypoint.build_env`.
4. `docker/entrypoint.py`:
   - chown `/config/context` to `assistant`, 0750 for directories and 0640 for files, in `own()`. The model still cannot reach it, because `policy._is_secret` treats paths outside ROOT as secret.
   - In `supervise()`, every tick: if any file under `/config/context` has an mtime newer than the last sync, run `_sync(CONFIG/"context", ROOT/"context", "context")` and re-apply root 0444 on `ROOT/context`.
   - `ROOT/context` stays root-only. This also removes "restart after editing on the share".
5. VPS: add `/opt/paia/context` to `ReadWritePaths` in `deploy/paia.service`, with a comment. The model is still blocked by policy, and SECURITY.md must say the OS layer is now policy-only for `context/` on the VPS.
6. `assistant/main.py`: register the 4 commands in `COMMANDS`. Strip an `@botname` suffix in the parse at :138-140.
7. Text changes, edited directly in the repo. The freeze applies to the running model, not to us:
   - `ROT.md` "Who writes what": `context/` is still the owner's. You change it only by filing a proposal, and the owner accepts it. Exempt `proposals/` from the index rule.
   - `.claude/skills/coach/SKILL.md:281-284`: "file the stable lines as a proposal for `training-rules`, tell them `/review training-rules`".
   - `CLAUDE.md` layout line for `context/`.
   - `SECURITY.md` new section, after "Routines may be written but not activated".
8. `scripts/distill.py`: confirm it skips `memory/wiki/proposals/`. If it doesn't, exclude the folder.

**Verify**
- `tests/test_proposals.py`:
  - diff output;
  - accept without review is refused;
  - accept after the file changed is refused;
  - accept writes the target, bumps `updated:` and deletes the proposal;
  - reject deletes;
  - bad frontmatter is refused.
- Policy tests: a proposal for an existing brief is allowed; an unknown name, a subfolder or a non-`.md` file is refused; a second `Write` is refused.
- Entrypoint test: after an mtime bump in `/config/context`, `supervise`'s sync helper (factor it out as `_sync_briefs_if_changed()`) updates `ROOT/context`.
- `grep -rn "copy into\|may want to copy" .claude/skills ROT.md` → none.

**Guards**
- The transport never applies a proposal on the model's say-so. Only the 4 commands touch it.
- Do not widen `_WRITE_ROOTS`. Do not make `ROOT/context` writable in the container.

---

## Phase 4 — Onboarding starts itself

**Implement**

1. Move the interview prompt from `ONBOARDING.md:136-142` into `assistant/onboarding.md`. It is English and shipped with the code. Rewrite it so that:
   - it runs the topics in order;
   - for each brief, it reads `context/<brief>.md`, asks only about the `TO FILL` lines and what each list needs, files **one proposal per brief** plus the wiki lists, and ends each topic with "`/review <brief>`";
   - for training, it follows the coach skill intake rather than restating it.
2. `assistant/main.py`:
   - On pairing success (Phase 2), send a welcome and then `brain.ask(onboarding_prompt)`.
   - New command `/onboard` re-runs it any time.
   - At start, if the owner is already known but `TO FILL` lines remain, do **not** auto-run. `/help` mentions `/onboard` while they remain.
   - Count with the `_TOKEN` regex from `scripts/onboard.py:66`, copied into `assistant/proposals.py`.
3. `scripts/onboard.py`:
   - Remove `--addon` mode (`addon_options`, `_ADDON*`, the branches at 627, 635, 667-669).
   - VPS mode stays as it is.
   - Update `tests/test_onboard.py`.

**Verify**
- Pump test with `PollingTelegram` (`tests/test_voice.py:103-125`): a pairing message triggers exactly one `brain.ask`, with the onboarding prompt.
- `/onboard` is handled, and `COMMANDS` coverage passes.
- `grep -n "\-\-addon" scripts ONBOARDING.md addon/DOCS.md` → none.

**Guards**
- The onboarding prompt is not a routine. It runs as a normal turn, so `coach_plan_workout` gates and similar stay as they are.
- Do not put owner name or language in a proposal. Those stay options.

---

## Phase 5 — Docs and release

- `addon/DOCS.md`:
  - Install becomes: bot token + provider + credential → start → copy the code from Log → send it to the bot → answer its questions.
  - Delete "Filling in your briefs" as a required step. Keep the share as an optional power-user path, now with no restart needed.
  - Keep "Moving an existing instance in".
- `ONBOARDING.md`: rewrite parts 2–3 around pairing, `/onboard` and `/review`/`/accept`.
- `CLAUDE.md` "Current state": one sentence on proposals.
- `addon/CHANGELOG.md` entry. The version bump is done by the pre-commit hook.

---

## Phase 6 — Google login (research gate, optional)

Facts: there is no device flow (Phase 0). The options that exist are:
- `gws auth export` on a machine with a browser, plus `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`;
- a service account.

Candidate design:
- The owner runs `gws auth login && gws auth export > gws.json` on a laptop and drops `gws.json` into `/config/import/gws/`.
- `entrypoint.import_state()` moves it to `/data/home/.config/gws/` with mode 0600 and sets the env var.

Before planning:
- web-fetch the gws README and confirm the `export` output format and that refresh works from it;
- confirm the image's gws version matches 0.22.5.

Do not route credentials through Telegram: Telegram stores files on its servers.

---

## Final phase — Verification

1. All suites: `for t in tests/test_*.py; do .venv/bin/python "$t" || echo "FAIL $t"; done`. The one `test_policy.py` failure that already exists (noted 2026-10-30) must be understood and fixed or noted, not masked.
2. Anti-pattern greps:
   - `grep -rn "frozenset" assistant/telegram.py` → no allowlist frozenset left.
   - `grep -rn "context/" assistant/policy.py`: `context` must not be in `_WRITE_ROOTS`.
   - `grep -rn "copy into .*context" .` → none.
3. Manual run on HA:
   - fresh install with only the 3 fields;
   - pair;
   - onboarding asks;
   - `/review training-rules` shows a diff;
   - `/accept` applies, and the file on the share changes with no restart;
   - a second phone with the code after pairing gets no reply.
4. Upgrade path: an existing install with legacy credential fields and a filled allowlist boots unchanged, with no pairing code logged.
