"""Text-to-speech backends and shared preparation helpers.

Primary backend is a local MLX server exposing the OpenAI-compatible
``/v1/audio/speech`` endpoint. edge-tts is kept as a graceful fallback.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import edge_tts
import httpx

from paper_assistant.config import Config

logger = logging.getLogger(__name__)


# Backend-facing typed errors ---------------------------------------------------


class TTSBackendError(Exception):
    """Base class for TTS backend failures."""


class MlxTransientError(TTSBackendError):
    """Transient MLX failure (connect refused, timeout, 5xx). Fallback-safe."""


class MlxConfigError(TTSBackendError):
    """MLX returned a 4xx. Indicates misconfiguration — do not silently fall back."""


class EdgeTTSError(TTSBackendError):
    """edge-tts failed to synthesize."""


class FfmpegMissingError(TTSBackendError):
    """ffmpeg is required for the current operation but not installed."""


# Preparation helpers ----------------------------------------------------------


def prepare_text_for_tts(
    markdown: str,
    title: str,
    authors: list[str],
    source_label: str = "paper",
) -> str:
    """Prepare the raw markdown summary for TTS (legacy/fallback path).

    Strips markdown formatting, prepends a short intro, and cleans up
    the text for natural speech. This is used whenever a derived
    narration transcript is unavailable.
    """
    if len(authors) > 3:
        author_str = f"{authors[0]}, {authors[1]}, {authors[2]}, and others"
    else:
        author_str = ", ".join(authors)

    intro = f"This is a summary of the {source_label}: {title}"
    if author_str:
        intro += f", by {author_str}"
    intro += ".\n\n"

    text = _strip_markdown_for_speech(markdown, replace_equations=True)
    return intro + text.strip()


def prepare_script_for_tts(script_markdown: str) -> str:
    """Prepare a derived narration script for TTS.

    The script is already prose written for audio — do NOT prepend an intro
    (the script opens naturally) and do the minimum amount of cleanup needed
    to remove stray markdown markers.
    """
    text = _strip_markdown_for_speech(script_markdown, replace_equations=False)
    return text.strip()


def _strip_markdown_for_speech(text: str, *, replace_equations: bool) -> str:
    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Remove bold/italic markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)

    # Drop image markdown entirely so URLs aren't spoken. The alt text
    # usually duplicates the surrounding figure prose, so keeping the alt
    # makes the narration repeat itself; strip the whole token instead.
    # Alt text can contain ``]`` (e.g. ``[CLS]`` tokens in ML captions),
    # so tolerate inner brackets that aren't followed by the URL ``(``.
    text = re.sub(r"!\[(?:[^\]]|\](?!\())*\]\([^)]+\)", "", text)

    # Remove markdown links, keep text. Same nested-bracket tolerance.
    text = re.sub(r"\[((?:[^\]]|\](?!\())*)\]\([^)]+\)", r"\1", text)

    # Remove code blocks (before inline code to avoid backtick conflicts)
    text = re.sub(r"```[\s\S]*?```", "", text)

    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Bullet points → soft indent
    text = re.sub(r"^\s*[-*+]\s+", "  ", text, flags=re.MULTILINE)

    # LaTeX math handling
    if replace_equations:
        text = re.sub(r"\$\$[\s\S]*?\$\$", " (equation omitted) ", text)
    else:
        text = re.sub(r"\$\$[\s\S]*?\$\$", " ", text)
    text = re.sub(r"\$([^$]+)\$", r"\1", text)

    # Clean up inline citations like (Section 3, p.5)
    text = re.sub(r"\((?:Section|§)\s*[\d.]+,?\s*p\.?\s*\d+\)", "", text)

    # Collapse excess whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text


# Chunking ---------------------------------------------------------------------


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`(])")


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at sentence boundaries, each ≤ max_chars.

    Handles overlong sentences by splitting on whitespace, and falls back to
    hard character splits if a single token exceeds max_chars.
    """
    if max_chars <= 0:
        return [text] if text else []

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(sentence) <= max_chars:
            current = sentence
        else:
            chunks.extend(_hard_split(sentence, max_chars))

    if current:
        chunks.append(current)
    return chunks


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        parts.extend(_SENTENCE_SPLIT_RE.split(paragraph))
    return parts


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Fall back to whitespace-aware hard split for single oversized tokens."""
    words = text.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(word) <= max_chars:
            current = word
        else:
            for i in range(0, len(word), max_chars):
                piece = word[i : i + max_chars]
                chunks.append(piece)
            current = ""
    if current:
        chunks.append(current)
    return chunks


# Backend protocol + factory ---------------------------------------------------


class TTSBackend(Protocol):
    name: str

    async def synthesize(self, text: str, output_path: Path) -> Path: ...


def get_tts_backend(config: Config) -> TTSBackend:
    """Return the configured primary TTS backend."""
    if config.tts_backend == "mlx":
        return MlxTTSBackend(
            url=config.mlx_tts_url,
            model=config.mlx_tts_model,
            voice=config.mlx_tts_voice,
            speaker=config.mlx_tts_speaker,
            api_key=config.mlx_tts_api_key,
            speed=config.mlx_tts_speed,
            timeout_s=config.mlx_tts_timeout_s,
            chunk_chars=config.mlx_tts_chunk_chars,
            max_input_chars=config.mlx_tts_max_input_chars,
        )
    return EdgeTTSBackend(voice=config.tts_voice, rate=config.tts_rate)


def get_edge_backend(config: Config) -> "EdgeTTSBackend":
    return EdgeTTSBackend(voice=config.tts_voice, rate=config.tts_rate)


# Edge backend -----------------------------------------------------------------


@dataclass
class EdgeTTSBackend:
    voice: str = "en-US-AriaNeural"
    rate: str = "+0%"
    name: str = "edge"

    async def synthesize(self, text: str, output_path: Path) -> Path:
        try:
            communicate = edge_tts.Communicate(text=text, voice=self.voice, rate=self.rate)
            await communicate.save(str(output_path))
        except Exception as exc:  # pragma: no cover - network/library errors
            raise EdgeTTSError(f"edge-tts failed: {exc}") from exc
        return output_path


# MLX backend ------------------------------------------------------------------


@dataclass
class MlxTTSBackend:
    url: str
    model: str
    voice: str | None = None
    speaker: str | None = None
    api_key: str | None = None
    speed: float = 1.0
    timeout_s: float = 120.0
    chunk_chars: int = 2000
    max_input_chars: int = 6000
    response_format: str = "wav"
    name: str = "mlx"

    def __post_init__(self) -> None:
        self._ffmpeg_available = shutil.which("ffmpeg") is not None

    @property
    def ffmpeg_available(self) -> bool:
        return self._ffmpeg_available

    @property
    def endpoint(self) -> str:
        return f"{self.url.rstrip('/')}/v1/audio/speech"

    async def synthesize(self, text: str, output_path: Path) -> Path:
        from pydub import AudioSegment

        text = text.strip()
        if not text:
            raise MlxConfigError("MLX TTS input is empty after preparation.")

        chunks = split_into_chunks(text, self.chunk_chars)
        if not chunks:
            raise MlxConfigError("MLX TTS produced zero chunks.")

        if len(chunks) == 1:
            audio_bytes, content_type = await self._synthesize_single(chunks[0])
            self._write_output(audio_bytes, content_type, output_path, AudioSegment)
            return output_path

        if not self._ffmpeg_available:
            if len(text) <= self.max_input_chars:
                audio_bytes, content_type = await self._synthesize_single(text)
                self._write_output(audio_bytes, content_type, output_path, AudioSegment)
                return output_path
            raise FfmpegMissingError(
                "ffmpeg is required to concatenate multi-chunk MLX audio. "
                "Install with `brew install ffmpeg` or reduce input length."
            )

        segments: list[AudioSegment] = []
        for chunk in chunks:
            audio_bytes, content_type = await self._synthesize_single(chunk)
            segments.append(self._decode_segment(audio_bytes, content_type, AudioSegment))

        combined = segments[0]
        for seg in segments[1:]:
            combined += seg

        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.export(str(output_path), format="mp3")
        return output_path

    async def _synthesize_single(self, chunk: str) -> tuple[bytes, str]:
        payload = self._build_payload(chunk)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.endpoint, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise MlxTransientError(f"MLX TTS request failed: {exc}") from exc

        if resp.status_code >= 500:
            raise MlxTransientError(
                f"MLX TTS server error {resp.status_code}: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise MlxConfigError(
                f"MLX TTS request rejected ({resp.status_code}): {resp.text[:300]}"
            )

        content_type = resp.headers.get("content-type", "").lower()
        return resp.content, content_type

    def _build_payload(self, chunk: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "input": chunk,
            "response_format": self.response_format,
            "speed": self.speed,
        }

        if self.voice:
            payload["voice"] = self.voice

        speaker = self._effective_speaker()
        if speaker:
            payload["speaker"] = speaker

        return payload

    def _effective_speaker(self) -> str | None:
        if self.speaker:
            return self.speaker
        if self.voice and self._uses_model_specific_speaker():
            return self.voice
        return None

    def _uses_model_specific_speaker(self) -> bool:
        return "qwen3-tts" in self.model.lower()

    def _write_output(
        self,
        audio_bytes: bytes,
        content_type: str,
        output_path: Path,
        AudioSegment,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._is_mp3(content_type, audio_bytes):
            output_path.write_bytes(audio_bytes)
            return

        if not self._ffmpeg_available:
            raise FfmpegMissingError(
                "ffmpeg is required to transcode MLX output "
                f"(content-type={content_type or 'unknown'}) to MP3. "
                "Install with `brew install ffmpeg` or set "
                "PAPER_ASSIST_TTS_BACKEND=edge."
            )

        segment = self._decode_segment(audio_bytes, content_type, AudioSegment)
        segment.export(str(output_path), format="mp3")

    @staticmethod
    def _is_mp3(content_type: str, audio_bytes: bytes) -> bool:
        if "audio/mpeg" in content_type or "audio/mp3" in content_type:
            return True
        # MP3 magic bytes: ID3 or FF FB / FF F3 / FF F2
        head = audio_bytes[:3]
        if head[:2] == b"ID":
            return True
        if len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
            return True
        return False

    @staticmethod
    def _decode_segment(audio_bytes: bytes, content_type: str, AudioSegment):
        from io import BytesIO

        buf = BytesIO(audio_bytes)
        if "wav" in content_type or audio_bytes[:4] == b"RIFF":
            return AudioSegment.from_wav(buf)
        if "mpeg" in content_type or "mp3" in content_type:
            return AudioSegment.from_file(buf, format="mp3")
        # Let pydub/ffmpeg probe
        return AudioSegment.from_file(buf)


# Backwards-compatible convenience wrapper used by legacy callers --------------


async def text_to_speech(
    text: str,
    output_path: Path,
    voice: str = "en-US-AriaNeural",
    rate: str = "+0%",
) -> Path:
    """Synthesize with edge-tts. Kept for backwards compatibility."""
    backend = EdgeTTSBackend(voice=voice, rate=rate)
    return await backend.synthesize(text, output_path)


async def list_available_voices(language: str = "en") -> list[dict]:
    """List available edge-tts voices for a language."""
    voices = await edge_tts.list_voices()
    return [v for v in voices if v["Locale"].startswith(language)]
