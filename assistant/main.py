"""Entry point: wire the Telegram transport to the brain."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import signal
import time
from datetime import datetime
from pathlib import Path

import tempfile

from . import coach_charts
from . import config as config_module
from . import memory
from . import routines as routines_module
from .agent import Brain
from .channels import Delivery, GwsMailer
from .speech import Transcriber
from .telegram import Incoming, Telegram

# A file name comes from the sender, so it is hostile input: "../../.env" or
# "report.pdf\n" must not be able to escape inbox/ or confuse the prompt.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

log = logging.getLogger("assistant")


async def run() -> None:
    cfg = config_module.load()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    brain = Brain(cfg)
    telegram = Telegram(cfg.bot_token, cfg.allowed_user_ids)
    transcriber = Transcriber(
        model_name=cfg.whisper_model,
        compute_type=cfg.whisper_compute_type,
        download_root=cfg.models_dir,
        language=cfg.whisper_language,
        vocabulary=cfg.whisper_vocabulary,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    chat_id = next(iter(sorted(cfg.allowed_user_ids)))
    mailer = GwsMailer(cfg.gws_bin, cfg.mail_to) if cfg.mail_to else None
    delivery = Delivery(telegram, chat_id, cfg.smtp, mailer)

    # Telegram keeps the chat menu, not us: it has to be rewritten on every
    # start, or the old one survives with commands the bot no longer handles.
    await telegram.set_commands(COMMANDS)

    reaper = asyncio.create_task(brain.reap_idle())
    scheduler = asyncio.create_task(_scheduler(cfg, brain, delivery))
    pump = asyncio.create_task(
        _pump(cfg, telegram, brain, transcriber, delivery)
    )

    log.info("listening (allowed user ids: %s)", sorted(cfg.allowed_user_ids))
    await stop.wait()
    log.info("shutting down")

    for task in (pump, reaper, scheduler):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    # Closing the session is what lets claude-mem summarise this run.
    await brain.close()
    await telegram.aclose()


async def _pump(cfg, telegram: Telegram, brain: Brain,
                transcriber: Transcriber, delivery: Delivery) -> None:
    async for incoming in telegram.poll():
        if incoming.text.startswith("/"):
            if await _command(cfg, telegram, brain, delivery, incoming):
                continue
        prompt = incoming.text
        if incoming.voice is not None:
            prompt = await _transcribe(cfg, telegram, transcriber, incoming)
            if not prompt:
                continue
        elif incoming.document is not None:
            prompt = await _receive_file(cfg, telegram, incoming)
            if not prompt:
                continue
        await memory.append(cfg.raw_memory_dir, "owner", prompt)
        typing = asyncio.create_task(_keep_typing(telegram, incoming.chat_id))
        try:
            # prompt, not incoming.text: for a voice note the latter is
            # empty and the model would be answering nothing.
            turn = await brain.ask(prompt)
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing
        # Failures are reported to the owner but kept out of memory: they are not
        # things the assistant said, and the distiller would treat them as such.
        if turn.ok:
            await memory.append(cfg.raw_memory_dir, "assistant", turn.text)
        await telegram.send(incoming.chat_id, turn.text)


# Single list of commands: it feeds both the Telegram menu and /help, so what
# the chat offers is always what _command knows how to handle.
COMMANDS: list[tuple[str, str]] = [
    ("routines", "list routines and their state"),
    ("enable", "enable a routine — /enable <name>"),
    ("disable", "suspend a routine — /disable <name>"),
    ("run", "run a routine now — /run <name>"),
    ("help", "what I can do"),
]

HELP = "Commands:\n\n" + "\n".join(
    f"`/{name}` — {description}" for name, description in COMMANDS
) + """

