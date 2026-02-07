"""Tests for paper_assistant.web API endpoints."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus
from paper_assistant.storage import StorageManager
from paper_assistant.web.app import create_app


def _make_metadata(**overrides):
    defaults = {
        "arxiv_id": "2503.10291",
        "title": "VisualPRM: An Effective Process Reward Model",
        "authors": ["Alice", "Bob"],
        "abstract": "We propose...",
        "published": datetime(2025, 3, 13, tzinfo=timezone.utc),
        "categories": ["cs.CV"],
        "arxiv_url": "https://arxiv.org/abs/2503.10291",
        "pdf_url": "https://arxiv.org/pdf/2503.10291",
    }
    defaults.update(overrides)
    return PaperMetadata(**defaults)


@pytest.fixture
def config(tmp_path):
    cfg = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def storage(config):
    return StorageManager(config)


@pytest.fixture
def paper_in_index(storage):
    paper = Paper(
        metadata=_make_metadata(),
        status=ProcessingStatus.COMPLETE,
        tags=["test"],
    )
    storage.add_paper(paper)
    return paper


class TestIndexPage:
    def test_empty_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "No papers yet" in resp.text

    def test_with_papers(self, client, paper_in_index):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "2503.10291" in resp.text
        assert "VisualPRM" in resp.text

    def test_tag_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="Paper A"),
            tags=["rl"],
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="Paper B"),
            tags=["cv"],
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/?tag=rl")
        assert resp.status_code == 200
        assert "Paper A" in resp.text
        assert "Paper B" not in resp.text


class TestPaperDetailPage:
    def test_existing_paper(self, client, paper_in_index):
        resp = client.get("/paper/2503.10291")
        assert resp.status_code == 200
        assert "VisualPRM" in resp.text

    def test_nonexistent_paper(self, client):
        resp = client.get("/paper/9999.99999")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_with_summary(self, client, storage, config):
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# Summary\nTest content")

        resp = client.get("/paper/2503.10291")
        assert resp.status_code == 200
        assert "Test content" in resp.text


class TestApiListPapers:
    def test_empty(self, client):
        resp = client.get("/api/papers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_papers(self, client, paper_in_index):
        resp = client.get("/api/papers")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["arxiv_id"] == "2503.10291"
        assert data[0]["tags"] == ["test"]

    def test_tag_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="A"),
            tags=["rl"],
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="B"),
            tags=["cv"],
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/api/papers?tag=rl")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["arxiv_id"] == "2501.00001"


class TestApiTags:
    def test_add_tags(self, client, paper_in_index):
        resp = client.post(
            "/api/paper/2503.10291/tags",
            json={"tags": ["rl", "multimodal"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rl" in data["tags"]
        assert "multimodal" in data["tags"]
        assert "test" in data["tags"]

    def test_add_tags_nonexistent_paper(self, client):
        resp = client.post(
            "/api/paper/9999.99999/tags",
            json={"tags": ["test"]},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_remove_tag(self, client, paper_in_index):
        resp = client.delete("/api/paper/2503.10291/tags/test")
        assert resp.status_code == 200
        data = resp.json()
        assert "test" not in data["tags"]

    def test_remove_tag_nonexistent_paper(self, client):
        resp = client.delete("/api/paper/9999.99999/tags/test")
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestApiDeletePaper:
    def test_delete_existing(self, client, paper_in_index):
        resp = client.delete("/api/paper/2503.10291")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify paper is gone
        resp2 = client.get("/api/papers")
        assert len(resp2.json()) == 0

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/paper/9999.99999")
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestApiImport:
    def test_import_success(self, client, config):
        mock_metadata = _make_metadata()
        with (
            patch("paper_assistant.arxiv.parse_arxiv_url", return_value="2503.10291"),
            patch("paper_assistant.arxiv.fetch_metadata", new_callable=AsyncMock, return_value=mock_metadata),
            patch("paper_assistant.tts.text_to_speech", new_callable=AsyncMock),
            patch("paper_assistant.podcast.generate_feed", return_value="<rss/>"),
        ):
            resp = client.post(
                "/api/import",
                json={
                    "url": "https://arxiv.org/abs/2503.10291",
                    "markdown": "# One-Pager\nSummary content\n# Detailed Analysis\nMore content",
                    "tags": ["test"],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["arxiv_id"] == "2503.10291"

    def test_import_invalid_url(self, client):
        resp = client.post(
            "/api/import",
            json={
                "url": "not-a-url",
                "markdown": "content",
            },
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_import_duplicate_paper(self, client, paper_in_index):
        resp = client.post(
            "/api/import",
            json={
                "url": "https://arxiv.org/abs/2503.10291",
                "markdown": "content",
            },
        )
        assert resp.status_code == 200
        assert "already exists" in resp.json()["error"]

    def test_import_missing_fields(self, client):
        resp = client.post(
            "/api/import",
            json={"url": "https://arxiv.org/abs/2503.10291"},
        )
        assert resp.status_code == 422
