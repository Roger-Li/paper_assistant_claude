"""CLI entry point for Paper Assistant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
import tempfile

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

console = Console()


@click.group()
@click.option(
    "--data-dir",
    type=click.Path(),
    envvar="PAPER_ASSIST_DATA_DIR",
    default=None,
    help="Override the default data directory (~/.paper-assistant).",
)
@click.pass_context
def main(ctx: click.Context, data_dir: str | None) -> None:
    """Paper Assistant - AI-powered ML paper summarizer."""
    ctx.ensure_object(dict)
    if data_dir:
        ctx.obj["data_dir"] = data_dir


@main.command()
@click.argument("url")
@click.option(
    "--native-pdf",
    is_flag=True,
    help="When PDF fallback is needed, send raw PDF to Claude instead of extracted text.",
)
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option(
    "--skip-transcript",
    is_flag=True,
    help="Skip the derived narration transcript (TTS uses the raw summary).",
)
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this paper.")
@click.option("--force", is_flag=True, help="Re-process even if paper already exists.")
@click.pass_context
def add(
    ctx: click.Context,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    skip_transcript: bool,
    tags: tuple[str, ...],
    force: bool,
) -> None:
    """Add and summarize a paper from an arXiv ID, paper URL, or web article URL.

    Examples:
      paper-assist add 2503.10291
      paper-assist add https://arxiv.org/abs/2503.10291
      paper-assist add https://huggingface.co/papers/2503.10291
      paper-assist add https://example.com/blog/article
    """
    asyncio.run(
        _add_paper(ctx.obj, url, native_pdf, skip_audio, skip_transcript, list(tags), force)
    )


async def _add_paper(
    obj: dict,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    skip_transcript: bool = False,
    tags: list[str] | None = None,
    force: bool = False,
) -> None:
    """Full pipeline: fetch -> extract -> summarize -> TTS -> RSS."""
    from paper_assistant.web_article import is_arxiv_url

    tag_list = list(tags or [])
    if is_arxiv_url(url):
        await _add_arxiv_paper(obj, url, native_pdf, skip_audio, skip_transcript, tag_list, force)
    else:
        await _add_web_article(obj, url, skip_audio, skip_transcript, tag_list, force)


async def _add_arxiv_paper(
    obj: dict,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    skip_transcript: bool = False,
    tags: list[str] | None = None,
    force: bool = False,
) -> None:
    """arXiv paper pipeline: fetch -> extract -> summarize -> TTS -> RSS."""
    tags = list(tags or [])
    from paper_assistant.arxiv import download_pdf, fetch_metadata as fetch_arxiv_metadata, parse_arxiv_url
    from paper_assistant.config import load_config
    from paper_assistant.hf_papers import (
        fetch_markdown_body as fetch_hf_markdown_body,
        fetch_metadata as fetch_hf_metadata,
    )
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.storage import StorageManager, make_pdf_filename
    from paper_assistant.summarizer import (
        format_summary_file,
        summarize_paper_pdf,
        summarize_paper_text,
    )
    config = load_config(**obj)
    if not config.anthropic_api_key:
        console.print(
            "[red]ANTHROPIC_API_KEY is required for summarization.[/red] "
            "Set it in .env or as an environment variable."
        )
        return
    config.ensure_dirs()
    storage = StorageManager(config)

    # Step 1: Parse URL and fetch metadata
    console.print("[bold]Step 1/5:[/bold] Parsing arXiv URL...")
    try:
        arxiv_id = parse_arxiv_url(url)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if storage.paper_exists(arxiv_id) and not force:
        console.print(
            f"[yellow]Paper {arxiv_id} already exists. Use --force to re-process.[/yellow]"
        )
        return

    console.print(f"[bold]Step 1/5:[/bold] Fetching metadata for {arxiv_id}...")
    try:
        metadata = await fetch_hf_metadata(arxiv_id, config=config)
        console.print("  Source: Hugging Face paper metadata")
    except Exception as e:
        console.print(f"  [yellow]HF metadata unavailable:[/yellow] {e}")
        try:
            metadata = await fetch_arxiv_metadata(arxiv_id, config=config)
            console.print("  Source: arXiv metadata fallback")
        except Exception as fallback_exc:
            console.print(f"[red]Error fetching metadata:[/red] {fallback_exc}")
            return

    paper_id = metadata.paper_id
    console.print(f"  Title: [cyan]{metadata.title}[/cyan]")
    console.print(f"  Authors: {', '.join(metadata.authors[:3])}")

    # Create paper record early
    paper = Paper(
        metadata=metadata,
        tags=tags,
        status=ProcessingStatus.PENDING,
    )
    storage.add_paper(paper)

    # Step 2: Fetch paper content
    console.print("[bold]Step 2/5:[/bold] Fetching paper content...")
    paper_text: str | None = None
    pdf_path = config.pdfs_dir / make_pdf_filename(paper_id)
    try:
        paper_text = await fetch_hf_markdown_body(arxiv_id, config=config)
        paper.status = ProcessingStatus.FETCHED
        storage.add_paper(paper)
        console.print(f"  Source: Hugging Face arXiv HTML markdown ({len(paper_text)} characters)")
    except Exception as e:
        console.print(f"  [yellow]HF markdown unavailable or rejected:[/yellow] {e}")
        console.print("  Falling back to PDF.")
        try:
            await download_pdf(arxiv_id, pdf_path, config=config)
            paper.pdf_path = f"pdfs/{make_pdf_filename(paper_id)}"
            paper.status = ProcessingStatus.FETCHED
            storage.add_paper(paper)
        except Exception as pdf_exc:
            console.print(f"[red]Error downloading PDF:[/red] {pdf_exc}")
            paper.status = ProcessingStatus.ERROR
            paper.error_message = str(pdf_exc)
            storage.add_paper(paper)
            return

    # Step 3: Summarize with Claude
    console.print(f"[bold]Step 3/5:[/bold] Summarizing with {config.claude_model}...")
    try:
        if paper_text is not None:
            result = await summarize_paper_text(config, metadata, paper_text)
        elif native_pdf:
            result = await summarize_paper_pdf(config, metadata, pdf_path)
        else:
            from paper_assistant.pdf import extract_text_from_pdf

            paper_text = extract_text_from_pdf(pdf_path)
            result = await summarize_paper_text(config, metadata, paper_text)

        from paper_assistant.visuals import enrich_summary_with_visuals

        result.full_markdown = enrich_summary_with_visuals(
            full_markdown=result.full_markdown,
            source_markdown=paper_text,
        )

        summary_content = format_summary_file(metadata, result)
        summary_path = storage.save_summary(paper_id, summary_content)
        paper = storage.get_paper(paper_id)  # Re-fetch with updated summary_path
        paper.model_used = result.model_used
        paper.token_count = result.input_tokens + result.output_tokens
        console.print(
            f"  Tokens used: {result.input_tokens} in + {result.output_tokens} out"
        )
    except Exception as e:
        console.print(f"[red]Error during summarization:[/red] {e}")
        paper.status = ProcessingStatus.ERROR
        paper.error_message = str(e)
        storage.add_paper(paper)
        return

    # Step 4: Generate audio
    await _generate_audio_step(
        config, storage, paper_id, result.full_markdown, skip_audio, skip_transcript, "4/5"
    )
    paper = storage.get_paper(paper_id) or paper

    # Step 5: Update RSS feed
    _update_feed_step(config, storage, paper, "5/5")

    # Copy audio to iCloud Drive for iPhone access
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    # Update search index
    from paper_assistant.search import get_search_manager

    search_mgr = get_search_manager(config)
    if search_mgr:
        try:
            search_mgr.sync_paper(paper_id, storage)
        except Exception:
            console.print("[yellow]Warning: Search index update failed.[/yellow]")

    console.print()
    console.print("[green]Done![/green] Paper processed successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.transcript_path:
        console.print(f"  Transcript: {config.data_dir / paper.transcript_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


async def _add_web_article(
    obj: dict,
    url: str,
    skip_audio: bool,
    skip_transcript: bool = False,
    tags: list[str] | None = None,
    force: bool = False,
) -> None:
    """Web article pipeline: fetch -> summarize -> TTS -> RSS."""
    tags = list(tags or [])
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.storage import StorageManager
    from paper_assistant.summarizer import format_summary_file, summarize_article_text
    from paper_assistant.web_article import fetch_article

    config = load_config(**obj)
    if not config.anthropic_api_key:
        console.print(
            "[red]ANTHROPIC_API_KEY is required for summarization.[/red] "
            "Set it in .env or as an environment variable."
        )
        return
    config.ensure_dirs()
    storage = StorageManager(config)

    # Step 1: Fetch article content and metadata
    console.print("[bold]Step 1/4:[/bold] Fetching web article...")
    try:
        metadata, body_text = await fetch_article(url)
    except Exception as e:
        console.print(f"[red]Error fetching article:[/red] {e}")
        return

    paper_id = metadata.paper_id
    if storage.paper_exists(paper_id) and not force:
        console.print(
            f"[yellow]Article {paper_id} already exists. Use --force to re-process.[/yellow]"
        )
        return

    console.print(f"  Title: [cyan]{metadata.title}[/cyan]")
    if metadata.authors:
        console.print(f"  Authors: {', '.join(metadata.authors[:3])}")
    console.print(f"  Content: {len(body_text)} characters extracted")

    paper = Paper(
        metadata=metadata,
        tags=tags,
        status=ProcessingStatus.FETCHED,
    )
    storage.add_paper(paper)

    # Step 2: Summarize with Claude
    console.print(f"[bold]Step 2/4:[/bold] Summarizing with {config.claude_model}...")
    try:
        result = await summarize_article_text(config, metadata, body_text)
        summary_content = format_summary_file(metadata, result)
        summary_path = storage.save_summary(paper_id, summary_content)
        paper = storage.get_paper(paper_id)
        paper.model_used = result.model_used
        paper.token_count = result.input_tokens + result.output_tokens
        console.print(
            f"  Tokens used: {result.input_tokens} in + {result.output_tokens} out"
        )
    except Exception as e:
        console.print(f"[red]Error during summarization:[/red] {e}")
        paper.status = ProcessingStatus.ERROR
        paper.error_message = str(e)
        storage.add_paper(paper)
        return

    # Step 3: Generate audio
    await _generate_audio_step(
        config, storage, paper_id, result.full_markdown, skip_audio, skip_transcript, "3/4"
    )
    paper = storage.get_paper(paper_id) or paper

    # Step 4: Update RSS feed
    _update_feed_step(config, storage, paper, "4/4")

    # Copy audio to iCloud Drive
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    # Update search index
    from paper_assistant.search import get_search_manager

    search_mgr = get_search_manager(config)
    if search_mgr:
        try:
            search_mgr.sync_paper(paper_id, storage)
        except Exception:
            console.print("[yellow]Warning: Search index update failed.[/yellow]")

    console.print()
    console.print("[green]Done![/green] Article processed successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.transcript_path:
        console.print(f"  Transcript: {config.data_dir / paper.transcript_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


async def _generate_audio_step(
    config, storage, paper_id, source_markdown, skip_audio, skip_transcript, step_label
):
    """Shared audio-asset step for arXiv + web article pipelines."""
    from paper_assistant.audio_assets import render_audio_assets

    if skip_audio:
        console.print(f"[bold]Step {step_label}:[/bold] Skipping audio (--skip-audio).")
        return

    label_suffix = " (transcript skipped, using raw summary)" if skip_transcript else ""
    console.print(f"[bold]Step {step_label}:[/bold] Generating audio{label_suffix}...")

    paper = storage.get_paper(paper_id)
    if paper is None:
        console.print(f"[yellow]Warning: paper {paper_id} missing before audio step.[/yellow]")
        return

    result = await render_audio_assets(
        config=config,
        storage=storage,
        paper=paper,
        source_markdown=source_markdown,
        skip_transcript=skip_transcript,
        skip_audio=False,
    )
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    if result.transcript_path:
        console.print(f"  Transcript: {result.transcript_path}")
    if result.audio_path and result.backend_used:
        console.print(f"  Audio backend: {result.backend_used}")


def _update_feed_step(config, storage, paper, step_label):
    """Shared RSS feed update step."""
    from paper_assistant.models import ProcessingStatus
    from paper_assistant.podcast import generate_feed

    console.print(f"[bold]Step {step_label}:[/bold] Updating podcast feed...")
    try:
        all_papers = storage.list_papers()
        generate_feed(config, all_papers)
        paper.status = ProcessingStatus.COMPLETE
        storage.add_paper(paper)
    except Exception as e:
        console.print(f"[yellow]Warning: Feed generation failed:[/yellow] {e}")


def _copy_to_icloud(config, paper, title, paper_id, *, quiet: bool = False) -> str | None:
    """Copy audio to iCloud Drive for iPhone access.

    With ``quiet`` (JSON output mode) nothing is printed — stdout must stay
    machine-parseable — and a failure message is returned instead so the
    caller can surface it through structured warnings.
    """
    import shutil

    try:
        config.icloud_dir.mkdir(parents=True, exist_ok=True)
        safe_title = title[:60].replace("/", "-").replace(":", " -")
        icloud_dest = config.icloud_dir / f"{safe_title} [{paper_id}].mp3"
        shutil.copy2(config.data_dir / paper.audio_path, icloud_dest)
        if not quiet:
            console.print(f"  iCloud:  Synced to {icloud_dest.name}")
        return None
    except Exception as e:
        if quiet:
            return f"iCloud copy failed: {e}"
        console.print(f"[yellow]Warning: iCloud copy failed:[/yellow] {e}")
        return None


def _read_markdown_input(file_path: str | None) -> str:
    """Read markdown from a file or the macOS clipboard."""
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")

    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout


def _build_model_label(model: str, model_version: str | None) -> str:
    if not model_version:
        return model
    return f"{model}/{model_version}"


_LIST_ITEM_RE = re.compile(r"^(\s*(?:[-+*]|\d+\.)\s+)(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^(\s*>\s?)(.*)$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HRULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _is_structural_markdown_line(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped == "$$"
        or _HEADING_RE.match(line)
        or _LIST_ITEM_RE.match(line)
        or _BLOCKQUOTE_RE.match(line)
        or _FENCE_RE.match(line)
        or _HRULE_RE.match(line)
        or _TABLE_LINE_RE.match(line)
    )


def _fold_wrapped_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []

    folded: list[str] = []
    current = lines[0].strip()
    for raw_line in lines[1:]:
        next_part = raw_line.strip()
        if not current:
            current = next_part
            continue
        if current.endswith("  "):
            folded.append(current.rstrip())
            current = next_part
            continue
        current = f"{current} {next_part}".strip()

    if current:
        folded.append(current.rstrip())
    return folded


def _normalize_skill_markdown(markdown: str) -> str:
    """Remove email-style hard wraps from agent-generated prose blocks."""
    lines = markdown.splitlines()
    normalized: list[str] = []
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if not stripped:
            normalized.append("")
            idx += 1
            continue

        if _FENCE_RE.match(line):
            normalized.append(line)
            fence_marker = _FENCE_RE.match(line).group(1)
            idx += 1
            while idx < len(lines):
                normalized.append(lines[idx])
                if lines[idx].strip().startswith(fence_marker):
                    idx += 1
                    break
                idx += 1
            continue

        if stripped == "$$":
            normalized.append(line)
            idx += 1
            while idx < len(lines):
                normalized.append(lines[idx])
                if lines[idx].strip() == "$$":
                    idx += 1
                    break
                idx += 1
            continue

        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            item_lines = [list_match.group(2)]
            prefix = list_match.group(1)
            idx += 1
            while idx < len(lines):
                next_line = lines[idx]
                if not next_line.strip() or _is_structural_markdown_line(next_line):
                    break
                item_lines.append(next_line)
                idx += 1
            for folded in _fold_wrapped_lines(item_lines):
                normalized.append(f"{prefix}{folded}")
            continue

        quote_match = _BLOCKQUOTE_RE.match(line)
        if quote_match:
            quote_lines = [quote_match.group(2)]
            prefix = quote_match.group(1)
            idx += 1
            while idx < len(lines):
                next_line = lines[idx]
                next_match = _BLOCKQUOTE_RE.match(next_line)
                if not next_line.strip() or not next_match:
                    break
                quote_lines.append(next_match.group(2))
                idx += 1
            for folded in _fold_wrapped_lines(quote_lines):
                normalized.append(f"{prefix}{folded}")
            continue

        if _is_structural_markdown_line(line):
            normalized.append(line)
            idx += 1
            continue

        paragraph_lines = [line]
        idx += 1
        while idx < len(lines):
            next_line = lines[idx]
            if not next_line.strip() or _is_structural_markdown_line(next_line):
                break
            paragraph_lines.append(next_line)
            idx += 1
        normalized.extend(_fold_wrapped_lines(paragraph_lines))

    return "\n".join(normalized) + ("\n" if markdown.endswith("\n") else "")


def _import_result_to_dict(result) -> dict[str, object]:
    return {
        "paper_id": result.paper_id,
        "title": result.title,
        "summary_path": str(result.summary_path),
        "audio_path": str(result.audio_path) if result.audio_path else None,
        "transcript_path": (
            str(result.transcript_path) if result.transcript_path else None
        ),
        "backend_used": result.backend_used,
        "model_used": result.model_used,
        "notion_synced": result.notion_synced,
        "notion_error": result.notion_error,
        "warnings": result.warnings,
    }


def _print_import_result(result, success_message: str) -> None:
    console.print()
    console.print(f"[green]{success_message}[/green]")
    console.print(f"  ID:      {result.paper_id}")
    console.print(f"  Title:   [cyan]{result.title}[/cyan]")
    console.print(f"  Summary: {result.summary_path}")
    if result.transcript_path:
        console.print(f"  Transcript: {result.transcript_path}")
    if result.audio_path:
        console.print(f"  Audio:   {result.audio_path}")
    if result.backend_used:
        console.print(f"  Backend: {result.backend_used}")
    console.print(f"  Model:   {result.model_used}")
    if result.notion_synced:
        console.print("  Notion:  Synced")
    elif result.notion_error:
        console.print(f"[yellow]Warning:[/yellow] Notion sync failed: {result.notion_error}")
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


def _cleanup_roots() -> list[Path]:
    return [
        Path(tempfile.gettempdir()).resolve(),
        (Path.cwd() / ".artifacts").resolve(strict=False),
    ]


def _is_within_cleanup_root(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_cleanup_files(paths: tuple[str, ...]) -> list[Path]:
    allowed_roots = _cleanup_roots()
    validated: list[Path] = []

    for raw_path in paths:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve(strict=False)

        if not _is_within_cleanup_root(candidate, allowed_roots):
            raise click.BadParameter(
                f"{candidate} must be under one of: {', '.join(str(root) for root in allowed_roots)}",
                param_hint="--cleanup-file",
            )

        resolved = candidate.resolve(strict=True)
        if not _is_within_cleanup_root(resolved, allowed_roots):
            raise click.BadParameter(
                f"{resolved} resolves outside the allowed cleanup roots",
                param_hint="--cleanup-file",
            )

        if not resolved.is_file():
            raise click.BadParameter(
                f"{candidate} must be a regular file",
                param_hint="--cleanup-file",
            )

        validated.append(candidate)

    return validated


def _recovery_artifact_paths(
    file_path: str | None, cleanup_paths: list[Path], *extra_paths: str | None
) -> list[Path]:
    paths: list[Path] = [Path(p) for p in (file_path, *extra_paths) if p]
    paths.extend(cleanup_paths)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


@main.command("import")
@click.argument("url")
@click.option("--file", "-f", "file_path", type=click.Path(exists=True), help="Read markdown from file instead of clipboard.")
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option(
    "--skip-transcript",
    is_flag=True,
    help="Skip the derived narration transcript (audio falls back to raw summary).",
)
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this paper.")
@click.option("--force", is_flag=True, help="Re-import even if paper already exists.")
@click.option(
    "--model",
    default=None,
    help="Model that generated this summary (e.g., 'claude-code'). Default: 'manual'.",
)
@click.pass_context
def import_paper(
    ctx: click.Context,
    url: str,
    file_path: str | None,
    skip_audio: bool,
    skip_transcript: bool,
    tags: tuple[str, ...],
    force: bool,
    model: str | None,
) -> None:
    """Import a pre-generated summary from clipboard or file.

    Reads markdown from the macOS clipboard (pbpaste) by default,
    or from a file with --file. Skips the Claude API summarization step.

    Examples:
      paper-assist import https://arxiv.org/abs/2503.10291
      paper-assist import https://example.com/blog/article --file summary.md
    """
    markdown = _read_markdown_input(file_path)

    if not markdown.strip():
        console.print("[red]No markdown content found.[/red]")
        if not file_path:
            console.print("Copy your summary to the clipboard first, or use --file.")
        return

    try:
        result = asyncio.run(
            _run_import_pipeline(
                ctx.obj,
                url=url,
                markdown=markdown,
                skip_audio=skip_audio,
                skip_transcript=skip_transcript,
                tags=list(tags),
                force=force,
                model=model or "manual",
                sync_notion=False,
            )
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    _print_import_result(result, "Summary imported successfully.")


async def _run_import_pipeline(
    obj: dict,
    *,
    url: str,
    markdown: str,
    skip_audio: bool,
    skip_transcript: bool,
    tags: list[str],
    force: bool,
    model: str,
    sync_notion: bool,
    provided_script_markdown: str | None = None,
    skip_script_generation: bool = False,
):
    from paper_assistant.config import load_config
    from paper_assistant.pipeline import import_paper_summary
    from paper_assistant.storage import StorageManager

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    return await import_paper_summary(
        config=config,
        storage=storage,
        url=url,
        markdown=markdown,
        model=model,
        tags=tags,
        skip_audio=skip_audio,
        skip_transcript=skip_transcript,
        force=force,
        sync_notion=sync_notion,
        provided_script_markdown=provided_script_markdown,
        skip_script_generation=skip_script_generation,
    )


@main.command("skill-import")
@click.argument("url")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True), help="Markdown summary file to import.")
@click.option("--model", required=True, help="Stable model label for provenance (e.g., 'claude-code', 'codex').")
@click.option("--model-version", default=None, help="Optional model version appended as model/version.")
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this paper.")
@click.option("--sync-notion", is_flag=True, help="Run Notion sync for this paper after import.")
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option(
    "--skip-transcript",
    is_flag=True,
    help="Skip the derived narration transcript (audio falls back to raw summary).",
)
@click.option(
    "--script-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Use this file as the narration transcript instead of generating one.",
)
@click.option(
    "--no-script-fallback",
    is_flag=True,
    default=False,
    help="Never call the Anthropic API for narration; require --script-file or warn.",
)
@click.option("--force", is_flag=True, help="Merge over an existing paper instead of failing.")
@click.option("--cleanup-file", multiple=True, help="Temporary file to delete after a successful import.")
@click.option("--json", "json_output", is_flag=True, help="Output ImportResult as JSON.")
@click.pass_context
def skill_import(
    ctx: click.Context,
    url: str,
    file_path: str,
    model: str,
    model_version: str | None,
    tags: tuple[str, ...],
    sync_notion: bool,
    skip_audio: bool,
    skip_transcript: bool,
    script_file: str | None,
    no_script_fallback: bool,
    force: bool,
    cleanup_file: tuple[str, ...],
    json_output: bool,
) -> None:
    """Import a skill-generated summary with deterministic provenance."""
    cleanup_paths = _validate_cleanup_files(cleanup_file)
    markdown = _normalize_skill_markdown(Path(file_path).read_text(encoding="utf-8"))

    if not markdown.strip():
        raise click.ClickException("No markdown content found in --file.")

    provided_script_markdown: str | None = None
    if script_file is not None:
        provided_script_markdown = Path(script_file).read_text(encoding="utf-8").strip()
        if not provided_script_markdown:
            raise click.ClickException("--script-file was empty.")

    model_label = _build_model_label(model, model_version)

    try:
        result = asyncio.run(
            _run_import_pipeline(
                ctx.obj,
                url=url,
                markdown=markdown,
                skip_audio=skip_audio,
                skip_transcript=skip_transcript,
                tags=list(tags),
                force=force,
                model=model_label,
                sync_notion=sync_notion,
                provided_script_markdown=provided_script_markdown,
                skip_script_generation=no_script_fallback,
            )
        )
    except Exception as exc:
        recovery_paths = _recovery_artifact_paths(file_path, cleanup_paths)
        console.print("[yellow]Artifacts preserved for manual recovery:[/yellow]")
        for recovery_path in recovery_paths:
            console.print(f"  - {recovery_path}")
        raise click.ClickException(str(exc)) from exc

    for cleanup_path in cleanup_paths:
        try:
            cleanup_path.unlink()
        except Exception as exc:
            result.warnings.append(f"Cleanup failed for {cleanup_path}: {exc}")

    if json_output:
        click.echo(json.dumps(_import_result_to_dict(result), indent=2))
        return

    _print_import_result(result, "Skill import completed successfully.")


@main.command("extract-text")
@click.argument("pdf_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--max-pages", default=100, show_default=True, help="Maximum number of pages to extract.")
@click.option("--output", type=click.Path(dir_okay=False), help="Write extracted markdown to a file instead of stdout.")
def extract_text(pdf_path: str, max_pages: int, output: str | None) -> None:
    """Extract PDF text as markdown for skill fallback workflows."""
    from paper_assistant.pdf import extract_text_from_pdf

    try:
        markdown = extract_text_from_pdf(Path(pdf_path), max_pages=max_pages)
    except Exception as exc:
        raise click.ClickException(f"Failed to extract text: {exc}") from exc

    if output:
        output_path = Path(output)
        output_path.write_text(markdown, encoding="utf-8")
        console.print(f"[green]Extracted markdown written to[/green] {output_path}")
        return

    click.echo(markdown, nl=False)


@main.command("create")
@click.option("--title", required=True, help="Title for the local note entry.")
@click.option("--source-url", help="Optional canonical or bookmark URL for this note.")
@click.option(
    "--file",
    "-f",
    "file_path",
    type=click.Path(exists=True),
    help="Read markdown from file instead of clipboard.",
)
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option(
    "--skip-transcript",
    is_flag=True,
    help="Skip the derived narration transcript (audio falls back to raw summary).",
)
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this note.")
@click.option(
    "--script-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Use this file as the narration transcript instead of generating one.",
)
@click.option(
    "--no-script-fallback",
    is_flag=True,
    default=False,
    help="Never call the Anthropic API for narration; require --script-file or warn.",
)
@click.option("--cleanup-file", multiple=True, help="Temporary file to delete after a successful create.")
@click.option("--json", "json_output", is_flag=True, help="Output the created entry as JSON.")
@click.pass_context
def create_note(
    ctx: click.Context,
    title: str,
    source_url: str | None,
    file_path: str | None,
    skip_audio: bool,
    skip_transcript: bool,
    tags: tuple[str, ...],
    script_file: str | None,
    no_script_fallback: bool,
    cleanup_file: tuple[str, ...],
    json_output: bool,
) -> None:
    """Create a local markdown-backed note entry from clipboard or file."""
    cleanup_paths = _validate_cleanup_files(cleanup_file)
    markdown = _read_markdown_input(file_path)

    if not markdown.strip():
        message = "No markdown content found."
        if not file_path:
            message += " Copy your note markdown to the clipboard first, or use --file."
        # Exit non-zero so automated callers (e.g. /synthesize parsing --json
        # output) never mistake an empty input for a successful create.
        raise click.ClickException(message)

    provided_script_markdown: str | None = None
    if script_file is not None:
        provided_script_markdown = Path(script_file).read_text(encoding="utf-8").strip()
        if not provided_script_markdown:
            raise click.ClickException("--script-file was empty.")

    if not json_output:
        console.print("[bold]Creating local note entry...[/bold]")
    try:
        config, outcome = asyncio.run(
            _run_create_pipeline(
                ctx.obj,
                title=title,
                source_url=source_url,
                markdown=markdown,
                skip_audio=skip_audio,
                skip_transcript=skip_transcript,
                tags=list(tags),
                provided_script_markdown=provided_script_markdown,
                skip_script_generation=no_script_fallback,
            )
        )
    except Exception as exc:
        recovery_paths = _recovery_artifact_paths(file_path, cleanup_paths, script_file)
        if recovery_paths:
            console.print("[yellow]Artifacts preserved for manual recovery:[/yellow]")
            for recovery_path in recovery_paths:
                console.print(f"  - {recovery_path}")
        raise click.ClickException(str(exc)) from exc

    for cleanup_path in cleanup_paths:
        try:
            cleanup_path.unlink()
        except Exception as exc:
            outcome.warnings.append(f"Cleanup failed for {cleanup_path}: {exc}")

    paper = outcome.paper
    paper_id = paper.metadata.paper_id

    if paper.audio_path and config.icloud_sync:
        icloud_error = _copy_to_icloud(
            config, paper, paper.metadata.title, paper_id, quiet=json_output
        )
        if icloud_error:
            outcome.warnings.append(icloud_error)

    if json_output:
        click.echo(json.dumps(_local_entry_result_to_dict(outcome, config), indent=2))
        return

    console.print()
    console.print("[green]Done![/green] Local note created successfully.")
    console.print(f"  ID:      {paper_id}")
    console.print(f"  Summary: {outcome.summary_path}")
    if paper.transcript_path:
        console.print(f"  Transcript: {config.data_dir / paper.transcript_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")
    for warning in outcome.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


async def _run_create_pipeline(
    obj: dict,
    *,
    title: str,
    source_url: str | None,
    markdown: str,
    skip_audio: bool,
    skip_transcript: bool,
    tags: list[str],
    provided_script_markdown: str | None = None,
    skip_script_generation: bool = False,
):
    from paper_assistant.config import load_config
    from paper_assistant.pipeline import create_local_entry
    from paper_assistant.storage import StorageManager

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    outcome = await create_local_entry(
        config=config,
        storage=storage,
        title=title,
        markdown=markdown,
        source_url=source_url,
        tags=tags,
        skip_audio=skip_audio,
        skip_transcript=skip_transcript,
        provided_script_markdown=provided_script_markdown,
        skip_script_generation=skip_script_generation,
    )
    return config, outcome


def _local_entry_result_to_dict(outcome, config) -> dict[str, object]:
    paper = outcome.paper
    return {
        # Post-dedupe slug (invariant 1c) — authoritative for follow-up
        # commands like `notion-sync --paper <id>`.
        "paper_id": paper.metadata.paper_id,
        "title": paper.metadata.title,
        # .absolute() keeps the documented absolute-path contract even when
        # the data dir was configured relative to the current directory.
        "summary_path": str(Path(outcome.summary_path).absolute()),
        "transcript_path": (
            str((config.data_dir / paper.transcript_path).absolute())
            if paper.transcript_path
            else None
        ),
        "audio_path": (
            str((config.data_dir / paper.audio_path).absolute())
            if paper.audio_path
            else None
        ),
        "warnings": outcome.warnings,
    }


@main.command("list")
@click.option(
    "--status",
    type=click.Choice(["all", "complete", "error", "pending"]),
    default="all",
    help="Filter by processing status.",
)
@click.option("--tag", "-t", help="Filter by tag.")
@click.option("--json", "json_output", is_flag=True, help="Output papers as JSON.")
@click.pass_context
def list_papers(ctx: click.Context, status: str, tag: str | None, json_output: bool) -> None:
    """List all processed papers."""
    from paper_assistant.config import load_config
    from paper_assistant.models import ProcessingStatus
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    status_filter = None if status == "all" else ProcessingStatus(status)
    papers = storage.list_papers(status=status_filter, tag=tag)

    if json_output:
        entries = [
            {
                "paper_id": p.metadata.paper_id,
                "title": p.metadata.title,
                "tags": p.tags,
                "date_added": p.date_added.isoformat(),
                "source_type": p.metadata.source_type.value,
                "status": p.status.value,
                "reading_status": p.reading_status.value,
                # Absolute path — the supported way for skill workflows to
                # locate stored summaries without guessing the data dir.
                # .absolute() covers relative --data-dir/PAPER_ASSIST_DATA_DIR.
                "summary_path": (
                    str((config.data_dir / p.summary_path).absolute())
                    if p.summary_path
                    else None
                ),
                "has_audio": p.audio_path is not None,
                "arxiv_id": p.metadata.arxiv_id,
                "source_url": p.metadata.source_url,
            }
            for p in papers
        ]
        click.echo(json.dumps(entries, indent=2))
        return

    if not papers:
        console.print("[dim]No papers found.[/dim]")
        return

    table = Table(title="Papers", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", max_width=50)
    table.add_column("Added", no_wrap=True)
    table.add_column("Status", style="green")
    table.add_column("Audio", justify="center")
    table.add_column("Tags")

    for p in papers:
        table.add_row(
            p.metadata.paper_id,
            p.metadata.title[:50] + ("..." if len(p.metadata.title) > 50 else ""),
            p.date_added.strftime("%Y-%m-%d"),
            p.status.value,
            "Y" if p.audio_path else "-",
            ", ".join(p.tags) if p.tags else "",
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(papers)} papers[/dim]")


@main.command()
@click.argument("paper_id")
@click.option(
    "--body",
    "body_only",
    is_flag=True,
    help="Print the normalized summary body as plain markdown (YAML front matter and generated header stripped via normalize_summary_body) — for skill workflows.",
)
@click.pass_context
def show(ctx: click.Context, paper_id: str, body_only: bool) -> None:
    """Display the summary for a specific paper."""
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)
    paper = storage.get_paper(paper_id)

    if not paper:
        if body_only:
            raise click.ClickException(f"Paper {paper_id} not found.")
        console.print(f"[red]Paper {paper_id} not found.[/red]")
        return

    if not paper.summary_path:
        if body_only:
            raise click.ClickException(f"Paper {paper_id} has no summary yet.")
        console.print(f"[yellow]Paper {paper_id} has no summary yet.[/yellow]")
        return

    content = (config.data_dir / paper.summary_path).read_text(encoding="utf-8")
    if body_only:
        from paper_assistant.summarizer import normalize_summary_body

        click.echo(normalize_summary_body(content))
        return
    console.print(Markdown(content))


@main.command()
@click.argument("paper_id")
@click.option("--keep-files", is_flag=True, help="Keep generated files, only remove from index.")
@click.confirmation_option(prompt="Are you sure you want to remove this paper?")
@click.pass_context
def remove(ctx: click.Context, paper_id: str, keep_files: bool) -> None:
    """Remove a paper from the index."""
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    if storage.delete_paper(paper_id, delete_files=not keep_files):
        from paper_assistant.search import get_search_manager

        search_mgr = get_search_manager(config)
        if search_mgr:
            try:
                search_mgr.delete_paper(paper_id)
            except Exception:
                console.print("[yellow]Warning: Search index update failed.[/yellow]")
        console.print(f"[green]Paper {paper_id} removed.[/green]")
    else:
        console.print(f"[red]Paper {paper_id} not found.[/red]")


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--port", default=8877, type=int, help="Port to listen on.")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Start the web UI and podcast feed server."""
    import uvicorn

    from paper_assistant.config import load_config
    from paper_assistant.web.app import create_app

    config = load_config(**ctx.obj)
    config.ensure_dirs()
    app = create_app(config)

    console.print(f"Starting Paper Assistant at [bold]http://{host}:{port}[/bold]")
    console.print(f"RSS feed: http://{host}:{port}/feed.xml")
    console.print("Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command("regenerate-feed")
@click.pass_context
def regenerate_feed(ctx: click.Context) -> None:
    """Regenerate the RSS podcast feed from existing data."""
    from paper_assistant.config import load_config
    from paper_assistant.podcast import generate_feed
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)
    papers = storage.list_papers()

    generate_feed(config, papers)
    console.print(f"[green]Feed regenerated:[/green] {config.feed_path}")