To create one, just talk to me: tell me what to do and when. I write it
disabled, then you enable it with /enable."""


async def _command(cfg, telegram: Telegram, brain: Brain, delivery: Delivery,
                   incoming: Incoming) -> bool:
    """Commands handled in Python, never by the model.

    Enabling a routine is a human act on purpose: the model can write a routine
    but not start it, so a successful injection does not turn into recurring
    execution.
    """
    parts = incoming.text.strip().split(maxsplit=1)
    command = parts[0].lower().lstrip("/")
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command in ("help", "start"):
        await telegram.send(incoming.chat_id, HELP)
        return True

    if command == "routines":
        found = routines_module.load_all(cfg.routines_dir)
        await telegram.send(
            incoming.chat_id,
            routines_module.describe(found, datetime.now()),
        )
        return True

    if command in ("enable", "disable", "run"):
        if not argument:
            await telegram.send(incoming.chat_id, f"Which one? `/{command} <name>`")
            return True
        routine = routines_module.find(cfg.routines_dir, argument)
        if routine is None:
            await telegram.send(
                incoming.chat_id,
                f"Can't find `{argument}`. See `/routines`.",
            )
            return True

        if command == "run":
            await telegram.send(incoming.chat_id, f"Running `{routine.name}`.")
            asyncio.create_task(_run_routine(brain, delivery, routine))
            return True

        enable = command == "enable"
        if enable and routine.next_run(datetime.now()) is None:
            await telegram.send(
                incoming.chat_id,
                f"Not enabling `{routine.name}`: schedule "
                f"`{routine.schedule}` is not a valid cron expression.",
            )
            return True
        if enable and not delivery.available(routine.channel):
            await telegram.send(
                incoming.chat_id,
                f"Channel `{routine.channel}` not available. "
                f"Set MAIL_TO in .env or switch the channel to telegram.",
            )
            return True
        try:
            routines_module.set_enabled(routine, enable)
        except (OSError, ValueError) as exc:
            await telegram.send(incoming.chat_id, f"Cannot save it: {exc}")
            return True
        nxt = routine.next_run(datetime.now())
        when = f", next {nxt:%d/%m %H:%M}" if enable and nxt else ""
        await telegram.send(
            incoming.chat_id,
            f"`{routine.name}` {'enabled' if enable else 'suspended'}"
            f"{when}.",
        )
        return True

    return False  # not one of ours: let the model handle it


async def _run_routine(brain: Brain, delivery: Delivery,
                       routine: "routines_module.Routine") -> None:
    """Run a routine and deliver the result."""
    log.info("routine '%s' running", routine.name)
    try:
        turn = await brain.ask(routine.prompt, timeout=getattr(
            brain, "routine_timeout_seconds", None), routine=True)
    except Exception:
        log.exception("routine '%s' failed", routine.name)
        await delivery.send("telegram", f"Routine {routine.name}",
                            "Error during the run, check the logs.")
        return
    if not turn.ok:
        # A transport error is not a result: say it, don't file it.
        await delivery.send("telegram", f"Routine {routine.name}", turn.text)
        return
    charts, note = await _charts_for(brain, routine)
    body = turn.text + (f"\n\n_{note}_" if note else "")
    await delivery.send(routine.channel, f"Routine {routine.name}", body, charts)


async def _charts_for(brain: Brain, routine: "routines_module.Routine"):
    """Draw a routine's chart set, if it asks for one. Never fails the run."""
    if not routine.charts:
        return [], ""
    if routine.charts not in coach_charts.CHART_SETS:
        return [], f"grafici '{routine.charts}' sconosciuti, non disegnati"
    if not brain.icu.enabled:
        return [], "grafici non disegnati: Intervals.icu non configurato"
    try:
        return await coach_charts.render(routine.charts, brain.icu, datetime.now().date()), ""
    except Exception:
        log.exception("routine '%s': charts failed", routine.name)
        return [], "grafici non disegnati: errore, vedi i log"


async def _scheduler(cfg, brain: Brain, delivery: Delivery) -> None:
    """Watch the clock and start whatever came due.

    Compares windows instead of exact times: if a tick slips, the run is not
    skipped. Routines start in sequence, not in parallel -- they share a single
    Brain, and twenty prompts at once would only queue on its lock while
    burning tokens.
    """
    previous = datetime.now()
    while True:
        await asyncio.sleep(cfg.scheduler_tick_seconds)
        now = datetime.now()
        if now < previous:
            # clock went backwards (DST, ntp): restart from here
            previous = now
            continue
        try:
            ready = routines_module.due(
                routines_module.load_all(cfg.routines_dir), previous, now
            )
        except Exception:
            log.exception("scheduler: cannot read the routines")
            previous = now
            continue
        previous = now
        for routine in ready:
            await _run_routine(brain, delivery, routine)


