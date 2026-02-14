"""Tests for paper_assistant.storage."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.storage import (
    StorageManager,
    make_audio_filename,
    make_pdf_filename,
    make_summary_filename,
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


def _make_config(tmp_path: Path) -> Config:
    return Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
    )


class TestFilenaming:
    def test_summary_filename(self):
        result = make_summary_filename("2503.10291", "VisualPRM: Subtitle")
        assert result == "[Paper][2503.10291] VisualPRM - Subtitle.md"

    def test_audio_filename(self):
        assert make_audio_filename("2503.10291") == "2503.10291.mp3"

    def test_pdf_filename(self):
        assert make_pdf_filename("2503.10291") == "2503.10291.pdf"


class TestStorageManager:
    @pytest.fixture
    def storage(self, tmp_path):
        config = _make_config(tmp_path)
        config.ensure_dirs()
        return StorageManager(config)

    @pytest.fixture
    def sample_paper(self):
        return Paper(metadata=_make_metadata(), tags=["test"])

    def test_add_and_get(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        retrieved = storage.get_paper("2503.10291")
        assert retrieved is not None
        assert retrieved.metadata.arxiv_id == "2503.10291"
        assert retrieved.tags == ["test"]

    def test_get_nonexistent(self, storage):
        assert storage.get_paper("9999.99999") is None

    def test_paper_exists(self, storage, sample_paper):
        assert not storage.paper_exists("2503.10291")
        storage.add_paper(sample_paper)
        assert storage.paper_exists("2503.10291")

    def test_list_papers(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="First"))
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Second"))
        storage.add_paper(p1)
        storage.add_paper(p2)
        papers = storage.list_papers()
        assert len(papers) == 2

    def test_list_papers_filter_tag(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="A"), tags=["rl"])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="B"), tags=["cv"])
        storage.add_paper(p1)
        storage.add_paper(p2)
        rl_papers = storage.list_papers(tag="rl")
        assert len(rl_papers) == 1
        assert rl_papers[0].metadata.arxiv_id == "2501.00001"

    def test_list_papers_filter_status(self, storage):
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
        complete = storage.list_papers(status=ProcessingStatus.COMPLETE)
        assert len(complete) == 1

    def test_delete_paper(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        assert storage.delete_paper("2503.10291", delete_files=False)
        assert storage.get_paper("2503.10291") is None

    def test_delete_nonexistent(self, storage):
        assert not storage.delete_paper("9999.99999")

    def test_delete_with_files(self, storage, tmp_path):
        paper = Paper(
            metadata=_make_metadata(),
            summary_path="papers/test.md",
            audio_path="audio/2503.10291.mp3",
        )
        storage.add_paper(paper)

        # Create the files
        summary_file = tmp_path / "papers" / "test.md"
        summary_file.write_text("test content")
        audio_file = tmp_path / "audio" / "2503.10291.mp3"
        audio_file.write_bytes(b"fake audio")

        assert storage.delete_paper("2503.10291", delete_files=True)
        assert not summary_file.exists()
        assert not audio_file.exists()

    def test_add_tags(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        tags = storage.add_tags("2503.10291", ["rl", "multimodal"])
        assert "rl" in tags
        assert "multimodal" in tags
        assert "test" in tags  # original tag preserved

    def test_add_tags_no_duplicates(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        tags = storage.add_tags("2503.10291", ["test"])  # already exists
        assert tags.count("test") == 1

    def test_add_tags_nonexistent_paper(self, storage):
        with pytest.raises(KeyError):
            storage.add_tags("9999.99999", ["tag"])

    def test_remove_tag(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        tags = storage.remove_tag("2503.10291", "test")
        assert "test" not in tags

    def test_remove_tag_nonexistent_tag(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        tags = storage.remove_tag("2503.10291", "nonexistent")
        assert tags == ["test"]  # unchanged

    def test_remove_tag_nonexistent_paper(self, storage):
        with pytest.raises(KeyError):
            storage.remove_tag("9999.99999", "tag")

    def test_save_summary(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        path = storage.save_summary("2503.10291", "# Summary\nContent here")
        assert path.exists()
        assert path.read_text() == "# Summary\nContent here"

        # Check paper was updated
        paper = storage.get_paper("2503.10291")
        assert paper.summary_path is not None
        assert paper.status == ProcessingStatus.SUMMARIZED

    def test_save_summary_nonexistent_paper(self, storage):
        with pytest.raises(KeyError):
            storage.save_summary("9999.99999", "content")

    def test_index_persists_to_disk(self, storage, sample_paper, tmp_path):
        storage.add_paper(sample_paper)
        # Create a new StorageManager pointing to the same dir
        config2 = _make_config(tmp_path)
        storage2 = StorageManager(config2)
        paper = storage2.get_paper("2503.10291")
        assert paper is not None
        assert paper.tags == ["test"]

    def test_list_papers_sort_by_title(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="Zebra"))
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Apple"))
        storage.add_paper(p1)
        storage.add_paper(p2)
        papers = storage.list_papers(sort_by="title", reverse=False)
        assert papers[0].metadata.title == "Apple"
        assert papers[1].metadata.title == "Zebra"

    def test_list_papers_sort_by_title_reverse(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="Zebra"))
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Apple"))
        storage.add_paper(p1)
        storage.add_paper(p2)
        papers = storage.list_papers(sort_by="title", reverse=True)
        assert papers[0].metadata.title == "Zebra"
        assert papers[1].metadata.title == "Apple"

    def test_list_papers_sort_by_tag(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="A"), tags=["rl"])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="B"), tags=["cv"])
        p3 = Paper(metadata=_make_metadata(arxiv_id="2501.00003", title="C"), tags=[])
        storage.add_paper(p1)
        storage.add_paper(p2)
        storage.add_paper(p3)
        papers = storage.list_papers(sort_by="tag", reverse=False)
        # Empty tag sorts first (""), then "cv", then "rl"
        assert papers[0].metadata.arxiv_id == "2501.00003"
        assert papers[1].metadata.arxiv_id == "2501.00002"
        assert papers[2].metadata.arxiv_id == "2501.00001"

    def test_list_papers_sort_with_tag_filter(self, storage):
        """Sorting and tag filtering should work together."""
        p1 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="Zebra"), tags=["rl"])
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00002", title="Apple"), tags=["rl"])
        p3 = Paper(metadata=_make_metadata(arxiv_id="2501.00003", title="Mango"), tags=["cv"])
        storage.add_paper(p1)
        storage.add_paper(p2)
        storage.add_paper(p3)
        papers = storage.list_papers(tag="rl", sort_by="title", reverse=False)
        assert len(papers) == 2
        assert papers[0].metadata.title == "Apple"
        assert papers[1].metadata.title == "Zebra"

    def test_list_papers_sort_by_arxiv_id(self, storage):
        p1 = Paper(metadata=_make_metadata(arxiv_id="2503.00100", title="A"))
        p2 = Paper(metadata=_make_metadata(arxiv_id="2501.00001", title="B"))
        storage.add_paper(p1)
        storage.add_paper(p2)
        papers = storage.list_papers(sort_by="arxiv_id", reverse=False)
        assert papers[0].metadata.arxiv_id == "2501.00001"
        assert papers[1].metadata.arxiv_id == "2503.00100"

    def test_set_reading_status(self, storage, sample_paper):
        storage.add_paper(sample_paper)
        result = storage.set_reading_status("2503.10291", ReadingStatus.READ)
        assert result == ReadingStatus.READ
        paper = storage.get_paper("2503.10291")
        assert paper.reading_status == ReadingStatus.READ

    def test_set_reading_status_nonexistent(self, storage):
        with pytest.raises(KeyError):
            storage.set_reading_status("9999.99999", ReadingStatus.READ)

    def test_list_papers_filter_reading_status(self, storage):
        p1 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00001", title="A"),
            reading_status=ReadingStatus.UNREAD,
        )
        p2 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00002", title="B"),
            reading_status=ReadingStatus.READ,
        )
        p3 = Paper(
            metadata=_make_metadata(arxiv_id="2501.00003", title="C"),
            reading_status=ReadingStatus.ARCHIVED,
        )
        storage.add_paper(p1)
        storage.add_paper(p2)
        storage.add_paper(p3)
        unread = storage.list_papers(reading_status=ReadingStatus.UNREAD)
        assert len(unread) == 1
        assert unread[0].metadata.arxiv_id == "2501.00001"

    def test_index_rereads_from_disk(self, storage, tmp_path):
        """StorageManager should re-read index to support concurrent CLI/web usage."""
        storage.add_paper(Paper(metadata=_make_metadata(), tags=["original"]))

        # Simulate external modification (e.g., CLI writes while web is running)
        config2 = _make_config(tmp_path)
        storage2 = StorageManager(config2)
        storage2.add_tags("2503.10291", ["added-externally"])

        # Original storage should see the change on next read
        paper = storage.get_paper("2503.10291")
        assert "added-externally" in paper.tags
