"""Tests for POST /api/paper/{id}/transcript/regenerate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus
from paper_assistant.pipeline import TranscriptRegenerateResult
from paper_assistant.storage import StorageManager
from paper_assistant.web.app import create_app


@pytest.fixture
def config(tmp_path):
    cfg = Config(anthropic_api_key="key", data_dir=tmp_path, icloud_sync=False)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def client(config):
    return TestClient(create_app(config))


@pytest.fixture
def storage(config):
    return StorageManager(config)


@pytest.fixture
def paper_with_summary(storage, config):
    metadata = PaperMetadata(
        arxiv_id="2503.10291",
        title="Test Paper",
        authors=["Alice"],
        abstract="Abstract",
    )
    paper = Paper(metadata=metadata, status=ProcessingStatus.COMPLETE)
    storage.add_paper(paper)
    storage.save_summary("2503.10291", "# One-Pager\nBody")
    return storage.get_paper("2503.10291")


def _result(config, warnings=None, backend="mlx"):
    return TranscriptRegenerateResult(
        paper_id="2503.10291",
        title="Test Paper",
        transcript_path=config.transcripts_dir / "2503.10291.md",
        audio_path=config.audio_dir / "2503.10291.mp3",
        script_model="claude-haiku-test",
        backend_used=backend,
        warnings=warnings or [],
    )


def test_transcript_regenerate_success(client, config, paper_with_summary):
    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(config)),
    ) as regen:
        resp = client.post("/api/paper/2503.10291/transcript/regenerate", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("backend_used") == "mlx"
    assert data.get("script_model") == "claude-haiku-test"
    assert "transcript_path" in data
    assert "audio_path" in data
    regen.assert_awaited_once()


def test_transcript_regenerate_with_model_override(client, config, paper_with_summary):
    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(config)),
    ) as regen:
        resp = client.post(
            "/api/paper/2503.10291/transcript/regenerate",
            json={"model": "claude-opus-test"},
        )

    assert resp.status_code == 200
    assert regen.await_args.kwargs["script_model_override"] == "claude-opus-test"


def test_transcript_regenerate_with_provided_script(client, config, paper_with_summary):
    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(config)),
    ) as regen:
        resp = client.post(
            "/api/paper/2503.10291/transcript/regenerate",
            json={"script_markdown": "Curated narration"},
        )

    assert resp.status_code == 200
    assert regen.await_args.kwargs["provided_script_markdown"] == "Curated narration"


def test_transcript_regenerate_missing_paper_returns_error(client):
    resp = client.post("/api/paper/9999.99999/transcript/regenerate", json={})

    assert resp.status_code == 200
    assert "error" in resp.json()


def test_transcript_regenerate_warnings_surfaced(client, config, paper_with_summary):
    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(config, warnings=["edge-tts fallback used"])),
    ):
        resp = client.post("/api/paper/2503.10291/transcript/regenerate", json={})

    data = resp.json()
    assert data.get("warnings") == ["edge-tts fallback used"]


def test_transcript_regenerate_paper_without_summary_errors(client, storage):
    metadata = PaperMetadata(arxiv_id="2503.99999", title="No Summary", authors=[])
    paper = Paper(metadata=metadata, status=ProcessingStatus.PENDING)
    storage.add_paper(paper)

    resp = client.post("/api/paper/2503.99999/transcript/regenerate", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
