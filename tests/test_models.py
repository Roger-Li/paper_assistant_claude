"""Tests for paper_assistant.models."""

from datetime import datetime, timezone

from paper_assistant.models import (
    Paper,
    PaperIndex,
    PaperMetadata,
    ProcessingStatus,
    ReadingStatus,
    sanitize_filename,
)


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


class TestSanitizeFilename:
    def test_basic(self):
        assert sanitize_filename("Hello World") == "Hello World"

    def test_colons_replaced(self):
        assert sanitize_filename("Title: Subtitle") == "Title - Subtitle"

    def test_invalid_chars_removed(self):
        assert sanitize_filename('A<B>C"D') == "ABCD"

    def test_truncation(self):
        long = "A" * 100
        result = sanitize_filename(long, max_length=80)
        assert len(result) <= 80

    def test_truncation_at_word_boundary(self):
        title = "Word " * 20  # 100 chars
        result = sanitize_filename(title, max_length=40)
        assert len(result) <= 40
        assert not result.endswith(" ")

    def test_whitespace_collapsed(self):
        assert sanitize_filename("A   B") == "A B"


class TestPaperMetadata:
    def test_roundtrip(self):
        meta = _make_metadata()
        data = meta.model_dump()
        restored = PaperMetadata.model_validate(data)
        assert restored.arxiv_id == "2503.10291"
        assert restored.title == "VisualPRM: An Effective Process Reward Model"


class TestPaper:
    def test_defaults(self):
        meta = _make_metadata()
        paper = Paper(metadata=meta)
        assert paper.status == ProcessingStatus.PENDING
        assert paper.tags == []
        assert paper.pdf_path is None
        assert paper.summary_path is None
        assert paper.audio_path is None
        assert paper.date_added is not None
        assert paper.local_modified_at is not None
        assert paper.notion_modified_at is None
        assert paper.last_synced_at is None
        assert paper.archived_at is None
        assert paper.notion_page_id is None

    def test_safe_title(self):
        meta = _make_metadata(title="Title: With Colon")
        paper = Paper(metadata=meta)
        assert ":" not in paper.safe_title

    def test_tags(self):
        meta = _make_metadata()
        paper = Paper(metadata=meta, tags=["rl", "multimodal"])
        assert paper.tags == ["rl", "multimodal"]

    def test_reading_status_default(self):
        meta = _make_metadata()
        paper = Paper(metadata=meta)
        assert paper.reading_status == ReadingStatus.UNREAD

    def test_reading_status_serialization(self):
        meta = _make_metadata()
        paper = Paper(metadata=meta, reading_status=ReadingStatus.READ)
        data = paper.model_dump()
        restored = Paper.model_validate(data)
        assert restored.reading_status == ReadingStatus.READ

    def test_notion_fields_serialization(self):
        meta = _make_metadata()
        now = datetime.now(timezone.utc)
        paper = Paper(
            metadata=meta,
            notion_page_id="abc123",
            notion_modified_at=now,
            last_synced_at=now,
        )
        data = paper.model_dump()
        restored = Paper.model_validate(data)
        assert restored.notion_page_id == "abc123"
        assert restored.notion_modified_at is not None
        assert restored.last_synced_at is not None


class TestPaperIndex:
    def test_empty(self):
        index = PaperIndex()
        assert index.papers == {}

    def test_add_paper(self):
        index = PaperIndex()
        paper = Paper(metadata=_make_metadata())
        index.papers["2503.10291"] = paper
        assert "2503.10291" in index.papers

    def test_serialization_roundtrip(self):
        index = PaperIndex()
        paper = Paper(metadata=_make_metadata(), tags=["test"])
        index.papers["2503.10291"] = paper
        json_str = index.model_dump_json()
        restored = PaperIndex.model_validate_json(json_str)
        assert "2503.10291" in restored.papers
        assert restored.papers["2503.10291"].tags == ["test"]
