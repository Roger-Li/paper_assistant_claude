"""File naming, index management, and paper CRUD operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from paper_assistant.config import Config
from paper_assistant.models import (
    Paper,
    PaperIndex,
    ProcessingStatus,
    ReadingStatus,
    sanitize_filename,
)


def make_summary_filename(arxiv_id: str, title: str) -> str:
    """Generate the standard summary filename.

    Example: [Paper][2503.10291] VisualPRM - An Effective Process Reward Model.md
    """
    safe = sanitize_filename(title)
    return f"[Paper][{arxiv_id}] {safe}.md"


def make_audio_filename(arxiv_id: str) -> str:
    """Generate audio filename. Example: 2503.10291.mp3"""
    return f"{arxiv_id}.mp3"


def make_pdf_filename(arxiv_id: str) -> str:
    """Generate PDF cache filename. Example: 2503.10291.pdf"""
    return f"{arxiv_id}.pdf"


class StorageManager:
    """Manages the paper index and file organization."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._index: PaperIndex | None = None

    def load_index(self) -> PaperIndex:
        """Load index from disk, always re-reading to pick up external changes."""
        if self.config.index_path.exists():
            data = json.loads(self.config.index_path.read_text())
            self._index = PaperIndex.model_validate(data)
        elif self._index is None:
            self._index = PaperIndex()

        return self._index

    def save_index(self) -> None:
        """Persist the current index to disk."""
        if self._index is None:
            return

        self._index.last_updated = datetime.now(timezone.utc)
        self.config.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.index_path.write_text(
            self._index.model_dump_json(indent=2)
        )

    def add_paper(self, paper: Paper) -> None:
        """Add or update a paper in the index."""
        index = self.load_index()
        index.papers[paper.metadata.arxiv_id] = paper
        self.save_index()

    @staticmethod
    def _mark_local_modified(paper: Paper, modified_at: datetime | None = None) -> None:
        """Update local sync timestamp for user-editable fields."""
        paper.local_modified_at = modified_at or datetime.now(timezone.utc)

    def get_paper(self, arxiv_id: str) -> Paper | None:
        """Retrieve a paper by arXiv ID."""
        index = self.load_index()
        return index.papers.get(arxiv_id)

    def list_papers(
        self,
        status: ProcessingStatus | None = None,
        reading_status: ReadingStatus | None = None,
        tag: str | None = None,
        sort_by: str = "date_added",
        reverse: bool = True,
    ) -> list[Paper]:
        """List papers with optional filtering and sorting."""
        index = self.load_index()
        papers = list(index.papers.values())

        if status is not None:
            papers = [p for p in papers if p.status == status]

        if reading_status is not None:
            papers = [p for p in papers if p.reading_status == reading_status]

        if tag is not None:
            papers = [p for p in papers if tag in p.tags]

        def sort_key(p: Paper) -> object:
            if sort_by == "title":
                return p.metadata.title.lower()
            if sort_by == "tag":
                return p.tags[0].lower() if p.tags else ""
            if sort_by == "arxiv_id":
                return p.metadata.arxiv_id
            return getattr(p, sort_by, p.date_added)

        papers.sort(key=sort_key, reverse=reverse)
        return papers

    def delete_paper(self, arxiv_id: str, delete_files: bool = True) -> bool:
        """Remove a paper from the index and optionally delete its files.

        Returns True if the paper was found and removed.
        """
        index = self.load_index()
        paper = index.papers.pop(arxiv_id, None)
        if paper is None:
            return False

        if delete_files:
            for rel_path in [paper.summary_path, paper.audio_path, paper.pdf_path]:
                if rel_path:
                    full_path = self.config.data_dir / rel_path
                    if full_path.exists():
                        full_path.unlink()

        self.save_index()
        return True

    def paper_exists(self, arxiv_id: str) -> bool:
        """Check if a paper already exists in the index."""
        index = self.load_index()
        return arxiv_id in index.papers

    def add_tags(
        self,
        arxiv_id: str,
        tags: list[str],
        modified_at: datetime | None = None,
    ) -> list[str]:
        """Add tags to a paper. Returns the updated tag list."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")
        changed = False
        for tag in tags:
            if tag and tag not in paper.tags:
                paper.tags.append(tag)
                changed = True
        if changed:
            self._mark_local_modified(paper, modified_at)
        self.save_index()
        return paper.tags

    def remove_tag(
        self,
        arxiv_id: str,
        tag: str,
        modified_at: datetime | None = None,
    ) -> list[str]:
        """Remove a tag from a paper. Returns the updated tag list."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")
        if tag in paper.tags:
            paper.tags.remove(tag)
            self._mark_local_modified(paper, modified_at)
        self.save_index()
        return paper.tags

    def set_reading_status(
        self,
        arxiv_id: str,
        reading_status: ReadingStatus,
        modified_at: datetime | None = None,
    ) -> ReadingStatus:
        """Set the reading status of a paper. Returns the new reading status."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")
        if paper.reading_status != reading_status:
            paper.reading_status = reading_status
            self._mark_local_modified(paper, modified_at)
        if reading_status == ReadingStatus.ARCHIVED and paper.archived_at is None:
            paper.archived_at = modified_at or datetime.now(timezone.utc)
        if reading_status != ReadingStatus.ARCHIVED:
            paper.archived_at = None
        self.save_index()
        return paper.reading_status

    def save_summary(
        self,
        arxiv_id: str,
        content: str,
        modified_at: datetime | None = None,
    ) -> Path:
        """Write summary markdown file and update paper's summary_path."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")

        filename = make_summary_filename(arxiv_id, paper.metadata.title)
        full_path = self.config.papers_dir / filename
        full_path.write_text(content, encoding="utf-8")

        paper.summary_path = f"papers/{filename}"
        paper.status = ProcessingStatus.SUMMARIZED
        self._mark_local_modified(paper, modified_at)
        self.save_index()

        return full_path

    def set_archived(
        self,
        arxiv_id: str,
        archived: bool,
        modified_at: datetime | None = None,
    ) -> None:
        """Mark a paper archived/unarchived for sync and UI filtering."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")

        now = modified_at or datetime.now(timezone.utc)
        if archived:
            paper.archived_at = paper.archived_at or now
            paper.reading_status = ReadingStatus.ARCHIVED
        else:
            paper.archived_at = None
            if paper.reading_status == ReadingStatus.ARCHIVED:
                paper.reading_status = ReadingStatus.UNREAD
        self._mark_local_modified(paper, modified_at)
        self.save_index()

    def set_notion_fields(
        self,
        arxiv_id: str,
        *,
        notion_page_id: str | None = None,
        notion_modified_at: datetime | None = None,
        last_synced_at: datetime | None = None,
    ) -> None:
        """Persist Notion linkage/sync metadata for a paper."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")

        if notion_page_id is not None:
            paper.notion_page_id = notion_page_id
        if notion_modified_at is not None:
            paper.notion_modified_at = notion_modified_at
        if last_synced_at is not None:
            paper.last_synced_at = last_synced_at
        self.save_index()

    def save_audio(self, arxiv_id: str, audio_data: bytes) -> Path:
        """Write audio file and update paper's audio_path."""
        paper = self.get_paper(arxiv_id)
        if paper is None:
            raise KeyError(f"Paper {arxiv_id} not in index")

        filename = make_audio_filename(arxiv_id)
        full_path = self.config.audio_dir / filename
        full_path.write_bytes(audio_data)

        paper.audio_path = f"audio/{filename}"
        paper.status = ProcessingStatus.AUDIO_GENERATED
        self.save_index()

        return full_path
