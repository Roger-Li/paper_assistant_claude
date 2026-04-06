"""Shared pipeline helpers used by CLI and web routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import shutil

import httpx

from paper_assistant.arxiv import (
    ArxivRateLimitError,
    PaperNotFoundError,
    fetch_metadata as fetch_arxiv_metadata,
    parse_arxiv_url,
)
from paper_assistant.config import Config
from paper_assistant.hf_papers import fetch_metadata as fetch_hf_metadata
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, SourceType
from paper_assistant.notion import sync_notion as run_notion_sync
from paper_assistant.podcast import generate_feed
from paper_assistant.storage import StorageManager, make_audio_filename
from paper_assistant.summarizer import (
    SummarizationResult,
    find_one_pager,
    format_summary_file,
    parse_summary_sections,
)
from paper_assistant.tts import prepare_text_for_tts, text_to_speech
from paper_assistant.web_article import fetch_article, is_arxiv_url, slugify_title

logger = logging.getLogger(__name__)


@dataclass
class LocalEntryResult:
    paper: Paper
    summary_path: Path
    warnings: list[str] = field(default_factory=list)


class DuplicatePaperError(Exception):
    """Raised when importing a paper that already exists without force."""

    def __init__(self, paper_id: str):
        self.paper_id = paper_id
        super().__init__(
            f"Paper {paper_id} already exists. Use --force to re-import, "
            f"or 'paper-assist notion-sync --paper {paper_id}' to sync only."
        )


@dataclass
class ImportResult:
    paper_id: str
    title: str
    summary_path: Path
    audio_path: Path | None
    model_used: str
    notion_synced: bool
    notion_error: str | None
    warnings: list[str] = field(default_factory=list)


async def create_local_entry(
    *,
    config: Config,
    storage: StorageManager,
    title: str,
    markdown: str,
    source_url: str | None = None,
    tags: list[str] | None = None,
    skip_audio: bool = False,
) -> LocalEntryResult:
    """Create a local markdown-backed note entry without fetching remote content."""
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Title cannot be empty.")
    if not markdown.strip():
        raise ValueError("Markdown content cannot be empty.")

    clean_source_url = source_url.strip() if source_url else None
    base_slug = slugify_title(clean_title)
    paper_id = storage.make_unique_slug(base_slug)

    metadata = PaperMetadata(
        source_type=SourceType.NOTE,
        source_slug=paper_id,
        source_url=clean_source_url or None,
        title=clean_title,
        authors=[],
        abstract="",
    )

    sections = parse_summary_sections(markdown)
    one_pager = find_one_pager(sections)
    result = SummarizationResult(
        full_markdown=markdown,
        one_pager=one_pager,
        sections=sections,
        model_used="manual",
    )

    paper = Paper(
        metadata=metadata,
        status=ProcessingStatus.PENDING,
        model_used="manual",
        tags=list(tags or []),
    )
    storage.add_paper(paper)

    summary_content = format_summary_file(metadata, result)
    summary_path = storage.save_summary(paper_id, summary_content)
    paper = storage.get_paper(paper_id) or paper

    warnings: list[str] = []

    if not skip_audio:
        try:
            tts_text = prepare_text_for_tts(
                markdown,
                metadata.title,
                metadata.authors,
                source_label=metadata.source_label,
            )
            audio_path = config.audio_dir / make_audio_filename(paper_id)
            await text_to_speech(
                tts_text,
                audio_path,
                config.tts_voice,
                config.tts_rate,
            )
            paper.audio_path = f"audio/{make_audio_filename(paper_id)}"
            paper.status = ProcessingStatus.AUDIO_GENERATED
            storage.add_paper(paper)
        except Exception as exc:
            warnings.append(f"Audio generation failed: {exc}")

    try:
        from paper_assistant.podcast import generate_feed

        generate_feed(config, storage.list_papers())
        paper = storage.get_paper(paper_id) or paper
        paper.status = ProcessingStatus.COMPLETE
        storage.add_paper(paper)
    except Exception as exc:
        warnings.append(f"Feed regeneration failed: {exc}")

    return LocalEntryResult(
        paper=storage.get_paper(paper_id) or paper,
        summary_path=summary_path,
        warnings=warnings,
    )


async def import_paper_summary(
    *,
    config: Config,
    storage: StorageManager,
    url: str,
    markdown: str,
    model: str = "manual",
    tags: list[str] | None = None,
    skip_audio: bool = False,
    force: bool = False,
    sync_notion: bool = False,
) -> ImportResult:
    """Import a pre-generated summary through the shared pipeline."""
    config.ensure_dirs()

    existing: Paper | None = None
    if is_arxiv_url(url):
        arxiv_id = parse_arxiv_url(url)
        existing = storage.get_paper(arxiv_id)
        if storage.paper_exists(arxiv_id) and not force:
            raise DuplicatePaperError(arxiv_id)

    metadata = await _resolve_import_metadata(
        url=url,
        markdown=markdown,
        config=config,
        existing=existing if force else None,
    )
    paper_id = metadata.paper_id
    existing = storage.get_paper(paper_id) or existing

    if existing and not force:
        raise DuplicatePaperError(paper_id)

    sections = parse_summary_sections(markdown)
    result = SummarizationResult(
        full_markdown=markdown,
        one_pager=find_one_pager(sections),
        sections=sections,
        model_used=model,
    )

    paper = _build_import_paper(
        metadata=metadata,
        model=model,
        tags=tags or [],
        result=result,
        existing=existing if force else None,
        skip_audio=skip_audio,
    )
    storage.add_paper(paper)

    summary_content = format_summary_file(metadata, result)
    summary_path = storage.save_summary(paper_id, summary_content)

    # Re-fetch after save_summary; StorageManager mutates a different paper instance.
    paper = storage.get_paper(paper_id) or paper
    warnings: list[str] = []

    if not skip_audio:
        try:
            await _generate_audio_for_import(
                config=config,
                storage=storage,
                paper=paper,
                markdown=markdown,
            )
            paper = storage.get_paper(paper_id) or paper
        except Exception as exc:
            warnings.append(f"Audio generation failed: {exc}")

    try:
        generate_feed(config, storage.list_papers())
        paper = storage.get_paper(paper_id) or paper
        paper.status = ProcessingStatus.COMPLETE
        storage.add_paper(paper)
        paper = storage.get_paper(paper_id) or paper
    except Exception as exc:
        warnings.append(f"Feed regeneration failed: {exc}")

    if paper.audio_path and config.icloud_sync:
        try:
            _copy_audio_to_icloud(config=config, paper=paper)
        except Exception as exc:
            warnings.append(f"iCloud copy failed: {exc}")

    notion_synced = False
    notion_error: str | None = None
    if sync_notion:
        try:
            await run_notion_sync(
                config=config,
                storage=storage,
                paper_id=paper_id,
                dry_run=False,
            )
            notion_synced = True
        except Exception as exc:
            notion_error = str(exc)

    final_paper = storage.get_paper(paper_id) or paper
    audio_path = (
        config.data_dir / final_paper.audio_path
        if final_paper.audio_path
        else None
    )

    return ImportResult(
        paper_id=paper_id,
        title=metadata.title,
        summary_path=summary_path,
        audio_path=audio_path,
        model_used=model,
        notion_synced=notion_synced,
        notion_error=notion_error,
        warnings=warnings,
    )


async def _resolve_import_metadata(
    *,
    url: str,
    markdown: str,
    config: Config,
    existing: Paper | None = None,
) -> PaperMetadata:
    if is_arxiv_url(url):
        arxiv_id = parse_arxiv_url(url)
        return await _resolve_import_arxiv_metadata(
            arxiv_id=arxiv_id,
            markdown=markdown,
            config=config,
            existing=existing,
        )

    metadata, _body_text = await fetch_article(url)
    return metadata


async def _resolve_import_arxiv_metadata(
    *,
    arxiv_id: str,
    markdown: str,
    config: Config,
    existing: Paper | None,
) -> PaperMetadata:
    hf_exc: Exception | None = None
    try:
        return await fetch_hf_metadata(arxiv_id, config=config)
    except Exception as exc:
        hf_exc = exc
        logger.warning("HF metadata unavailable for %s during import: %s", arxiv_id, exc)

    try:
        return await fetch_arxiv_metadata(arxiv_id, config=config)
    except Exception as exc:
        arxiv_transient = _is_transient_metadata_error(exc)
        hf_transient = hf_exc is not None and _is_transient_metadata_error(hf_exc)

        if not arxiv_transient:
            raise

        if existing is not None:
            logger.warning(
                "Reusing existing metadata for %s after transient remote metadata failure: %s",
                arxiv_id,
                exc,
            )
            return existing.metadata

        if not hf_transient:
            raise exc

        logger.warning(
            "Falling back to summary-derived metadata for %s after remote metadata failures: %s",
            arxiv_id,
            exc,
        )
        return _derive_import_metadata_from_summary(arxiv_id=arxiv_id, markdown=markdown)


def _is_transient_metadata_error(exc: Exception) -> bool:
    if isinstance(exc, (ArxivRateLimitError, httpx.TimeoutException, httpx.TransportError)):
        return True

    if isinstance(exc, PaperNotFoundError):
        return False

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code == 408 or 500 <= status_code < 600

    return False


def _derive_import_metadata_from_summary(*, arxiv_id: str, markdown: str) -> PaperMetadata:
    sections = parse_summary_sections(markdown)
    one_pager = find_one_pager(sections) or markdown
    identity_line = _extract_summary_identity_line(one_pager)
    title = _extract_title_from_identity(identity_line) or f"arXiv {arxiv_id}"
    authors = _extract_authors_from_identity(identity_line)
    abstract = _extract_summary_abstract(one_pager, identity_line)
    published = _extract_year_from_identity(identity_line)

    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
        categories=[],
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def _strip_inline_markdown(text: str) -> str:
    """Strip one outer layer of simple inline markdown markers.

    This is intentionally shallow because the summary-derived metadata fallback
    only needs light cleanup for identity lines such as `*Title, 2026, Author*`.
    """
    cleaned = text.strip()
    for marker in ("**", "__", "*", "_", "`"):
        if cleaned.startswith(marker) and cleaned.endswith(marker) and len(cleaned) >= len(marker) * 2:
            cleaned = cleaned[len(marker):-len(marker)].strip()
    return cleaned


def _extract_summary_identity_line(one_pager: str) -> str | None:
    for line in one_pager.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "+ ", "* ", ">")):
            continue

        cleaned = _strip_inline_markdown(stripped)
        if re.search(r"\b(?:19|20)\d{2}\b", cleaned) or "arxiv" in cleaned.lower():
            return cleaned

    return None


def _extract_title_from_identity(identity_line: str | None) -> str:
    if not identity_line:
        return ""

    year_match = re.search(r"\b(?:19|20)\d{2}\b", identity_line)
    if year_match is not None:
        title_end = identity_line.rfind(",", 0, year_match.start())
        if title_end != -1:
            return identity_line[:title_end].strip(" -*_`")

    return identity_line.strip(" -*_`")


def _extract_authors_from_identity(identity_line: str | None) -> list[str]:
    if not identity_line:
        return []

    year_match = re.search(r"\b(?:19|20)\d{2}\b", identity_line)
    if year_match is None:
        return []

    author_blob = identity_line[year_match.end():]
    if ")" in author_blob:
        author_blob = author_blob.split(")", 1)[1]
    author_blob = author_blob.lstrip(" ,;-")

    authors: list[str] = []
    for author in author_blob.split(","):
        cleaned = author.strip()
        if cleaned:
            authors.append(cleaned)
    return authors


def _extract_year_from_identity(identity_line: str | None) -> datetime | None:
    if not identity_line:
        return None

    match = re.search(r"\b((?:19|20)\d{2})\b", identity_line)
    if match is None:
        return None

    return datetime(int(match.group(1)), 1, 1, tzinfo=timezone.utc)


def _extract_summary_abstract(one_pager: str, identity_line: str | None) -> str:
    lines = one_pager.splitlines()
    identity_idx = -1
    if identity_line:
        for idx, line in enumerate(lines):
            if _strip_inline_markdown(line.strip()) == identity_line:
                identity_idx = idx
                break

    start_idx = identity_idx + 1 if identity_idx != -1 else 0
    paragraph: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith(("- ", "+ ", "* ", ">")) or stripped.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(stripped)

    return " ".join(paragraph)


def _build_import_paper(
    *,
    metadata: PaperMetadata,
    model: str,
    tags: list[str],
    result: SummarizationResult,
    existing: Paper | None,
    skip_audio: bool,
) -> Paper:
    token_count = result.input_tokens + result.output_tokens

    if existing is None:
        return Paper(
            metadata=metadata,
            status=ProcessingStatus.PENDING,
            tags=list(tags),
            model_used=model,
            token_count=token_count,
            error_message=None,
        )

    return Paper(
        metadata=metadata,
        date_added=existing.date_added,
        status=ProcessingStatus.PENDING,
        tags=_merge_tags(existing.tags, tags),
        reading_status=existing.reading_status,
        local_modified_at=existing.local_modified_at,
        notion_modified_at=existing.notion_modified_at,
        last_synced_at=existing.last_synced_at,
        archived_at=existing.archived_at,
        notion_page_id=existing.notion_page_id,
        audio_path=existing.audio_path if skip_audio else None,
        model_used=model,
        token_count=token_count,
        error_message=None,
    )


def _merge_tags(existing_tags: list[str], new_tags: list[str]) -> list[str]:
    merged: list[str] = []
    for tag in [*existing_tags, *new_tags]:
        if tag and tag not in merged:
            merged.append(tag)
    return merged


async def _generate_audio_for_import(
    *,
    config: Config,
    storage: StorageManager,
    paper: Paper,
    markdown: str,
) -> Path:
    paper_id = paper.metadata.paper_id
    tts_text = prepare_text_for_tts(
        markdown,
        paper.metadata.title,
        paper.metadata.authors,
        source_label=paper.metadata.source_label,
    )
    audio_path = config.audio_dir / make_audio_filename(paper_id)
    await text_to_speech(
        tts_text,
        audio_path,
        config.tts_voice,
        config.tts_rate,
    )
    paper.audio_path = f"audio/{make_audio_filename(paper_id)}"
    paper.status = ProcessingStatus.AUDIO_GENERATED
    storage.add_paper(paper)
    return audio_path


def _copy_audio_to_icloud(*, config: Config, paper: Paper) -> Path:
    if not paper.audio_path:
        raise ValueError("Paper has no audio_path to copy to iCloud.")

    config.icloud_dir.mkdir(parents=True, exist_ok=True)
    safe_title = paper.metadata.title[:60].replace("/", "-").replace(":", " -")
    destination = config.icloud_dir / f"{safe_title} [{paper.metadata.paper_id}].mp3"
    shutil.copy2(config.data_dir / paper.audio_path, destination)
    return destination
