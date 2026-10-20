"""Telegram transport: outbound long-polling, no inbound port, no TLS.

Security note: the allowlist check below is the only thing standing between a
stranger on the internet and an agent with filesystem access. It runs before
the model is ever invoked, and it compares numeric user ids — never usernames,
which are re-assignable, and never anything found in the message body.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pathlib import Path

import httpx

from .format import to_telegram_html

log = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096


@dataclass(frozen=True)
class Voice:
    file_id: str
    duration: int
    size: int


@dataclass(frozen=True)
class Document:
    file_id: str
    file_name: str
    mime: str
    size: int


@dataclass(frozen=True)
class Incoming:
    chat_id: int
    user_id: int
    text: str
    voice: Voice | None = None
    document: Document | None = None


class Telegram:
    def __init__(self, token: str, allowed_user_ids: frozenset[int]) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"
        self._allowed = allowed_user_ids
        self._offset: int | None = None
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(65.0, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, **params) -> dict:
        response = await self._client.post(f"{self._base}/{method}", json=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {payload}")
        return payload["result"]

    async def poll(self) -> AsyncIterator[Incoming]:
        """Yield allowed messages forever, riding out network hiccups."""
        backoff = 1.0
        while True:
            try:
                updates = await self._call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=60,
                    allowed_updates=["message"],
                )
                backoff = 1.0
            except Exception as exc:
                # Never interpolate the exception itself: httpx puts the full
                # request URL in it, and the URL contains the bot token.
                log.warning("getUpdates failed (%s), retrying in %.0fs",
                            _redact(exc), backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            for update in updates:
                self._offset = update["update_id"] + 1
                message = update.get("message") or {}
                sender = (message.get("from") or {}).get("id")
                chat = (message.get("chat") or {}).get("id")
                if sender is None or chat is None:
                    continue
                # Allowlist first, always: nothing below this line -- not a
                # download, not a transcription -- runs for a stranger.
                if sender not in self._allowed:
                    log.warning("dropped message from unauthorised user id %s", sender)
                    continue

                text = message.get("text")
                if text:
                    yield Incoming(chat_id=chat, user_id=sender, text=text)
                    continue

                document = _document_of(message)
                if document is not None:
                    yield Incoming(
                        chat_id=chat,
                        user_id=sender,
                        # the caption is the question: "what does this say?"
                        text=(message.get("caption") or "").strip(),
                        document=document,
                    )
                    continue

                # "voice" is a note recorded in the app; "audio" and
                # "video_note" are handled the same way -- they are all
                # containers PyAV can decode.
                for key in ("voice", "audio", "video_note"):
                    blob = message.get(key)
                    if not blob or not blob.get("file_id"):
                        continue
                    yield Incoming(
                        chat_id=chat,
                        user_id=sender,
                        text="",
                        voice=Voice(
                            file_id=blob["file_id"],
                            duration=int(blob.get("duration") or 0),
                            size=int(blob.get("file_size") or 0),
                        ),
                    )
                    break

    async def send(self, chat_id: int, text: str) -> None:
        # The split happens on the markdown, not the HTML: cutting HTML
        # would break a tag in half and Telegram would drop the message.
        for chunk in _split(text, TELEGRAM_MAX_CHARS):
            try:
                await self._call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=to_telegram_html(chunk),
                    parse_mode="HTML",
                    link_preview_options={"is_disabled": True},
                )
                continue
            except Exception as exc:
                # Markup the parser rejects must not cost the message:
                # better delivered raw than not delivered.
                log.warning("send with markup failed (%s), retrying plain",
                            _redact(exc))
            try:
                await self._call("sendMessage", chat_id=chat_id, text=chunk)
            except Exception:
                log.exception("send failed in plain text too")
                return

    async def send_photos(self, chat_id: int, photos: list[tuple[str, bytes, str]]) -> None:
        """Send (filename, png, caption) as albums of at most ten."""
        for start in range(0, len(photos), 10):
            batch = photos[start:start + 10]
            media = [
                {"type": "photo", "media": f"attach://p{i}", "caption": caption[:1024]}
                for i, (_, _, caption) in enumerate(batch)
            ]
            files = {f"p{i}": (name, png, "image/png") for i, (name, png, _) in enumerate(batch)}
            try:
                response = await self._client.post(
                    f"{self._base}/sendMediaGroup",
                    data={"chat_id": str(chat_id), "media": json.dumps(media)},
                    files=files,
                )
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise RuntimeError("sendMediaGroup not ok")
            except Exception as exc:
                log.warning("sending photos failed (%s)", _redact(exc))
                await self.send(chat_id, "_(grafici non inviati: errore Telegram)_")
                return

    async def download(self, file_id: str, dest: Path) -> bool:
        """Download a file from Telegram. False if it cannot be done."""
        try:
            info = await self._call("getFile", file_id=file_id)
            remote = info.get("file_path")
            if not remote:
                return False
            async with self._client.stream(
                "GET", f"{self._file_base}/{remote}"
            ) as response:
                response.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        fh.write(chunk)
            return True
        except Exception as exc:
            log.warning("voice download failed (%s)", _redact(exc))
            return False

    async def typing(self, chat_id: int) -> None:
        try:
            await self._call("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass  # cosmetic only

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Rewrite the bot's command menu.

        Telegram keeps the list on its own server until you change it: without
        this call the chat shows the last menu set, forever, including commands
        the bot no longer handles. The list replaces, it does not add, so it is
        the single source of truth for what appears in the menu.
        """
        try:
            await self._call(
                "setMyCommands",
                commands=[
                    {"command": name, "description": description}
                    for name, description in commands
                ],
            )
        except Exception as exc:
            # The menu is convenience, not function: carry on if it fails.
            log.warning("setMyCommands failed (%s)", _redact(exc))


def _document_of(message: dict) -> Document | None:
    """Extract an attachment, if the message carries one."""
    blob = message.get("document")
    if blob and blob.get("file_id"):
        return Document(
            file_id=blob["file_id"],
            file_name=str(blob.get("file_name") or "document"),
            mime=str(blob.get("mime_type") or ""),
            size=int(blob.get("file_size") or 0),
        )

    # Photos arrive as a list of increasing sizes: the last one is the full
    # image. The thumbnails are useless to read.
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = photos[-1]
        if largest.get("file_id"):
            return Document(
                file_id=largest["file_id"],
                file_name="photo.jpg",
                mime="image/jpeg",
                size=int(largest.get("file_size") or 0),
            )
    return None


def _redact(exc: Exception) -> str:
    """Describe a transport failure without echoing the tokenised URL."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 409:
            return ("409 Conflict - another poller holds this bot, or a "
                    "previous long-poll has not expired yet")
        return f"HTTP {code}"
    return type(exc).__name__


def _split(text: str, limit: int) -> list[str]:
    """Chunk on paragraph, then line, then hard cut — code blocks survive better."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return _balance_fences(chunks)


def _balance_fences(chunks: list[str]) -> list[str]:
    """Re-close code blocks the split cut in half.

    A fence opened and never closed swallows the rest of the message inside a
    <pre>, and the next chunk starts with code nobody opened.
    """
    out: list[str] = []
    carry = ""
    for chunk in chunks:
        chunk = carry + chunk
        carry = ""
        if chunk.count("```") % 2:
            # reopen in the next chunk, keeping the language if declared
            opener = chunk[chunk.rfind("```"):].split("\n", 1)[0]
            chunk += "\n```"
            carry = opener + "\n"
        out.append(chunk)
    return out
