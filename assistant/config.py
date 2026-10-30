"""Runtime configuration, loaded from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .channels import SmtpConfig

# Explicit path, not find_dotenv(): the distiller runs from a systemd timer
# with an unrelated cwd, and discovery-by-walking-up would silently find
# nothing. Under systemd the EnvironmentFile has already populated the
# environment, and load_dotenv does not override what is already set.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    root: Path
    smtp: "SmtpConfig"
    bot_token: str
    allowed_user_ids: frozenset[int]
    model: str

    # -- which model ---------------------------------------------------------
    # "claude" runs the Claude Agent SDK, with the CLI, claude-mem and its
    # built-in tools. "openrouter" runs assistant/openrouter.py instead: any
    # model OpenRouter serves with tool calling, the same tools and the same
    # policy gate, no claude-mem. Chosen by OPENROUTER_API_KEY being set, and
    # then `model` is an OpenRouter slug ("vendor/model"), not a Claude name.
    backend: str = "claude"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # What WebSearch asks on the OpenRouter backend: a model that searches the
    # web itself and answers with its sources. Empty means no WebSearch.
    openrouter_search_model: str = "perplexity/sonar"
    # Photos sent in chat reach the model as images. Off for a model that
    # takes no image input, or the provider refuses the whole request.
    openrouter_images: bool = True
    # Ceiling on one reply, tool calls included.
    openrouter_max_tokens: int = 8192

    # -- the owner ---------------------------------------------------------
    # Who the assistant serves. IDENTITY.md, ROT.md and the skills say "the
    # owner" so the repository carries nobody's name; these two make it a
    # person, appended to the system prompt by assistant/agent.py.
    owner_name: str = ""
    # The language of everything the owner reads: chat, Docs, routine output.
    # What the machine reads -- wiki, code, routines -- stays English.
    owner_language: str = "English"

    # How long a conversation stays open after the last message. When it
    # expires the SDK session is closed cleanly, which is what makes
    # claude-mem's Stop/SessionEnd hooks fire and compress the exchange.
    # Too short: memory fragments into useless slivers. Too long: context
    # grows and every turn costs more.
    idle_timeout_seconds: int = 15 * 60

    # Hard ceiling on one turn, so a runaway tool loop can't wedge the bot.
    turn_timeout_seconds: int = 300
    # A routine does a whole job alone -- the Sunday coach report reads a
    # dozen sources and writes a long text -- so it gets its own ceiling.
    routine_timeout_seconds: int = 900

    # The built-in tools that exist at all for this agent. Everything absent
    # here -- Bash above all -- cannot be called even if the policy layer were
    # bypassed, because the model is never offered it.
    tools: tuple[str, ...] = (
        "Read", "Write", "Edit", "Glob", "Grep",
        "WebSearch", "WebFetch", "TodoWrite", "Skill",
    )

    # Deliberately empty. An entry here auto-approves that tool *before* the
    # PreToolUse policy hook runs (the SDK raises CanUseToolShadowedWarning for
    # this), so listing a tool here would punch a hole straight through
    # assistant/policy.py. Permission decisions belong in one place.
    allowed_tools: tuple[str, ...] = ()

    # Belt and braces: these are refused by the policy anyway, but a hard deny
    # means a future SDK default cannot quietly reintroduce them.
    disallowed_tools: tuple[str, ...] = (
        "Bash", "BashOutput", "KillShell", "Task", "NotebookEdit",
    )

    # -- skills ------------------------------------------------------------
    # Which skills in .claude/skills/ are live, by name. A skill is a method
    # written down once and followed every time -- how a price is verified,
    # how a document is filed -- so it is the assistant's default way of
    # working, not an extra.
    #
    # This list is the enable switch, the same shape as `enabled: false` on a
    # routine and DRIVE_ALLOWED_FOLDERS on Drive: the model may write a skill,
    # and it stays inert until the owner names it here. Empty means the SDK is
    # passed an empty list rather than nothing at all -- nothing is "let the
    # CLI decide", which would load every skill the claude-mem plugin ships
    # and bury the two that belong to this assistant.
    skills: tuple[str, ...] = ()

    # -- voice notes (local transcription) --------------------------------
    # The model runs on this machine: no audio leaves the server. "small" is
    # the sensible compromise for Italian on CPU; "base" is faster but gets
    # proper nouns wrong, "medium" wants more RAM than a 4 GB VPS has.
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    # None = automatic language detection, so Italian and English on the same
    # day both work without a config change.
    whisper_language: str | None = None
    # Whisper accepts an "initial_prompt" it uses as context: terms inside it
    # are recognised far better. Without it, English words inside an Italian
    # sentence come out mangled -- "wiki" becomes "a lui chi", "wren" becomes
    # "Ren". Keep the names you say often in here; load() puts OWNER_NAME in
    # front.
    whisper_vocabulary: str = (
        "paia, wiki, memory, claude-mem, Claude, systemd, "
        "Hetzner, VPS, Telegram, wren, repository, commit, deploy, log"
    )

    # A long voice note pins the CPU for a while: on 2 vCPU transcription
    # runs at roughly 1/3 of real time.
    max_voice_seconds: int = 600
    max_voice_bytes: int = 20 * 1024 * 1024  # getFile limit

    # -- scheduled routines -----------------------------------------------
    # How often the scheduler looks at the clock. A minute is cron's
    # granularity; going finer buys nothing.
    scheduler_tick_seconds: int = 60

    # -- attachments -------------------------------------------------------
    # Extensions the assistant may receive. Whitelist, not blacklist: a new
    # type gets added on purpose. Read handles PDFs and images natively, the
    # rest is text.
    allowed_file_types: tuple[str, ...] = (
        ".pdf",
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log",
    )
    max_file_bytes: int = 20 * 1024 * 1024  # getFile limit
    # Attachments stay readable for a few days, so follow-up questions about
    # the same document work without resending it.
    inbox_retention_days: int = 7
    # Read returns an image as one base64 block, and that whole tool result
    # comes back from the CLI as a single JSON line. The SDK refuses a line
    # over its buffer -- 1 MB by default, which a phone photo passes on its
    # own -- and kills the message reader for the rest of the session. Base64
    # costs a third on top, so a max_file_bytes attachment needs ~27 MB;
    # 64 MB leaves room for the JSON around it.
    sdk_max_buffer_bytes: int = 64 * 1024 * 1024

    # Empty = open web, which is the intended default: an assistant that can
    # search but not open the results is not much use. Putting domains here
    # narrows fetching back to those. Either way check_host() applies, which
    # blocks non-public addresses.
    web_fetch_domains: frozenset[str] = frozenset()

    # -- Google Workspace --------------------------------------------------
    # The `gws` CLI (@googleworkspace/cli), which assistant/gws.py calls with
    # an argv list. A bare name resolves through PATH, which is fine under a
    # login shell and not fine under systemd: set GWS_BIN to the absolute path
    # there. Empty disables the calendar tools entirely rather than failing
    # once per morning digest.
    gws_bin: str = "gws"
    # The CLI does a network round trip to Google. Long enough for a slow
    # link, short enough that a hung call cannot eat the turn timeout.
    gws_timeout_seconds: int = 30

    # -- the scraper worker ------------------------------------------------
    # Where scraper/ is listening, e.g. http://127.0.0.1:8099. Empty -- the
    # default -- means the three page tools are simply not offered, the same
    # arrangement as gws_bin above: better than offering a tool that fails
    # every time a price is asked for.
    #
    # This is the assistant's answer to the problem memory/wiki/price-sources.md
    # documents: the built-in WebFetch is a plain GET with a Python TLS
    # fingerprint, and two thirds of the shops worth watching refuse it. The
    # worker climbs from a Chrome-fingerprinted request up to a real browser
    # driven through /dev/uinput, and stops at the first tier that opens the
    # page.
    scraper_url: str = ""
    # Shared secret, not the security boundary -- binding the worker to
    # loopback is. This stops another process on the same host using it by
    # accident.
    scraper_token: str = ""
    # Generous, because a tier-3 or tier-4 fetch means starting a browser and
    # waiting for a challenge to clear -- up to SCRAPER_CHALLENGE_WAIT (25 s)
    # per browser tier, three of them in a row on a domain that resists.
    # Still inside turn_timeout_seconds, so a hung worker cannot eat the turn.
    scraper_timeout_seconds: int = 150

    # Which Drive folders the assistant may read: the ids in
    # DRIVE_ALLOWED_FOLDERS, plus everything beneath them. Drive is the one
    # surface where read-only is not limit enough -- a Drive holds contracts
    # and scans of documents next to the shopping list -- so the folders are
    # named here rather than the whole account being reachable by a search.
    # Empty means the whole Drive, which is what it did before this existed;
    # assistant/gws.py logs a warning when that is the case.
    drive_folder_ids: frozenset[str] = frozenset()

    # The folders Google Docs may be written in: DRIVE_WRITE_FOLDERS, one id or
    # a comma-separated list. A new Doc is created in one of them
    # (drive_create_doc); a Doc in one of them or in any folder beneath one can
    # be added to at its end (drive_append_doc), corrected one exact string at
    # a time (drive_replace_text) or rewritten whole (drive_rewrite_doc),
    # because a trip usually has a folder of its own and a document that can
    # only grow eventually says something false at the top. Nothing outside
    # these folder trees is touched by any of the four, and no file is ever
    # deleted, renamed, moved or shared.
    # Empty -- the default -- means the assistant writes
    # nothing at Drive. With several open the model picks which by id, and only
    # from this list: gws.py refuses any other. Each has to sit inside
    # drive_folder_ids as well, which gws.py checks at the call, so anything
    # written can be read back afterwards.
    drive_write_folders: tuple[str, ...] = ()

    # -- Intervals.icu (the cycling coach) ---------------------------------
    # The API key and athlete id from Intervals.icu > Settings > Developer
    # Settings. Either empty means the six coach tools are not offered, the
    # same arrangement as gws_bin and scraper_url. The key travels only in the
    # auth header assistant/intervals.py builds; the model never sees it,
    # because .env is unreadable to it (see policy._SECRET_NAMES).
    intervals_api_key: str = ""
    intervals_athlete_id: str = ""
    # The day a new watch took over overnight HRV from the previous one.
    # Apple's SDNN and Garmin's rMSSD are different measurements, so HRV and
    # RHR baselines are computed only from this date on. Unset means the
    # baseline uses everything, and the coach says so in every readiness call.
    hrv_cutover_date: date | None = None
    # Where routines with `channel: email` go, sent through the `gws` CLI from
    # the Google account it is logged in as. One fixed address from the
    # environment, never one the model picks. Falls back to SMTP_TO.
    mail_to: str = ""

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def raw_memory_dir(self) -> Path:
        return self.root / "memory" / "raw"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def inbox_dir(self) -> Path:
        return self.root / "inbox"

    @property
    def routines_dir(self) -> Path:
        return self.root / "routines"

    @property
    def skills_dir(self) -> Path:
        return self.root / ".claude" / "skills"


def load() -> Config:
    root = Path(os.environ.get("ASSISTANT_ROOT", Path(__file__).resolve().parent.parent))
    ids = _require("TELEGRAM_ALLOWED_USER_IDS")
    domains = os.environ.get("WEB_FETCH_ALLOWED_DOMAINS", "").strip()
    language = os.environ.get("WHISPER_LANGUAGE", "").strip()
    folders = os.environ.get("DRIVE_ALLOWED_FOLDERS", "").strip()
    write_folders = os.environ.get("DRIVE_WRITE_FOLDERS", "").strip()
    skills = os.environ.get("ASSISTANT_SKILLS", "").strip()
    cutover = os.environ.get("HRV_CUTOVER_DATE", "").strip()
    owner = os.environ.get("OWNER_NAME", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get("ASSISTANT_MODEL", "").strip()
    if openrouter_key and "/" not in model:
        # A Claude name here is the leftover default, and OpenRouter answers
        # it with a 400 on every message. Say so once, at start.
        raise SystemExit(
            "OPENROUTER_API_KEY is set, so ASSISTANT_MODEL must be an OpenRouter "
            f"model slug like vendor/model-name, got {model or 'nothing'!r}"
        )
    try:
        hrv_cutover = date.fromisoformat(cutover) if cutover else None
    except ValueError:
        raise SystemExit(f"HRV_CUTOVER_DATE must be YYYY-MM-DD, got {cutover!r}")
    return Config(
        intervals_api_key=os.environ.get("INTERVALS_API_KEY", "").strip(),
        intervals_athlete_id=os.environ.get("INTERVALS_ATHLETE_ID", "").strip(),
        hrv_cutover_date=hrv_cutover,
        routine_timeout_seconds=int(os.environ.get("ROUTINE_TIMEOUT_SECONDS", "") or 900),
        mail_to=(os.environ.get("MAIL_TO") or os.environ.get("SMTP_TO") or "").strip(),
        scraper_url=os.environ.get("SCRAPER_URL", "").strip().rstrip("/"),
        scraper_token=os.environ.get("SCRAPER_TOKEN", "").strip(),
        root=root,
        smtp=SmtpConfig(
            host=os.environ.get("SMTP_HOST", "").strip(),
            port=int(os.environ.get("SMTP_PORT", "587") or 587),
            user=os.environ.get("SMTP_USER", "").strip(),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=os.environ.get("SMTP_FROM", "").strip(),
            recipient=os.environ.get("SMTP_TO", "").strip(),
            use_tls=os.environ.get("SMTP_TLS", "1").strip() != "0",
        ),
        whisper_model=os.environ.get("WHISPER_MODEL", "small"),
        whisper_language=language or None,
        whisper_vocabulary=", ".join(
            v for v in (owner, Config.whisper_vocabulary) if v
        ),
        owner_name=owner,
        owner_language=os.environ.get("OWNER_LANGUAGE", "").strip() or "English",
        # `*` reads as "anywhere", so it means what empty means: an allowlist
        # holding it literally would match no host and close the web.
        web_fetch_domains=frozenset(
            d.strip().lower() for d in domains.split(",") if d.strip()
        ) if "*" not in domains.replace(" ", "").split(",") else frozenset(),
        bot_token=_require("TELEGRAM_BOT_TOKEN"),
        allowed_user_ids=frozenset(int(x) for x in ids.replace(" ", "").split(",") if x),
        model=model or "claude-sonnet-5",
        backend="openrouter" if openrouter_key else "claude",
        openrouter_api_key=openrouter_key,
        openrouter_base_url=(
            os.environ.get("OPENROUTER_BASE_URL", "").strip().rstrip("/")
            or Config.openrouter_base_url
        ),
        openrouter_search_model=os.environ.get(
            "OPENROUTER_SEARCH_MODEL", Config.openrouter_search_model
        ).strip(),
        openrouter_images=os.environ.get("OPENROUTER_IMAGES", "1").strip() != "0",
        openrouter_max_tokens=int(
            os.environ.get("OPENROUTER_MAX_TOKENS", "") or Config.openrouter_max_tokens
        ),
        gws_bin=os.environ.get("GWS_BIN", "gws").strip(),
        drive_write_folders=tuple(
            f.strip() for f in write_folders.split(",") if f.strip()
        ),
        drive_folder_ids=frozenset(
            f.strip() for f in folders.split(",") if f.strip()
        ),
        skills=tuple(s.strip() for s in skills.split(",") if s.strip()),
    )
