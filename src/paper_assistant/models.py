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


class PaperMetadata(BaseModel):
    """Core metadata fetched from arXiv."""

    arxiv_id: str  # e.g., "2503.10291"
    title: str
    authors: list[str]
    abstract: str
    published: datetime
    categories: list[str] = Field(default_factory=list)
    arxiv_url: str  # https://arxiv.org/abs/2503.10291
    pdf_url: str  # https://arxiv.org/pdf/2503.10291


class Paper(BaseModel):
    """Full paper record with processing state and file paths."""

    metadata: PaperMetadata
    date_added: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ProcessingStatus = ProcessingStatus.PENDING
    tags: list[str] = Field(default_factory=list)

    # File paths relative to the data directory
    pdf_path: str | None = None
    summary_path: str | None = None
    audio_path: str | None = None

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

    papers: dict[str, Paper] = Field(default_factory=dict)  # keyed by arxiv_id
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
