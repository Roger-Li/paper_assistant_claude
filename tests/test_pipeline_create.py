"""Tests for ``pipeline.create_local_entry`` script-file plumbing."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from paper_assistant.config import Config
from paper_assistant.models import SourceType
from paper_assistant.pipeline import create_local_entry
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


def _fake_backend(config):
    async def _synthesize(text, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-mp3")
        return out_path

    fake = AsyncMock()
    fake.name = "mlx"
    fake.synthesize.side_effect = _synthesize
    return fake


@pytest.mark.asyncio
async def test_create_with_provided_script_skips_generation(config, storage):
    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ) as gen_script,
        patch("paper_assistant.podcast.generate_feed", new=Mock()),
    ):
        get_backend.return_value = _fake_backend(config)

        outcome = await create_local_entry(
            config=config,
            storage=storage,
            title="Survey Note",
            markdown="# One-Pager\nSynthesis body",
            provided_script_markdown="Curated synthesis narration.",
            skip_script_generation=True,
        )

    paper = outcome.paper
    assert paper.metadata.source_type == SourceType.NOTE
    assert paper.metadata.paper_id == "survey-note"
    transcript_path = config.data_dir / paper.transcript_path
    assert transcript_path.read_text(encoding="utf-8") == "Curated synthesis narration."
    assert paper.audio_path is not None
    gen_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_slug_dedupe_with_script_params(config, storage):
    with (
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_script.generate_audio_script",
            new_callable=AsyncMock,
        ),
        patch("paper_assistant.podcast.generate_feed", new=Mock()),
    ):
        get_backend.return_value = _fake_backend(config)

        first = await create_local_entry(
            config=config,
            storage=storage,
            title="Survey Note",
            markdown="# One-Pager\nFirst",
            skip_audio=True,
        )
        second = await create_local_entry(
            config=config,
            storage=storage,
            title="Survey Note",
            markdown="# One-Pager\nSecond",
            provided_script_markdown="Second narration.",
            skip_script_generation=True,
        )

    assert first.paper.metadata.paper_id == "survey-note"
    assert second.paper.metadata.paper_id == "survey-note-2"
    transcript_path = config.data_dir / second.paper.transcript_path
    assert transcript_path.read_text(encoding="utf-8") == "Second narration."
