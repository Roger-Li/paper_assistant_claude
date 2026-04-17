"""Tests for the MLX TTS backend (respx-mocked /v1/audio/speech)."""

from __future__ import annotations

import httpx
import pytest
import respx

from paper_assistant.tts import (
    FfmpegMissingError,
    MlxConfigError,
    MlxTransientError,
    MlxTTSBackend,
)


# Minimal valid WAV: 44-byte header + silence. pydub can decode this,
# but for chunking tests we also mock MP3 responses to avoid ffmpeg.
def _wav_bytes(n_frames: int = 1) -> bytes:
    header = (
        b"RIFF"
        + (36 + n_frames * 2).to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # PCM
        + (1).to_bytes(2, "little")  # mono
        + (16000).to_bytes(4, "little")  # sample rate
        + (32000).to_bytes(4, "little")  # byte rate
        + (2).to_bytes(2, "little")  # block align
        + (16).to_bytes(2, "little")  # bits per sample
        + b"data"
        + (n_frames * 2).to_bytes(4, "little")
        + (b"\x00\x00" * n_frames)
    )
    return header


def _mp3_bytes() -> bytes:
    # ID3v2 tag + one MP3 frame header (MPEG1 Layer3 128kbps 44100Hz mono).
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 380
    return id3 + frame


def _backend(**overrides) -> MlxTTSBackend:
    defaults = dict(
        url="http://mlx.local:8000",
        model="TestModel",
        chunk_chars=2000,
        max_input_chars=6000,
        timeout_s=5.0,
    )
    defaults.update(overrides)
    return MlxTTSBackend(**defaults)


@pytest.mark.asyncio
@respx.mock
async def test_single_chunk_mp3_response(tmp_path):
    backend = _backend()
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            content=_mp3_bytes(),
            headers={"content-type": "audio/mpeg"},
        )
    )
    out = tmp_path / "out.mp3"

    await backend.synthesize("One short sentence.", out)

    assert out.exists()
    assert out.read_bytes().startswith(b"ID3")


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_raises_transient(tmp_path):
    backend = _backend()
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=httpx.ConnectError("refused")
    )

    with pytest.raises(MlxTransientError):
        await backend.synthesize("text", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_transient(tmp_path):
    backend = _backend()
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=httpx.ReadTimeout("slow")
    )

    with pytest.raises(MlxTransientError):
        await backend.synthesize("text", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_5xx_raises_transient(tmp_path):
    backend = _backend()
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(503, text="bad gateway")
    )

    with pytest.raises(MlxTransientError):
        await backend.synthesize("text", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_4xx_raises_config_error(tmp_path):
    backend = _backend()
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(400, text="unknown model")
    )

    with pytest.raises(MlxConfigError):
        await backend.synthesize("text", tmp_path / "out.mp3")


@pytest.mark.asyncio
async def test_empty_input_raises_config_error(tmp_path):
    backend = _backend()
    with pytest.raises(MlxConfigError):
        await backend.synthesize("   ", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_multi_chunk_no_ffmpeg_and_within_max_input_retries_single(tmp_path):
    """When ffmpeg is missing but text fits in max_input_chars, a single
    large request is sent as a last resort rather than failing."""
    backend = _backend(chunk_chars=40, max_input_chars=4000)
    backend._ffmpeg_available = False  # simulate missing ffmpeg

    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200, content=_mp3_bytes(), headers={"content-type": "audio/mpeg"}
        )
    )

    # 3 sentences force multiple chunks at chunk_chars=40.
    text = (
        "First short sentence here. "
        "Second short sentence follows. "
        "Third short sentence to push past the chunk limit."
    )

    await backend.synthesize(text, tmp_path / "out.mp3")

    assert route.called
    # Only one request because fallback to single-shot consolidation.
    assert route.call_count == 1
    sent = route.calls[0].request
    assert b"First short sentence" in sent.content


@pytest.mark.asyncio
@respx.mock
async def test_multi_chunk_no_ffmpeg_over_max_raises_ffmpeg_missing(tmp_path):
    backend = _backend(chunk_chars=40, max_input_chars=80)
    backend._ffmpeg_available = False  # simulate missing ffmpeg

    # Ensure text exceeds max_input_chars so single-shot fallback cannot save it.
    text = "Sentence. " * 40  # > 80 chars and produces >1 chunks at chunk_chars=40

    with pytest.raises(FfmpegMissingError):
        await backend.synthesize(text, tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_wav_response_without_ffmpeg_raises_ffmpeg_missing(tmp_path):
    backend = _backend()
    backend._ffmpeg_available = False

    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200, content=_wav_bytes(), headers={"content-type": "audio/wav"}
        )
    )

    with pytest.raises(FfmpegMissingError):
        await backend.synthesize("One short sentence.", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_bearer_header_sent_when_api_key_set(tmp_path):
    backend = _backend(api_key="secret-token")
    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200, content=_mp3_bytes(), headers={"content-type": "audio/mpeg"}
        )
    )

    await backend.synthesize("hi", tmp_path / "out.mp3")

    assert route.called
    assert route.calls[0].request.headers.get("authorization") == "Bearer secret-token"
