"""Data models for Paper Assistant."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    FETCHED = "fetched"
    SUMMARIZED = "summarized"
    AUDIO_GENERATED = "audio_generated"
    COMPLETE = "complete"
    ERROR = "error"


class ReadingStatus(str, Enum):
    UNREAD = "unread"
    READING = "reading"
    READ = "read"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    ARXIV = "arxiv"
    WEB = "web"
    NOTE = "note"


class PaperMetadata(BaseModel):
    """Core metadata for a paper, web article, or local note.

    For arXiv papers, ``arxiv_id`` is set and used as the primary key.
    For web articles and notes, ``source_slug`` is set and used as the primary key.
    """

    # Source identification
    source_type: SourceType = SourceType.ARXIV
    source_url: str | None = None  # canonical URL for web articles / bookmarked notes
    source_slug: str | None = None  # URL- or title-derived slug for non-arXiv entries

    # arXiv-specific (optional for web articles / notes)
    arxiv_id: str | None = None  # e.g., "2503.10291"
    arxiv_url: str | None = None  # https://arxiv.org/abs/2503.10291
    pdf_url: str | None = None  # https://arxiv.org/pdf/2503.10291

    # Common metadata
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    published: datetime | None = None
    categories: list[str] = Field(default_factory=list)

    @property
    def paper_id(self) -> str:
        """Primary key: arxiv_id for arXiv papers, source_slug otherwise."""
        if self.arxiv_id:
            return self.arxiv_id
        if self.source_slug:
            return self.source_slug
        raise ValueError("PaperMetadata has neither arxiv_id nor source_slug")

    @property
    def source_label(self) -> str:
        """Human-friendly source label for narration and UI copy."""
        if self.source_type == SourceType.WEB:
            return "article"
        if self.source_type == SourceType.NOTE:
            return "note"
        return "paper"


class Paper(BaseModel):
    """Full paper record with processing state and file paths."""

    metadata: PaperMetadata
    date_added: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ProcessingStatus = ProcessingStatus.PENDING
    tags: list[str] = Field(default_factory=list)
    reading_status: ReadingStatus = ReadingStatus.UNREAD
    local_modified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notion_modified_at: datetime | None = None
    last_synced_at: datetime | None = None
    archived_at: datetime | None = None
    notion_page_id: str | None = None

    # File paths relative to the data directory
    pdf_path: str | None = None
    summary_path: str | None = None
    audio_path: str | None = None
    transcript_path: str | None = None

    # Processing metadata
    model_used: str | None = None
    token_count: int | None = None
    error_message: str | None = None

    @property
    def safe_title(self) -> str:
        """Title sanitized for filenames."""
        return sanitize_filename(self.metadata.title)


class PaperIndex(BaseModel):
    """Top-level index stored in index.json."""

    papers: dict[str, Paper] = Field(default_factory=dict)  # keyed by paper_id
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def sanitize_filename(title: str, max_length: int = 80) -> str:
    """Sanitize a paper title for use in filenames."""
    # Replace colons with dashes
    title = title.replace(":", " -")
    # Remove characters invalid in filenames
    title = re.sub(r'[<>"/\\|?*]', "", title)
    # Collapse multiple spaces
    title = re.sub(r"\s+", " ", title).strip()
    # Truncate at word boundary
    if len(title) > max_length:
        title = title[:max_length].rsplit(" ", 1)[0].rstrip(" -")
    return title