def _safe_name(raw: str) -> str:
    """A harmless file name, derived from the sender's."""
    name = Path(raw).name  # throw away any path component
    suffix = _UNSAFE.sub("", Path(name).suffix.lower())[:10]
    stem = _UNSAFE.sub("_", Path(name).stem).strip("._") or "file"
    # Truncate the stem, never the suffix: the extension decides whether the
    # file is accepted, and cutting it would reject a long-named PDF.
    return f"{stem[:80]}{suffix}"


def _sweep_inbox(cfg) -> None:
    """Remove expired attachments.

    Without it, inbox/ grows forever and every document ever received stays
    readable by the model for good.
    """
    cutoff = time.time() - cfg.inbox_retention_days * 86400
    for path in cfg.inbox_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            log.exception("could not remove %s", path.name)


async def _receive_file(cfg, telegram: Telegram, incoming: Incoming) -> str:
    """Save an attachment in inbox/ and build the prompt that opens it."""
    doc = incoming.document
    name = _safe_name(doc.file_name)
    suffix = Path(name).suffix.lower()

    if suffix not in cfg.allowed_file_types:
        await telegram.send(
            incoming.chat_id,
            f"I don't open {suffix or 'extensionless'} files. "
            f"I accept: {', '.join(cfg.allowed_file_types)}",
        )
        return ""
    if doc.size > cfg.max_file_bytes:
        mb = cfg.max_file_bytes // 1024 // 1024
        await telegram.send(incoming.chat_id, f"File too big (max {mb} MB).")
        return ""

    cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    _sweep_inbox(cfg)
    # The timestamp keeps two files with the same name from overwriting each
    # other, and keeps arrival order readable at a glance.
    dest = cfg.inbox_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}"

    typing = asyncio.create_task(_keep_typing(telegram, incoming.chat_id))
    try:
        if not await telegram.download(doc.file_id, dest):
            await telegram.send(incoming.chat_id, "Could not download the file.")
            return ""
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing

    relative = dest.relative_to(cfg.root).as_posix()
    question = incoming.text or "Tell me what it contains."
    # The path is handed to the model so it opens it with Read, which handles
    # PDFs and images on its own. The content stays data, not instruction:
    # INJECTION_GUARD applies, and a PDF trying to give orders gets reported.
    return (
        f"the owner sent a file: `{relative}`\n"
        f"Open it with Read and answer.\n\n{question}"
    )


async def _transcribe(cfg, telegram: Telegram, transcriber: Transcriber,
                      incoming: Incoming) -> str:
    """Voice -> text, locally. Empty string if nothing comes of it."""
    voice = incoming.voice
    if not transcriber.available:
        await telegram.send(
            incoming.chat_id,
            "I can't listen to voice notes: faster-whisper is not installed.",
        )
        return ""
    if voice.duration > cfg.max_voice_seconds:
        await telegram.send(
            incoming.chat_id,
            f"Voice note too long ({voice.duration}s, max "
            f"{cfg.max_voice_seconds}s). Send it in pieces.",
        )
        return ""
    if voice.size > cfg.max_voice_bytes:
        await telegram.send(incoming.chat_id, "Voice note too heavy.")
        return ""

    typing = asyncio.create_task(_keep_typing(telegram, incoming.chat_id))
    # The file lives in a temporary directory, not inside the installation:
    # that way it never lands on a path the model's tools can read, and it
    # disappears anyway if transcription blows up.
    with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
        path = Path(tmp) / "audio"
        try:
            if not await telegram.download(voice.file_id, path):
                await telegram.send(incoming.chat_id,
                                    "Could not download the voice note.")
                return ""
            text = await transcriber.transcribe(path)
        except Exception:
            log.exception("transcription failed")
            await telegram.send(incoming.chat_id,
                                "Could not transcribe the voice note.")
            return ""
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing

    if not text:
        await telegram.send(incoming.chat_id, "I heard no speech.")
        return ""
    # Echo the transcript back: if it misheard, you see it immediately
    # instead of getting an answer to a question you never asked.
    await telegram.send(incoming.chat_id, f"\U0001F399 {text}")
    return text


async def _keep_typing(telegram: Telegram, chat_id: int) -> None:
    """Telegram clears the typing indicator after ~5s; a long turn needs nudging."""
    while True:
        await telegram.typing(chat_id)
        await asyncio.sleep(4)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs the full request URL at INFO, and the Telegram URL carries the
    # bot token. Under systemd that would write the token into journald on
    # every poll, forever.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