@main.group("bundle")
def bundle_group() -> None:
    """Export and import portable local paper bundles."""


@bundle_group.command("export")
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--paper", "paper_ids", multiple=True, help="Export only this paper ID; repeatable.")
@click.option("--json", "json_output", is_flag=True, help="Output export report as JSON.")
@click.pass_context
def bundle_export(
    ctx: click.Context,
    output: str,
    paper_ids: tuple[str, ...],
    json_output: bool,
) -> None:
    """Export local records and assets to a portable zip bundle.

    Notion linkage metadata is stripped from the bundle.
    """
    from paper_assistant.bundle import export_bundle
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    try:
        report = export_bundle(
            config,
            storage,
            Path(output),
            paper_ids=paper_ids or None,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps({
            "bundle_path": str(report.bundle_path),
            "exported_papers": report.exported_papers,
            "exported_files": report.exported_files,
            "paper_ids": report.paper_ids,
            "warnings": report.warnings,
        }, indent=2))
        return

    console.print(f"[green]Bundle exported:[/green] {report.bundle_path}")
    console.print(
        f"  Papers: {report.exported_papers}  Files: {report.exported_files}"
    )
    console.print("  Notion metadata: stripped")
    if report.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in report.warnings:
            console.print(f"  - {warning}")


@bundle_group.command("import")
@click.argument("bundle_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--paper", "paper_ids", multiple=True, help="Import only this paper ID; repeatable.")
@click.option("--dry-run", is_flag=True, help="Preview the merge without writing files.")
@click.option("--force", is_flag=True, help="Merge over existing paper IDs instead of skipping them.")
@click.option("--json", "json_output", is_flag=True, help="Output import report as JSON.")
@click.pass_context
def bundle_import(
    ctx: click.Context,
    bundle_path: str,
    paper_ids: tuple[str, ...],
    dry_run: bool,
    force: bool,
    json_output: bool,
) -> None:
    """Import a portable zip bundle into the local library."""
    from paper_assistant.bundle import import_bundle
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    try:
        report = import_bundle(
            config,
            storage,
            Path(bundle_path),
            paper_ids=paper_ids or None,
            dry_run=dry_run,
            force=force,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    feed_warning: str | None = None
    search_warning: str | None = None
    if not dry_run and report.touched_paper_ids:
        try:
            from paper_assistant.podcast import generate_feed

            generate_feed(config, storage.list_papers())
        except Exception as exc:
            feed_warning = f"Feed regeneration failed: {exc}"

        try:
            from paper_assistant.search import get_search_manager

            search_mgr = get_search_manager(config)
            if search_mgr:
                search_mgr.batch_sync(report.touched_paper_ids, storage)
        except Exception as exc:
            search_warning = f"Search index update failed: {exc}"

    warnings = [*report.warnings]
    if feed_warning:
        warnings.append(feed_warning)
    if search_warning:
        warnings.append(search_warning)

    if json_output:
        click.echo(json.dumps({
            "created": report.created,
            "updated": report.updated,
            "skipped": report.skipped,
            "imported_files": report.imported_files,
            "paper_ids": report.paper_ids,
            "dry_run": dry_run,
            "force": force,
            "warnings": warnings,
        }, indent=2))
        return

    label = "Bundle import preview" if dry_run else "Bundle imported"
    console.print(f"[green]{label}:[/green] {bundle_path}")
    console.print(
        "  Created: {created}  Updated: {updated}  Skipped: {skipped}  Files: {files}".format(
            created=report.created,
            updated=report.updated,
            skipped=report.skipped,
            files=report.imported_files,
        )
    )
    console.print("  Notion sync: not run")
    if warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in warnings:
            console.print(f"  - {warning}")


@main.command("search")
@click.argument("query")
@click.option("--limit", "-n", default=10, show_default=True, help="Maximum number of results.")
@click.option(
    "--mode",
    type=click.Choice(["text", "vector", "hybrid"]),
    default="hybrid",
    show_default=True,
    help="Search mode: text (BM25), vector (semantic), or hybrid (BM25 + vector + LLM re-ranking).",
)
@click.option("--json", "json_output", is_flag=True, help="Output results as JSON.")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    limit: int,
    mode: str,
    json_output: bool,
) -> None:
    """Search across paper summaries and metadata."""
    from paper_assistant.config import load_config
    from paper_assistant.search import EmbeddingsNotAvailableError, SearchManager

    config = load_config(**ctx.obj)

    if not config.qmd_enabled:
        console.print(
            "[red]Search requires qmd.[/red] Install it with "
            "`bun install -g github:tobi/qmd` and set `PAPER_ASSIST_QMD_ENABLED=true`."
        )
        raise SystemExit(1)

    mgr = SearchManager(config)
    if not mgr.is_available():
        console.print(
            "[red]Search requires qmd.[/red] Install it with "
            "`bun install -g github:tobi/qmd` and set `PAPER_ASSIST_QMD_ENABLED=true`."
        )
        raise SystemExit(1)

    try:
        results = mgr.search(query, limit=limit, mode=mode)
    except EmbeddingsNotAvailableError:
        console.print(
            "[yellow]Embeddings not available — falling back to text search.[/yellow] "
            "Run `paper-assist index-rebuild --embed` for hybrid search."
        )
        try:
            results = mgr.search(query, limit=limit, mode="text")
        except Exception as e:
            console.print(f"[red]Search failed:[/red] {e}")
            raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Search failed:[/red] {e}")
        raise SystemExit(1)

    if json_output:
        import json as json_mod
        click.echo(json_mod.dumps(
            [{"paper_id": r.paper_id, "title": r.title, "score": r.score, "snippet": r.snippet} for r in results],
            indent=2,
        ))
        return

    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(title=f"Search: {query}")
    table.add_column("#", style="dim", width=3)
    table.add_column("Paper ID", style="cyan", no_wrap=True)
    table.add_column("Title", max_width=50)
    table.add_column("Score", justify="right", width=8)

    for i, r in enumerate(results, 1):
        table.add_row(str(i), r.paper_id, r.title or r.paper_id, f"{r.score:.2f}")

    console.print(table)
    console.print(f"\n[dim]{len(results)} result(s)[/dim]")


