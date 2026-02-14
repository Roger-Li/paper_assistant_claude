"""Tests for Notion sync behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.notion import NotionPaper, sync_notion
from paper_assistant.storage import StorageManager
from paper_assistant.summarizer import SummarizationResult, format_summary_file


def _make_metadata(arxiv_id: str = "2503.10291", title: str = "Sample Paper") -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors=["Alice", "Bob"],
        abstract="Abstract",
        published=datetime(2025, 3, 13, tzinfo=timezone.utc),
        categories=["cs.LG"],
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def _make_config(tmp_path: Path) -> Config:
    cfg = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
        notion_sync_enabled=True,
        notion_token="secret_test",
        notion_database_id="db_test",
    )
    cfg.ensure_dirs()
    return cfg


class FakeNotionClient:
    def __init__(self, remote_papers: list[NotionPaper]):
        self.remote_papers = remote_papers
        self.created_calls: list[str] = []
        self.updated_calls: list[str] = []
        self.archived_calls: list[str] = []
        self.audio_calls: list[str] = []
        self.fail_audio_upload = False

    async def list_papers(self) -> list[NotionPaper]:
        return list(self.remote_papers)

    async def create_page(self, *, paper, summary_markdown, summary_modified_at, include_audio):
        self.created_calls.append(paper.metadata.arxiv_id)
        created = NotionPaper(
            page_id=f"page-{paper.metadata.arxiv_id}",
            arxiv_id=paper.metadata.arxiv_id,
            title=paper.metadata.title,
            authors=paper.metadata.authors,
            tags=paper.tags,
            reading_status=paper.reading_status.value,
            summary_markdown=summary_markdown,
            summary_last_modified=summary_modified_at,
            local_last_modified=paper.local_modified_at,
            archived=False,
            notion_last_edited_time=datetime.now(timezone.utc),
        )
        self.remote_papers.append(created)
        return created

    async def update_page(
        self,
        *,
        page_id,
        paper,
        summary_markdown,
        summary_modified_at,
        include_audio,
        archived,
    ):
        self.updated_calls.append(page_id)
        return NotionPaper(
            page_id=page_id,
            arxiv_id=paper.metadata.arxiv_id,
            title=paper.metadata.title,
            authors=paper.metadata.authors,
            tags=paper.tags,
            reading_status=paper.reading_status.value,
            summary_markdown=summary_markdown,
            summary_last_modified=summary_modified_at,
            local_last_modified=paper.local_modified_at,
            archived=archived,
            notion_last_edited_time=datetime.now(timezone.utc),
        )

    async def set_archived(self, page_id: str, archived: bool) -> None:
        if archived:
            self.archived_calls.append(page_id)

    async def attach_audio(self, page_id: str, audio_path: Path) -> None:
        if self.fail_audio_upload:
            raise RuntimeError("mock audio upload failed")
        self.audio_calls.append(f"{page_id}:{audio_path.name}")


def _save_summary(storage: StorageManager, paper: Paper, markdown: str) -> None:
    storage.add_paper(paper)
    result = SummarizationResult(
        full_markdown=markdown,
        one_pager=markdown,
        sections={"One-Pager": markdown},
        model_used="manual",
    )
    storage.save_summary(paper.metadata.arxiv_id, format_summary_file(paper.metadata, result))
    reloaded = storage.get_paper(paper.metadata.arxiv_id)
    reloaded.status = ProcessingStatus.COMPLETE
    storage.add_paper(reloaded)


@pytest.mark.asyncio
async def test_sync_local_only_creates_notion_record(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)
    paper = Paper(metadata=_make_metadata())
    _save_summary(storage, paper, "# One-Pager\nLocal summary")

    fake_client = FakeNotionClient(remote_papers=[])
    report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.notion_created == 1
    updated = storage.get_paper("2503.10291")
    assert updated.notion_page_id == "page-2503.10291"


@pytest.mark.asyncio
async def test_sync_remote_newer_pulls_summary_tags_status(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)
    paper = Paper(metadata=_make_metadata(), tags=["local"], reading_status=ReadingStatus.UNREAD)
    _save_summary(storage, paper, "# One-Pager\nOld summary")

    old = datetime.now(timezone.utc) - timedelta(days=2)
    p = storage.get_paper("2503.10291")
    p.local_modified_at = old
    storage.add_paper(p)

    remote = NotionPaper(
        page_id="page-1",
        arxiv_id="2503.10291",
        title="Sample Paper",
        authors=["Alice", "Bob"],
        tags=["remote-tag"],
        reading_status="read",
        summary_markdown="# One-Pager\nNew summary from notion",
        summary_last_modified=datetime.now(timezone.utc),
        local_last_modified=old,
        archived=False,
        notion_last_edited_time=datetime.now(timezone.utc),
    )

    fake_client = FakeNotionClient(remote_papers=[remote])
    report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.local_updated == 1
    updated = storage.get_paper("2503.10291")
    assert updated.tags == ["remote-tag"]
    assert updated.reading_status == ReadingStatus.READ
    summary_text = (config.data_dir / updated.summary_path).read_text(encoding="utf-8")
    assert "New summary from notion" in summary_text


@pytest.mark.asyncio
async def test_sync_local_newer_pushes_update(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)
    now = datetime.now(timezone.utc)

    paper = Paper(
        metadata=_make_metadata(),
        notion_page_id="page-1",
        local_modified_at=now,
        tags=["local"],
    )
    _save_summary(storage, paper, "# One-Pager\nLocal newer")
    saved = storage.get_paper("2503.10291")
    saved.notion_page_id = "page-1"
    saved.local_modified_at = now
    storage.add_paper(saved)

    remote = NotionPaper(
        page_id="page-1",
        arxiv_id="2503.10291",
        title="Sample Paper",
        authors=["Alice", "Bob"],
        tags=["remote"],
        reading_status="unread",
        summary_markdown="# One-Pager\nOld remote",
        summary_last_modified=now - timedelta(days=1),
        local_last_modified=now - timedelta(days=1),
        archived=False,
        notion_last_edited_time=now - timedelta(days=1),
    )

    fake_client = FakeNotionClient(remote_papers=[remote])
    report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.notion_updated == 1
    assert fake_client.updated_calls == ["page-1"]


@pytest.mark.asyncio
async def test_sync_imports_notion_only_record(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)

    remote = NotionPaper(
        page_id="page-new",
        arxiv_id="2502.00001",
        title="Remote Paper",
        authors=["Remote Author"],
        tags=["remote"],
        reading_status="unread",
        summary_markdown="# One-Pager\nImported summary",
        summary_last_modified=datetime.now(timezone.utc),
        local_last_modified=None,
        archived=False,
        notion_last_edited_time=datetime.now(timezone.utc),
    )

    fake_client = FakeNotionClient(remote_papers=[remote])
    with patch(
        "paper_assistant.notion.fetch_metadata",
        new_callable=AsyncMock,
        return_value=_make_metadata(arxiv_id="2502.00001", title="Remote Paper"),
    ):
        report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.local_created == 1
    imported = storage.get_paper("2502.00001")
    assert imported is not None
    assert imported.notion_page_id == "page-new"
    assert imported.summary_path is not None


@pytest.mark.asyncio
async def test_sync_archive_propagates_from_notion(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)
    paper = Paper(metadata=_make_metadata(), notion_page_id="page-arch")
    _save_summary(storage, paper, "# One-Pager\nArchive me")
    saved = storage.get_paper("2503.10291")
    saved.notion_page_id = "page-arch"
    storage.add_paper(saved)

    remote = NotionPaper(
        page_id="page-arch",
        arxiv_id="2503.10291",
        title="Sample Paper",
        authors=["Alice", "Bob"],
        tags=[],
        reading_status="archived",
        summary_markdown="# One-Pager\nArchive me",
        summary_last_modified=datetime.now(timezone.utc),
        local_last_modified=None,
        archived=True,
        notion_last_edited_time=datetime.now(timezone.utc),
    )

    fake_client = FakeNotionClient(remote_papers=[remote])
    report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.local_archived == 1
    updated = storage.get_paper("2503.10291")
    assert updated.reading_status == ReadingStatus.ARCHIVED
    assert updated.archived_at is not None


@pytest.mark.asyncio
async def test_sync_continues_when_audio_upload_fails(tmp_path):
    config = _make_config(tmp_path)
    storage = StorageManager(config)
    paper = Paper(metadata=_make_metadata())
    _save_summary(storage, paper, "# One-Pager\nAudio test")

    local = storage.get_paper("2503.10291")
    audio_file = config.data_dir / "audio" / "2503.10291.mp3"
    audio_file.write_bytes(b"fake mp3 bytes")
    local.audio_path = "audio/2503.10291.mp3"
    storage.add_paper(local)

    fake_client = FakeNotionClient(remote_papers=[])
    fake_client.fail_audio_upload = True

    report = await sync_notion(config=config, storage=storage, notion_client=fake_client)

    assert report.notion_created == 1
    assert report.warnings
    assert "Audio upload failed" in report.warnings[0]
