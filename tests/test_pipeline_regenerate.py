"""Tests for paper_assistant.pipeline.regenerate_transcript_and_audio."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from paper_assistant.audio_assets import AudioAssetsResult
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus
from paper_assistant.pipeline import regenerate_transcript_and_audio
from paper_assistant.storage import StorageManager


@pytest.fixture
def config(tmp_data_dir):
    cfg = Config(anthropic_api_key="k", data_dir=tmp_data_dir, icloud_sync=False)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def storage(config):
    return StorageManager(config)


@pytest.fixture
def paper_with_summary(storage, config):
    meta = PaperMetadata(
        arxiv_id="2503.10291",
        title="Test Paper",
        authors=["Alice"],
        abstract="Abstract",
    )
    paper = Paper(metadata=meta, status=ProcessingStatus.AUDIO_GENERATED)
    storage.add_paper(paper)
    storage.save_summary("2503.10291", "# One-Pager\nBody")
    return storage.get_paper("2503.10291")


@pytest.mark.asyncio
async def test_regenerate_restores_complete_status(config, storage, paper_with_summary):
    """Successful regeneration must leave the paper in COMPLETE, not AUDIO_GENERATED."""
    audio_rel = "audio/2503.10291.mp3"
    transcript_rel = "transcripts/2503.10291.md"

    async def fake_render(*, paper, **_kwargs):
        # Mirror how audio_assets persists paths + leaves status AUDIO_GENERATED.
        current = storage.get_paper(paper.metadata.paper_id)
        current.audio_path = audio_rel
        current.transcript_path = transcript_rel
        current.status = ProcessingStatus.AUDIO_GENERATED
        storage.add_paper(current)
        return AudioAssetsResult(
            audio_path=config.data_dir / audio_rel,
            transcript_path=config.data_dir / transcript_rel,
            backend_used="mlx",
            script_model="claude-test",
            warnings=[],
        )

    with (
        patch("paper_assistant.pipeline.render_audio_assets", new=AsyncMock(side_effect=fake_render)),
        patch("paper_assistant.pipeline.generate_feed"),
    ):
        result = await regenerate_transcript_and_audio(
            config=config, storage=storage, paper_id="2503.10291"
        )

    assert result.audio_path is not None
    final = storage.get_paper("2503.10291")
    assert final.status == ProcessingStatus.COMPLETE


@pytest.mark.asyncio
async def test_regenerate_without_audio_does_not_promote_status(config, storage, paper_with_summary):
    """When audio fails (no audio_path), status must not be forced to COMPLETE."""

    async def fake_render(*, paper, **_kwargs):
        return AudioAssetsResult(
            audio_path=None,
            transcript_path=None,
            backend_used=None,
            script_model=None,
            warnings=["MLX TTS rejected the request"],
        )

    with (
        patch("paper_assistant.pipeline.render_audio_assets", new=AsyncMock(side_effect=fake_render)),
        patch("paper_assistant.pipeline.generate_feed"),
    ):
        result = await regenerate_transcript_and_audio(
            config=config, storage=storage, paper_id="2503.10291"
        )

    assert result.audio_path is None
    final = storage.get_paper("2503.10291")
    # Audio failed → no audio_path → status must not be promoted to COMPLETE.
    assert final.status != ProcessingStatus.COMPLETE
