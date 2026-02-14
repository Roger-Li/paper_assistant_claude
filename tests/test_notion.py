"""Tests for Notion sync behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.notion import NotionPaper, sync_notion, _markdown_to_blocks, _blocks_to_markdown
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


# ---------------------------------------------------------------------------
# _markdown_to_blocks conversion tests
# ---------------------------------------------------------------------------


def _find_block(blocks, block_type):
    return [b for b in blocks if b["type"] == block_type]


def _rich_text(block):
    return block[block["type"]]["rich_text"]


class TestMarkdownToBlocks:
    def test_bold_annotation(self):
        blocks = _markdown_to_blocks("Hello **bold** text")
        rt = _rich_text(blocks[0])
        bold_items = [r for r in rt if r.get("annotations", {}).get("bold")]
        assert len(bold_items) == 1
        assert bold_items[0]["text"]["content"] == "bold"

    def test_italic_annotation(self):
        blocks = _markdown_to_blocks("Hello *italic* text")
        rt = _rich_text(blocks[0])
        italic_items = [r for r in rt if r.get("annotations", {}).get("italic")]
        assert len(italic_items) == 1
        assert italic_items[0]["text"]["content"] == "italic"

    def test_inline_code_annotation(self):
        blocks = _markdown_to_blocks("Some `code` here")
        rt = _rich_text(blocks[0])
        code_items = [r for r in rt if r.get("annotations", {}).get("code")]
        assert len(code_items) == 1
        assert code_items[0]["text"]["content"] == "code"

    def test_strikethrough_annotation(self):
        blocks = _markdown_to_blocks("Some ~~deleted~~ text")
        rt = _rich_text(blocks[0])
        strike_items = [r for r in rt if r.get("annotations", {}).get("strikethrough")]
        assert len(strike_items) == 1
        assert strike_items[0]["text"]["content"] == "deleted"

    def test_link(self):
        blocks = _markdown_to_blocks("[click](https://example.com)")
        rt = _rich_text(blocks[0])
        link_items = [r for r in rt if r.get("text", {}).get("link")]
        assert len(link_items) == 1
        assert link_items[0]["text"]["link"]["url"] == "https://example.com"
        assert link_items[0]["text"]["content"] == "click"

    def test_inline_math(self):
        blocks = _markdown_to_blocks("Energy is $E=mc^2$ here")
        rt = _rich_text(blocks[0])
        eq_items = [r for r in rt if r.get("type") == "equation"]
        assert len(eq_items) == 1
        assert eq_items[0]["equation"]["expression"] == "E=mc^2"

    def test_display_math_block(self):
        blocks = _markdown_to_blocks("Before\n\n$$\n\\sum x\n$$\n\nAfter")
        eq_blocks = _find_block(blocks, "equation")
        assert len(eq_blocks) == 1
        assert eq_blocks[0]["equation"]["expression"] == "\\sum x"

    def test_inline_display_math_no_stray_dollars(self):
        """$$...$$ appearing inline should become a clean equation block (no $ in expression)."""
        blocks = _markdown_to_blocks("Energy is $$E=mc^2$$ in physics")
        eq_blocks = _find_block(blocks, "equation")
        assert len(eq_blocks) == 1
        assert eq_blocks[0]["equation"]["expression"] == "E=mc^2"
        # No rich_text item should contain a bare "$" artefact
        for b in blocks:
            btype = b["type"]
            if btype in ("paragraph",):
                for rt in b[btype]["rich_text"]:
                    content = rt.get("text", {}).get("content", "")
                    assert content.strip() != "$"

    def test_heading_levels(self):
        blocks = _markdown_to_blocks("# H1\n## H2\n### H3")
        assert blocks[0]["type"] == "heading_1"
        assert blocks[1]["type"] == "heading_2"
        assert blocks[2]["type"] == "heading_3"

    def test_bullet_list(self):
        blocks = _markdown_to_blocks("- one\n- two")
        assert all(b["type"] == "bulleted_list_item" for b in blocks)
        assert len(blocks) == 2

    def test_numbered_list(self):
        blocks = _markdown_to_blocks("1. first\n2. second")
        assert all(b["type"] == "numbered_list_item" for b in blocks)
        assert len(blocks) == 2

    def test_code_block(self):
        blocks = _markdown_to_blocks("```python\nprint('hi')\n```")
        code_blocks = _find_block(blocks, "code")
        assert len(code_blocks) == 1
        assert code_blocks[0]["code"]["language"] == "python"

    def test_quote_block(self):
        blocks = _markdown_to_blocks("> a wise quote")
        quote_blocks = _find_block(blocks, "quote")
        assert len(quote_blocks) == 1

    def test_mixed_bold_code(self):
        blocks = _markdown_to_blocks("**bold with `code` inside**")
        rt = _rich_text(blocks[0])
        bold_items = [r for r in rt if r.get("annotations", {}).get("bold")]
        code_items = [r for r in rt if r.get("annotations", {}).get("code")]
        assert len(bold_items) >= 1
        assert len(code_items) == 1

    def test_empty_markdown_fallback(self):
        blocks = _markdown_to_blocks("")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"

    def test_divider(self):
        blocks = _markdown_to_blocks("text\n\n---\n\nmore")
        divider_blocks = _find_block(blocks, "divider")
        assert len(divider_blocks) == 1

    def test_nested_bullets(self):
        blocks = _markdown_to_blocks("- parent\n  - child 1\n  - child 2")
        assert len(blocks) == 1
        parent = blocks[0]
        assert parent["type"] == "bulleted_list_item"
        children = parent["bulleted_list_item"].get("children", [])
        assert len(children) == 2
        assert children[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "child 1"
        assert children[1]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "child 2"

    def test_deep_nested_bullets(self):
        blocks = _markdown_to_blocks("- L1\n  - L2\n    - L3")
        l1 = blocks[0]
        l2 = l1["bulleted_list_item"]["children"][0]
        l3 = l2["bulleted_list_item"]["children"][0]
        assert l3["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "L3"

    def test_nested_mixed_list_types(self):
        blocks = _markdown_to_blocks("1. first\n   - sub bullet\n2. second")
        assert blocks[0]["type"] == "numbered_list_item"
        children = blocks[0]["numbered_list_item"].get("children", [])
        assert len(children) == 1
        assert children[0]["type"] == "bulleted_list_item"
        assert blocks[1]["type"] == "numbered_list_item"

    def test_nested_bullet_with_formatting(self):
        blocks = _markdown_to_blocks("- **bold** parent\n  - *italic* child")
        parent_rt = _rich_text(blocks[0])
        bold_items = [r for r in parent_rt if r.get("annotations", {}).get("bold")]
        assert len(bold_items) == 1
        children = blocks[0]["bulleted_list_item"]["children"]
        child_rt = children[0]["bulleted_list_item"]["rich_text"]
        italic_items = [r for r in child_rt if r.get("annotations", {}).get("italic")]
        assert len(italic_items) == 1


class TestBlocksToMarkdown:
    def test_nested_list(self):
        blocks = [
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"plain_text": "parent"}],
                    "children": [
                        {
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [{"plain_text": "child"}],
                            },
                        }
                    ],
                },
            }
        ]
        md = _blocks_to_markdown(blocks)
        assert "- parent" in md
        assert "  - child" in md

    def test_deep_nested_list(self):
        blocks = [
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"plain_text": "L1"}],
                    "children": [
                        {
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [{"plain_text": "L2"}],
                                "children": [
                                    {
                                        "type": "bulleted_list_item",
                                        "bulleted_list_item": {
                                            "rich_text": [{"plain_text": "L3"}],
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            }
        ]
        md = _blocks_to_markdown(blocks)
        assert "- L1" in md
        assert "  - L2" in md
        assert "    - L3" in md

    def test_equation_block(self):
        blocks = [
            {
                "type": "equation",
                "equation": {"expression": "E=mc^2"},
            }
        ]
        md = _blocks_to_markdown(blocks)
        assert "$$" in md
        assert "E=mc^2" in md

    def test_divider_block(self):
        blocks = [
            {"type": "divider", "divider": {}},
        ]
        md = _blocks_to_markdown(blocks)
        assert "---" in md