@main.command("index-setup")
@click.pass_context
def index_setup(ctx: click.Context) -> None:
    """Set up the qmd search index (idempotent)."""
    from paper_assistant.config import load_config
    from paper_assistant.search import SearchManager
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)

    if not config.qmd_enabled:
        console.print(
            "[red]Search requires qmd.[/red] Set `PAPER_ASSIST_QMD_ENABLED=true`."
        )
        raise SystemExit(1)

    mgr = SearchManager(config)
    if not mgr.is_available():
        console.print(
            "[red]qmd binary not found.[/red] Install it with "
            "`bun install -g github:tobi/qmd`."
        )
        raise SystemExit(1)

    console.print("[bold]Setting up search index...[/bold]")
    mgr.setup()

    storage = StorageManager(config)
    console.print("Rebuilding search documents...")
    mgr.rebuild_all(storage)

    console.print("[green]Search index ready.[/green]")


@main.command("index-rebuild")
@click.option("--embed", is_flag=True, help="Also generate vector embeddings (slow).")
@click.pass_context
def index_rebuild(ctx: click.Context, embed: bool) -> None:
    """Regenerate all search documents and update the index."""
    from paper_assistant.config import load_config
    from paper_assistant.search import SearchManager
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)

    if not config.qmd_enabled:
        console.print(
            "[red]Search requires qmd.[/red] Set `PAPER_ASSIST_QMD_ENABLED=true`."
        )
        raise SystemExit(1)

    mgr = SearchManager(config)
    if not mgr.is_available():
        console.print(
            "[red]qmd binary not found.[/red] Install it with "
            "`bun install -g github:tobi/qmd`."
        )
        raise SystemExit(1)

    storage = StorageManager(config)
    console.print("[bold]Rebuilding search documents...[/bold]")
    mgr.rebuild_all(storage)
    console.print("[green]Search documents rebuilt.[/green]")

    if embed:
        console.print("[bold]Generating embeddings...[/bold] (this may take a while)")
        mgr.generate_embeddings()
        console.print("[green]Embeddings generated.[/green]")


