"""Shared pipeline helpers used by CLI and web routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, SourceType
from paper_assistant.storage import StorageManager, make_audio_filename
from paper_assistant.summarizer import (
    SummarizationResult,
    find_one_pager,
    format_summary_file,
    parse_summary_sections,
)
from paper_assistant.tts import prepare_text_for_tts, text_to_speech
from paper_assistant.web_article import slugify_title


@dataclass
class LocalEntryResult:
    paper: Paper
    summary_path: Path
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
