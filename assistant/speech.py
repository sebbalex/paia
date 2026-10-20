"""Voice transcription, entirely local.

No external service receives the audio: faster-whisper runs the model on this
machine's CPU, and PyAV decodes Telegram's OGG/Opus without a separate ffmpeg
install.

The only thing that comes off the network is the model weights, downloaded
once from HuggingFace on first use and then kept in `models/`. After that
download the system works offline: `local_files_only` blocks any later
request, so a missing model fails explicitly instead of quietly reaching for
the network mid-conversation.

The model stays loaded between messages. Loading it costs several seconds and
a good deal of memory: paying that per voice note would make the assistant
unusable on a small VPS.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(
        self,
        model_name: str,
        compute_type: str,
        download_root: Path,
        language: str | None = None,
        vocabulary: str = "",
        offline: bool = False,
    ) -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._download_root = download_root
        self._language = language
        self._vocabulary = vocabulary.strip()
        self._offline = offline
        self._model = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        from faster_whisper import WhisperModel

        self._download_root.mkdir(parents=True, exist_ok=True)
        log.info("loading whisper model '%s' (%s)",
                 self._model_name, self._compute_type)
        return WhisperModel(
            self._model_name,
            device="cpu",
            compute_type=self._compute_type,
            download_root=str(self._download_root),
            local_files_only=self._offline,
        )

    def _run(self, path: Path) -> str:
        segments, info = self._model.transcribe(
            str(path),
            language=self._language,
            # The VAD cuts silence: Telegram voice notes almost always open
            # with a couple of empty seconds, and without the filter Whisper
            # hallucinates text over them.
            vad_filter=True,
            beam_size=5,
            # Lexical context: forces nothing, but shifts the probabilities
            # towards the terms we actually use.
            initial_prompt=self._vocabulary or None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("transcribed: language=%s prob=%.2f characters=%d",
                 info.language, info.language_probability, len(text))
        return text

    async def transcribe(self, path: Path) -> str:
        """Return the text, or an empty string if there is no speech."""
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
            # to_thread because transcription is CPU-bound and blocking:
            # without it, Telegram's long poll stalls for the whole duration.
            return await asyncio.to_thread(self._run, path)
