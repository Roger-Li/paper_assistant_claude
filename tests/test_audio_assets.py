"""Tests for paper_assistant.audio_assets.render_audio_assets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from paper_assistant.audio_assets import render_audio_assets
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus
from paper_assistant.storage import StorageManager


@pytest.fixture
def config(tmp_data_dir):
    cfg = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_data_dir,
        icloud_sync=False,
        tts_backend="mlx",
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def storage(config):
    return StorageManager(config)


def _paper(paper_id: str = "2503.10291") -> Paper:
    return Paper(
        metadata=PaperMetadata(
            arxiv_id=paper_id,
            title="Test Paper",
            authors=["Alice"],
            abstract="Abstract",
        ),
        status=ProcessingStatus.PENDING,
    )


def _fake_mlx_backend(output_path: Path, *, succeed: bool = True):
    """Return a coroutine that simulates MLX backend synthesize."""
    async def _synthesize(text: str, out_path: Path):
        if not succeed:
            from paper_assistant.tts import MlxTransientError
            raise MlxTransientError("simulated")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-mp3")
        return out_path
    return _synthesize


@pytest.mark.asyncio
async def test_skip_audio_preserves_existing_paths(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    # Seed existing paths on disk.
    existing = storage.get_paper(paper.metadata.paper_id)
    existing.audio_path = "audio/2503.10291.mp3"
    existing.transcript_path = "transcripts/2503.10291.md"
    storage.add_paper(existing)

    result = await render_audio_assets(
        config=config,
        storage=storage,
        paper=existing,
        source_markdown="# One-Pager\nBody",
        skip_transcript=False,
        skip_audio=True,
    )

    assert result.audio_path == config.data_dir / "audio/2503.10291.mp3"
    assert result.transcript_path == config.data_dir / "transcripts/2503.10291.md"
    assert result.backend_used is None


@pytest.mark.asyncio
async def test_provided_script_short_circuits_generation(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch(
            "paper_assistant.audio_assets.get_tts_backend"
        ) as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ) as gen_script,
    ):
        audio_out = config.audio_dir / "2503.10291.mp3"
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=False,
            skip_audio=False,
            provided_script_markdown="My curated narration script.",
        )

    assert result.transcript_path == config.transcripts_dir / "2503.10291.md"
    assert result.transcript_path.read_text(encoding="utf-8") == "My curated narration script."
    assert result.audio_path is not None
    assert result.backend_used == "mlx"
    gen_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_script_model_override_forwarded(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    from paper_assistant.audio_script import AudioScriptResult

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
            return_value=AudioScriptResult(
                script_markdown="Generated narration.",
                model_used="claude-test-override",
            ),
        ) as gen_script,
    ):
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(
            config.audio_dir / "2503.10291.mp3"
        )
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=False,
            skip_audio=False,
            script_model_override="claude-test-override",
        )

    gen_script.assert_awaited_once()
    assert gen_script.await_args.kwargs["model"] == "claude-test-override"
    assert result.script_model == "claude-test-override"


@pytest.mark.asyncio
async def test_script_failure_falls_back_to_raw_summary(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    from paper_assistant.audio_script import AudioScriptError

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
            side_effect=AudioScriptError("boom"),
        ),
    ):
        audio_out = config.audio_dir / "2503.10291.mp3"
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=False,
            skip_audio=False,
        )

    # Audio should still render via the raw-summary fallback path.
    assert result.audio_path is not None
    assert result.transcript_path is None
    assert any("Transcript generation failed" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_skip_script_generation_uses_raw_summary_without_api_call(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_assets._try_generate_script",
            new_callable=AsyncMock,
        ) as try_script,
    ):
        audio_out = config.audio_dir / "2503.10291.mp3"
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=False,
            skip_audio=False,
            skip_script_generation=True,
        )

    try_script.assert_not_awaited()
    assert result.audio_path is not None
    assert result.transcript_path is None
    assert any("Skipped narration script generation" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_empty_provided_script_with_skip_generation_stays_on_raw_summary(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_assets._try_generate_script",
            new_callable=AsyncMock,
        ) as try_script,
    ):
        audio_out = config.audio_dir / "2503.10291.mp3"
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=False,
            skip_audio=False,
            provided_script_markdown="   \n",
            skip_script_generation=True,
        )

    try_script.assert_not_awaited()
    assert result.audio_path is not None
    assert result.transcript_path is None
    assert any("Provided transcript was empty" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_mlx_transient_falls_back_to_edge(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_primary,
        patch("paper_assistant.audio_assets.get_edge_backend") as get_edge,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ),
    ):
        from paper_assistant.tts import MlxTransientError

        primary = AsyncMock()
        primary.name = "mlx"
        primary.synthesize.side_effect = MlxTransientError("connect refused")
        get_primary.return_value = primary

        audio_out = config.audio_dir / "2503.10291.mp3"
        edge = AsyncMock()
        edge.name = "edge"
        edge.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_edge.return_value = edge

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=True,
            skip_audio=False,
        )

    assert result.backend_used == "edge"
    assert result.audio_path is not None
    assert any("falling back to edge-tts" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_mlx_quality_failure_falls_back_to_edge(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_primary,
        patch("paper_assistant.audio_assets.get_edge_backend") as get_edge,
    ):
        from paper_assistant.tts import MlxQualityError

        primary = AsyncMock()
        primary.name = "mlx"
        primary.synthesize.side_effect = MlxQualityError("truncated")
        get_primary.return_value = primary

        edge = AsyncMock()
        edge.name = "edge"
        edge.synthesize.side_effect = _fake_mlx_backend(
            config.audio_dir / "2503.10291.mp3"
        )
        get_edge.return_value = edge

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=True,
            skip_audio=False,
        )

    assert result.backend_used == "edge"
    assert result.audio_path is not None
    assert any("truncated" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_failed_regeneration_preserves_existing_audio(config, storage):
    paper = _paper()
    storage.add_paper(paper)
    existing = storage.get_paper(paper.metadata.paper_id)
    existing.audio_path = "audio/2503.10291.mp3"
    storage.add_paper(existing)
    audio_path = config.data_dir / existing.audio_path
    audio_path.write_bytes(b"existing-audio")

    async def fail_after_partial_write(_text: str, out_path: Path):
        from paper_assistant.tts import MlxQualityError

        out_path.write_bytes(b"partial-mlx")
        raise MlxQualityError("truncated")

    async def edge_fail_after_partial_write(_text: str, out_path: Path):
        from paper_assistant.tts import EdgeTTSError

        out_path.write_bytes(b"partial-edge")
        raise EdgeTTSError("offline")

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_primary,
        patch("paper_assistant.audio_assets.get_edge_backend") as get_edge,
    ):
        primary = AsyncMock()
        primary.name = "mlx"
        primary.synthesize.side_effect = fail_after_partial_write
        get_primary.return_value = primary

        edge = AsyncMock()
        edge.name = "edge"
        edge.synthesize.side_effect = edge_fail_after_partial_write
        get_edge.return_value = edge

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=existing,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=True,
            skip_audio=False,
        )

    assert result.backend_used is None
    assert result.audio_path == audio_path
    assert audio_path.read_bytes() == b"existing-audio"


@pytest.mark.asyncio
async def test_mlx_config_error_suppresses_fallback(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_primary,
        patch("paper_assistant.audio_assets.get_edge_backend") as get_edge,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ),
    ):
        from paper_assistant.tts import MlxConfigError

        primary = AsyncMock()
        primary.name = "mlx"
        primary.synthesize.side_effect = MlxConfigError("bad model")
        get_primary.return_value = primary

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=True,
            skip_audio=False,
        )

    assert result.backend_used is None
    assert result.audio_path is None  # no prior audio to preserve
    get_edge.assert_not_called()
    assert any("MLX TTS rejected" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_skip_transcript_true_still_regenerates_audio(config, storage):
    paper = _paper()
    storage.add_paper(paper)

    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ) as gen_script,
    ):
        audio_out = config.audio_dir / "2503.10291.mp3"
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _fake_mlx_backend(audio_out)
        get_backend.return_value = fake

        result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown="# One-Pager\nRaw body",
            skip_transcript=True,
            skip_audio=False,
        )

    gen_script.assert_not_awaited()
    assert result.audio_path is not None
    assert result.transcript_path is None
