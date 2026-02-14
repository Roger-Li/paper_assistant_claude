"""Tests for paper_assistant.web API endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from paper_assistant.arxiv import ArxivRateLimitError
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
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

    def test_sort_by_title(self, client, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="Zebra Paper"), tags=[])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Apple Paper"), tags=[])
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/?sort=title&order=asc")
        assert resp.status_code == 200
        apple_pos = resp.text.index("Apple Paper")
        zebra_pos = resp.text.index("Zebra Paper")
        assert apple_pos < zebra_pos

    def test_sort_invalid_field_defaults(self, client, paper_in_index):
        """Invalid sort field should fall back to date_added."""
        resp = client.get("/?sort=invalid_field")
        assert resp.status_code == 200
        assert "2503.10291" in resp.text


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

    def test_sort_by_title(self, client, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="Zebra"), tags=[])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Apple"), tags=[])
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/api/papers?sort=title&order=asc")
        data = resp.json()
        assert data[0]["title"] == "Apple"
        assert data[1]["title"] == "Zebra"


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

    def test_import_arxiv_rate_limit_error_message(self, client):
        with (
            patch("paper_assistant.arxiv.parse_arxiv_url", return_value="2503.10291"),
            patch(
                "paper_assistant.arxiv.fetch_metadata",
                new_callable=AsyncMock,
                side_effect=ArxivRateLimitError(attempts=3, retry_after_seconds=45),
            ),
        ):
            resp = client.post(
                "/api/import",
                json={"url": "https://arxiv.org/abs/2503.10291", "markdown": "# Summary\nbody"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "rate limit" in data["error"].lower()


class TestApiUpdateSummary:
    def test_update_summary_success(self, client, storage, config):
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# Old Summary\nOld content")

        with (
            patch("paper_assistant.tts.text_to_speech", new_callable=AsyncMock),
            patch("paper_assistant.podcast.generate_feed", return_value="<rss/>"),
        ):
            resp = client.put(
                "/api/paper/2503.10291/summary",
                json={"markdown": "# New Summary\nNew content", "regenerate_audio": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # Verify file was updated
        paper = storage.get_paper("2503.10291")
        summary_path = config.data_dir / paper.summary_path
        assert "New content" in summary_path.read_text()

    def test_update_summary_skip_audio(self, client, storage, config):
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# Old\nOld")

        with patch("paper_assistant.podcast.generate_feed", return_value="<rss/>"):
            resp = client.put(
                "/api/paper/2503.10291/summary",
                json={"markdown": "# New\nNew", "regenerate_audio": False},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_update_summary_nonexistent_paper(self, client):
        resp = client.put(
            "/api/paper/9999.99999/summary",
            json={"markdown": "content"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_update_summary_empty_markdown(self, client, storage):
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# Old\nOld")

        resp = client.put(
            "/api/paper/2503.10291/summary",
            json={"markdown": "   ", "regenerate_audio": False},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_update_summary_audio_failure_graceful(self, client, storage, config):
        """Audio failure should not prevent summary from being saved."""
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# Old\nOld")

        with (
            patch(
                "paper_assistant.tts.text_to_speech",
                new_callable=AsyncMock,
                side_effect=RuntimeError("TTS broke"),
            ),
            patch("paper_assistant.podcast.generate_feed", return_value="<rss/>"),
        ):
            resp = client.put(
                "/api/paper/2503.10291/summary",
                json={"markdown": "# New\nNew content", "regenerate_audio": True},
            )
        data = resp.json()
        assert data["status"] == "ok"
        assert "warning" in data

        # Summary should still be saved
        paper = storage.get_paper("2503.10291")
        summary_path = config.data_dir / paper.summary_path
        assert "New content" in summary_path.read_text()

    def test_get_raw_summary(self, client, storage):
        paper = Paper(metadata=_make_metadata(), status=ProcessingStatus.COMPLETE)
        storage.add_paper(paper)
        storage.save_summary("2503.10291", "# One-Pager\nSummary body text")

        resp = client.get("/api/paper/2503.10291/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "Summary body text" in data["markdown"]

    def test_get_summary_no_paper(self, client):
        resp = client.get("/api/paper/9999.99999/summary")
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestApiSortByArxivId:
    def test_sort_by_arxiv_id(self, client, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2503.00100", title="A"), tags=[])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="B"), tags=[])
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/api/papers?sort=arxiv_id&order=asc")
        data = resp.json()
        assert data[0]["arxiv_id"] == "2501.00001"
        assert data[1]["arxiv_id"] == "2503.00100"


class TestStatusFilter:
    def test_index_status_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="Complete Paper"),
            status=ProcessingStatus.COMPLETE,
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="Pending Paper"),
            status=ProcessingStatus.PENDING,
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/?status=complete")
        assert resp.status_code == 200
        assert "Complete Paper" in resp.text
        assert "Pending Paper" not in resp.text

    def test_api_status_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="A"),
            status=ProcessingStatus.COMPLETE,
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="B"),
            status=ProcessingStatus.PENDING,
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/api/papers?status=complete")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["arxiv_id"] == "2501.00001"

    def test_index_reading_status_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="Unread Paper"),
            reading_status=ReadingStatus.UNREAD,
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="Read Paper"),
            reading_status=ReadingStatus.READ,
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/?reading_status=unread")
        assert resp.status_code == 200
        assert "Unread Paper" in resp.text
        assert "Read Paper" not in resp.text

    def test_api_reading_status_filter(self, client, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="A"),
            reading_status=ReadingStatus.UNREAD,
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="B"),
            reading_status=ReadingStatus.READ,
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        resp = client.get("/api/papers?reading_status=unread")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["arxiv_id"] == "2501.00001"

    def test_api_response_includes_reading_status(self, client, paper_in_index):
        resp = client.get("/api/papers")
        data = resp.json()
        assert data[0]["reading_status"] == "unread"


class TestApiReadingStatus:
    def test_set_reading_status(self, client, paper_in_index):
        resp = client.put(
            "/api/paper/2503.10291/reading-status",
            json={"reading_status": "read"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["reading_status"] == "read"

    def test_set_invalid_reading_status(self, client, paper_in_index):
        resp = client.put(
            "/api/paper/2503.10291/reading-status",
            json={"reading_status": "invalid"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_set_reading_status_nonexistent_paper(self, client):
        resp = client.put(
            "/api/paper/9999.99999/reading-status",
            json={"reading_status": "read"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()
