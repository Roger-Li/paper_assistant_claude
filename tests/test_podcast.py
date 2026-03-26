"""Tests for podcast feed generation."""

from __future__ import annotations

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, SourceType
from paper_assistant.podcast import generate_feed


def test_generate_feed_uses_local_detail_page_when_no_external_url(tmp_path):
    config = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
    )
    config.ensure_dirs()
    (config.audio_dir / "local-note.mp3").write_bytes(b"fake-audio")

    paper = Paper(
        metadata=PaperMetadata(
            source_type=SourceType.NOTE,
            source_slug="local-note",
            title="Local Note",
        ),
        status=ProcessingStatus.COMPLETE,
        audio_path="audio/local-note.mp3",
    )

    feed = generate_feed(config, [paper])

    assert "http://127.0.0.1:8877/paper/local-note" in feed