@main.command("notion-sync")
@click.option("--paper", "paper_id", help="Sync only one paper by ID or Notion page ID.")
@click.option("--dry-run", is_flag=True, help="Preview sync actions without writing changes.")
@click.pass_context
def notion_sync(ctx: click.Context, paper_id: str | None, dry_run: bool) -> None:
    """Run manual two-way sync between local storage and Notion."""
    asyncio.run(_notion_sync(ctx.obj, paper_id, dry_run))


async def _notion_sync(obj: dict, paper_id: str | None, dry_run: bool) -> None:
    from paper_assistant.config import load_config
    from paper_assistant.notion import describe_exception, sync_notion
    from paper_assistant.storage import StorageManager

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    mode = "preview" if dry_run else "apply"
    target = paper_id if paper_id else "all papers"
    console.print(f"[bold]Notion sync ({mode})[/bold]: {target}")

    try:
        report = await sync_notion(
            config=config,
            storage=storage,
            paper_id=paper_id,
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[red]Notion sync failed:[/red] {describe_exception(e)}")
        return

    if not dry_run and report.touched_paper_ids:
        from paper_assistant.search import get_search_manager

        search_mgr = get_search_manager(config)
        if search_mgr:
            try:
                search_mgr.batch_sync(report.touched_paper_ids, storage)
            except Exception:
                console.print("[yellow]Warning: Search index update failed.[/yellow]")

    data = report.to_dict()
    console.print(
        "  Local   created={local_created} updated={local_updated} archived={local_archived}".format(
            **data
        )
    )
    console.print(
        "  Notion  created={notion_created} updated={notion_updated} archived={notion_archived}".format(
            **data
        )
    )
    console.print(f"  Skipped: {data['skipped']}")

    if data["warnings"]:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in data["warnings"]:
            console.print(f"  - {warning}")
    if data["errors"]:
        console.print("[red]Errors:[/red]")
        for error in data["errors"]:
            console.print(f"  - {error}")


@main.command("notion-preflight")
@click.pass_context
def notion_preflight(ctx: click.Context) -> None:
    """Verify that Notion sync can reach the configured database."""
    asyncio.run(_notion_preflight(ctx.obj))


async def _notion_preflight(obj: dict) -> None:
    from paper_assistant.config import load_config
    from paper_assistant.notion import preflight_notion

    config = load_config(**obj)

    try:
        await preflight_notion(config=config)
    except Exception as e:
        raise click.ClickException(str(e)) from e

    console.print("[green]Notion preflight passed.[/green] Database is reachable and schema-compatible.")


@main.group("transcript")
def transcript_group() -> None:
    """Manage narration transcripts for existing papers."""


@transcript_group.command("regenerate")
@click.argument("paper_id")
@click.option(
    "--model",
    default=None,
    help="Override the Claude model used for narration (defaults to config).",
)
@click.option(
    "--script-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Use this file as the narration transcript instead of generating one.",
)
@click.pass_context
def transcript_regenerate(
    ctx: click.Context,
    paper_id: str,
    model: str | None,
    script_file: str | None,
) -> None:
    """Regenerate narration transcript + audio for an existing paper."""
    provided_script: str | None = None
    if script_file is not None:
        provided_script = Path(script_file).read_text(encoding="utf-8").strip()
        if not provided_script:
            raise click.ClickException("--script-file was empty.")

    asyncio.run(
        _transcript_regenerate(
            ctx.obj,
            paper_id=paper_id,
            model=model,
            provided_script=provided_script,
        )
    )


async def _transcript_regenerate(
    obj: dict,
    *,
    paper_id: str,
    model: str | None,
    provided_script: str | None,
) -> None:
    from paper_assistant.config import load_config
    from paper_assistant.pipeline import regenerate_transcript_and_audio
    from paper_assistant.storage import StorageManager

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    console.print(f"[bold]Regenerating transcript + audio for {paper_id}...[/bold]")
    try:
        result = await regenerate_transcript_and_audio(
            config=config,
            storage=storage,
            paper_id=paper_id,
            provided_script_markdown=provided_script,
            script_model_override=model,
        )
    except KeyError:
        raise click.ClickException(f"Paper {paper_id} not found.")
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    from paper_assistant.search import get_search_manager

    search_mgr = get_search_manager(config)
    if search_mgr:
        try:
            search_mgr.sync_paper(paper_id, storage)
        except Exception:
            console.print("[yellow]Warning: Search index update failed.[/yellow]")

    console.print()
    console.print(f"[green]Done![/green] {result.title}")
    if result.transcript_path:
        console.print(f"  Transcript: {result.transcript_path}")
    if result.audio_path:
        console.print(f"  Audio:      {result.audio_path}")
    if result.script_model:
        console.print(f"  Script model: {result.script_model}")
    if result.backend_used:
        console.print(f"  Backend:    {result.backend_used}")
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@main.group("tts")
def tts_group() -> None:
    """Text-to-speech diagnostics."""


@tts_group.command("check")
@click.pass_context
def tts_check(ctx: click.Context) -> None:
    """Probe the configured TTS backend and report readiness."""
    asyncio.run(_tts_check(ctx.obj))


async def _tts_check(obj: dict) -> None:
    import shutil
    import time

    import httpx

    from paper_assistant.config import load_config
    from paper_assistant.tts import (
        EdgeTTSError,
        MlxConfigError,
        MlxTransientError,
        TTSBackendError,
        get_edge_backend,
        get_tts_backend,
    )

    config = load_config(**obj)
    config.ensure_dirs()

    console.print(f"[bold]TTS backend:[/bold] {config.tts_backend}")

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        console.print(f"  ffmpeg: [green]present[/green] ({ffmpeg_path})")
    else:
        console.print("  ffmpeg: [yellow]missing[/yellow] — long papers may fail to concat; `brew install ffmpeg`.")

    exit_code = 0

    if config.tts_backend == "mlx":
        url = config.mlx_tts_url.rstrip("/")
        console.print(f"  MLX url: {url}")
        console.print(f"  MLX model: {config.mlx_tts_model}")
        if config.mlx_tts_voice:
            console.print(f"  MLX voice (generic): {config.mlx_tts_voice}")
        if config.mlx_tts_speaker:
            console.print(f"  MLX speaker (optional/model-specific): {config.mlx_tts_speaker}")
        elif config.mlx_tts_voice and "qwen3-tts" in config.mlx_tts_model.lower():
            console.print(
                "  MLX speaker (optional/model-specific): "
                f"{config.mlx_tts_voice} [dim](mirrored from MLX voice when the server supports a separate `speaker` field)[/dim]"
            )

        models_url = f"{url}/v1/models"
        headers = {}
        if config.mlx_tts_api_key:
            headers["Authorization"] = f"Bearer {config.mlx_tts_api_key}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(models_url, headers=headers)
            console.print(f"  GET /v1/models → {resp.status_code}")
            if resp.status_code >= 400:
                console.print(f"  [red]Models endpoint rejected request:[/red] {resp.text[:200]}")
                exit_code = 1
        except Exception as exc:
            console.print(f"  [red]MLX /v1/models unreachable:[/red] {exc}")
            exit_code = 1

    probe_path = config.audio_dir / "_tts_probe.mp3"
    probe_text = "This is a one-sentence probe of the paper assistant text to speech pipeline."

    backend = get_tts_backend(config)
    start = time.monotonic()
    primary_failed = False
    try:
        await backend.synthesize(probe_text, probe_path)
        elapsed = time.monotonic() - start
        size = probe_path.stat().st_size if probe_path.exists() else 0
        console.print(
            f"  Primary probe: [green]{backend.name}[/green] ok in {elapsed:.1f}s ({size} bytes)"
        )
    except MlxConfigError as exc:
        console.print(f"  Primary probe: [red]{backend.name} config error[/red] — {exc}")
        console.print("  Fix config and retry — fallback suppressed to keep the bug visible.")
        exit_code = 1
        primary_failed = True
    except MlxTransientError as exc:
        console.print(f"  Primary probe: [yellow]{backend.name} transient[/yellow] — {exc}")
        primary_failed = True
    except TTSBackendError as exc:
        console.print(f"  Primary probe: [yellow]{backend.name} failed[/yellow] — {exc}")
        primary_failed = True
    except Exception as exc:
        console.print(f"  Primary probe: [red]unexpected error[/red] — {exc}")
        exit_code = 1
        primary_failed = True

    if primary_failed and config.tts_backend == "mlx" and config.tts_edge_fallback:
        edge = get_edge_backend(config)
        try:
            start = time.monotonic()
            await edge.synthesize(probe_text, probe_path)
            elapsed = time.monotonic() - start
            size = probe_path.stat().st_size if probe_path.exists() else 0
            console.print(
                f"  Fallback probe: [green]edge[/green] ok in {elapsed:.1f}s ({size} bytes)"
            )
        except EdgeTTSError as exc:
            console.print(f"  Fallback probe: [red]edge failed[/red] — {exc}")
            exit_code = 1

    if probe_path.exists():
        try:
            probe_path.unlink()
        except OSError:
            pass

    if exit_code != 0:
        raise SystemExit(exit_code)
