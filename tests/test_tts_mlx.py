"""Tests for the MLX TTS backend (respx-mocked /v1/audio/speech)."""

from __future__ import annotations

import json
from io import BytesIO

import httpx
import pytest
import respx
from pydub import AudioSegment
from pydub.generators import Sine

from paper_assistant.tts import (
    FfmpegMissingError,
    MlxConfigError,
    MlxQualityError,
    MlxTransientError,
    MlxTTSBackend,
    analyze_audio_file,
)


def _audio_bytes(
    *,
    audio_format: str = "wav",
    audible_ms: int = 1000,
    trailing_silence_ms: int = 0,
) -> bytes:
    tone = Sine(440).to_audio_segment(duration=audible_ms).apply_gain(-12)
    audio = tone + AudioSegment.silent(duration=trailing_silence_ms)
    buf = BytesIO()
    audio.export(buf, format=audio_format)
    return buf.getvalue()


def _audio_with_internal_silence_bytes(
    *,
    audible_ms: int = 5000,
    internal_silence_ms: int = 20_000,
) -> bytes:
    tone = Sine(440).to_audio_segment(duration=audible_ms).apply_gain(-12)
    audio = tone + AudioSegment.silent(duration=internal_silence_ms) + tone
    buf = BytesIO()
    audio.export(buf, format="wav")
    return buf.getvalue()


def _healthy_wav_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    word_count = len(payload["input"].split())
    audible_ms = max(1000, round(word_count * 60_000 / 180))
    return httpx.Response(
        200,
        content=_audio_bytes(audible_ms=audible_ms),
        headers={"content-type": "audio/wav"},
    )


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
            content=_audio_bytes(audio_format="mp3"),
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
async def test_multi_chunk_no_ffmpeg_raises_before_request(tmp_path):
    backend = _backend(chunk_chars=40, max_input_chars=4000)
    backend._ffmpeg_available = False  # simulate missing ffmpeg

    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=_healthy_wav_response
    )

    # 3 sentences force multiple chunks at chunk_chars=40.
    text = (
        "First short sentence here. "
        "Second short sentence follows. "
        "Third short sentence to push past the chunk limit."
    )

    with pytest.raises(FfmpegMissingError):
        await backend.synthesize(text, tmp_path / "out.mp3")

    assert not route.called


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
            200, content=_audio_bytes(), headers={"content-type": "audio/wav"}
        )
    )

    with pytest.raises(FfmpegMissingError):
        await backend.synthesize("One short sentence.", tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_bearer_header_sent_when_api_key_set(tmp_path):
    backend = _backend(api_key="secret-token")
    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=_healthy_wav_response
    )

    await backend.synthesize("hi", tmp_path / "out.mp3")

    assert route.called
    assert route.calls[0].request.headers.get("authorization") == "Bearer secret-token"


@pytest.mark.asyncio
@respx.mock
async def test_generic_voice_forwarded_without_speaker_for_non_qwen_model(tmp_path):
    backend = _backend(model="Voxtral-4B-TTS-2603-mlx-bf16", voice="alloy")
    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=_healthy_wav_response
    )

    await backend.synthesize("hi", tmp_path / "out.mp3")

    payload = json.loads(route.calls[0].request.content)
    assert payload["voice"] == "alloy"
    assert "speaker" not in payload


@pytest.mark.asyncio
@respx.mock
async def test_qwen_model_mirrors_voice_into_speaker_when_unset(tmp_path):
    backend = _backend(
        model="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
        voice="Ryan",
    )
    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=_healthy_wav_response
    )

    await backend.synthesize("hi", tmp_path / "out.mp3")

    payload = json.loads(route.calls[0].request.content)
    assert payload["voice"] == "Ryan"
    assert payload["speaker"] == "Ryan"


@pytest.mark.asyncio
@respx.mock
async def test_explicit_speaker_is_forwarded_separately(tmp_path):
    backend = _backend(voice="alloy", speaker="Ryan")
    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=_healthy_wav_response
    )

    await backend.synthesize("hi", tmp_path / "out.mp3")

    payload = json.loads(route.calls[0].request.content)
    assert payload["voice"] == "alloy"
    assert payload["speaker"] == "Ryan"


@pytest.mark.asyncio
@respx.mock
async def test_trims_long_trailing_silence(tmp_path):
    text = "This healthy narration has twelve words and should remain completely audible."
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            content=_audio_bytes(audible_ms=4000, trailing_silence_ms=3000),
            headers={"content-type": "audio/wav"},
        )
    )
    out = tmp_path / "out.mp3"

    await _backend().synthesize(text, out)

    metrics = analyze_audio_file(out, text)
    assert 4.0 <= metrics.duration_seconds <= 4.3
    assert metrics.trailing_silence_ms <= 250


@pytest.mark.asyncio
@respx.mock
async def test_truncated_audio_raises_quality_error_and_preserves_output(tmp_path):
    text = " ".join(["word"] * 40)
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            content=_audio_bytes(audible_ms=3000, trailing_silence_ms=2000),
            headers={"content-type": "audio/wav"},
        )
    )
    out = tmp_path / "out.mp3"
    out.write_bytes(b"existing-audio")

    with pytest.raises(MlxQualityError):
        await _backend().synthesize(text, out)

    assert out.read_bytes() == b"existing-audio"


@pytest.mark.asyncio
@respx.mock
async def test_silent_audio_raises_quality_error(tmp_path):
    text = "This narration should contain audible speech."
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            content=AudioSegment.silent(duration=3000).export(
                BytesIO(),
                format="wav",
            ).getvalue(),
            headers={"content-type": "audio/wav"},
        )
    )

    with pytest.raises(MlxQualityError, match="no speech"):
        await _backend().synthesize(text, tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_internal_silence_raises_quality_error(tmp_path):
    text = " ".join(["word"] * 40)
    respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            content=_audio_with_internal_silence_bytes(),
            headers={"content-type": "audio/wav"},
        )
    )

    with pytest.raises(MlxQualityError, match="mostly silent"):
        await _backend(chunk_chars=2000).synthesize(text, tmp_path / "out.mp3")


@pytest.mark.asyncio
@respx.mock
async def test_quality_failure_retries_once_with_smaller_chunks(tmp_path):
    text = " ".join(["word"] * 80)

    def response_for_chunk(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        chunk = payload["input"]
        if len(chunk) > 250:
            audible_ms = 3000
        else:
            audible_ms = max(1000, round(len(chunk.split()) * 60_000 / 180))
        return httpx.Response(
            200,
            content=_audio_bytes(audible_ms=audible_ms),
            headers={"content-type": "audio/wav"},
        )

    route = respx.post("http://mlx.local:8000/v1/audio/speech").mock(
        side_effect=response_for_chunk
    )
    out = tmp_path / "out.mp3"

    await _backend(chunk_chars=500).synthesize(text, out)

    assert route.call_count == 3
    assert out.exists()
