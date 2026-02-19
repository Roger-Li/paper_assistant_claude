"""CLI entry point for Paper Assistant."""

from __future__ import annotations

import asyncio

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
@click.option("--native-pdf", is_flag=True, help="Send raw PDF to Claude (preserves figures, more expensive).")
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this paper.")
@click.option("--force", is_flag=True, help="Re-process even if paper already exists.")
@click.pass_context
def add(
    ctx: click.Context,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    tags: tuple[str, ...],
    force: bool,
) -> None:
    """Add and summarize a paper from an arXiv URL or web article URL.

    Examples:
      paper-assist add https://arxiv.org/abs/2503.10291
      paper-assist add https://example.com/blog/article
    """
    asyncio.run(_add_paper(ctx.obj, url, native_pdf, skip_audio, list(tags), force))


async def _add_paper(
    obj: dict,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """Full pipeline: fetch -> extract -> summarize -> TTS -> RSS."""
    from paper_assistant.web_article import is_arxiv_url

    if is_arxiv_url(url):
        await _add_arxiv_paper(obj, url, native_pdf, skip_audio, tags, force)
    else:
        await _add_web_article(obj, url, skip_audio, tags, force)


async def _add_arxiv_paper(
    obj: dict,
    url: str,
    native_pdf: bool,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """arXiv paper pipeline: fetch -> extract -> summarize -> TTS -> RSS."""
    from paper_assistant.arxiv import download_pdf, fetch_metadata, parse_arxiv_url
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.podcast import generate_feed
    from paper_assistant.storage import StorageManager, make_audio_filename, make_pdf_filename
    from paper_assistant.summarizer import (
        format_summary_file,
        summarize_paper_pdf,
        summarize_paper_text,
    )
    from paper_assistant.tts import prepare_text_for_tts, text_to_speech

    config = load_config(**obj)
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
        metadata = await fetch_metadata(arxiv_id, config=config)
    except Exception as e:
        console.print(f"[red]Error fetching metadata:[/red] {e}")
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

    # Step 2: Download PDF
    console.print("[bold]Step 2/5:[/bold] Downloading PDF...")
    pdf_path = config.pdfs_dir / make_pdf_filename(paper_id)
    try:
        await download_pdf(arxiv_id, pdf_path, config=config)
        paper.pdf_path = f"pdfs/{make_pdf_filename(paper_id)}"
        paper.status = ProcessingStatus.FETCHED
        storage.add_paper(paper)
    except Exception as e:
        console.print(f"[red]Error downloading PDF:[/red] {e}")
        paper.status = ProcessingStatus.ERROR
        paper.error_message = str(e)
        storage.add_paper(paper)
        return

    # Step 3: Summarize with Claude
    console.print(f"[bold]Step 3/5:[/bold] Summarizing with {config.claude_model}...")
    try:
        if native_pdf:
            result = await summarize_paper_pdf(config, metadata, pdf_path)
        else:
            from paper_assistant.pdf import extract_text_from_pdf

            paper_text = extract_text_from_pdf(pdf_path)
            result = await summarize_paper_text(config, metadata, paper_text)

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
        config, storage, paper, result, metadata, paper_id, skip_audio, "4/5"
    )

    # Step 5: Update RSS feed
    _update_feed_step(config, storage, paper, "5/5")

    # Copy audio to iCloud Drive for iPhone access
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    console.print()
    console.print("[green]Done![/green] Paper processed successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


async def _add_web_article(
    obj: dict,
    url: str,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """Web article pipeline: fetch -> summarize -> TTS -> RSS."""
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.storage import StorageManager
    from paper_assistant.summarizer import format_summary_file, summarize_article_text
    from paper_assistant.web_article import fetch_article

    config = load_config(**obj)
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
        config, storage, paper, result, metadata, paper_id, skip_audio, "3/4"
    )

    # Step 4: Update RSS feed
    _update_feed_step(config, storage, paper, "4/4")

    # Copy audio to iCloud Drive
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    console.print()
    console.print("[green]Done![/green] Article processed successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


async def _generate_audio_step(
    config, storage, paper, result, metadata, paper_id, skip_audio, step_label
):
    """Shared audio generation step for both arXiv and web article pipelines."""
    from paper_assistant.models import ProcessingStatus, SourceType
    from paper_assistant.storage import make_audio_filename
    from paper_assistant.tts import prepare_text_for_tts, text_to_speech

    if not skip_audio:
        console.print(f"[bold]Step {step_label}:[/bold] Generating audio...")
        try:
            source_label = (
                "article" if metadata.source_type == SourceType.WEB else "paper"
            )
            tts_text = prepare_text_for_tts(
                result.full_markdown, metadata.title, metadata.authors,
                source_label=source_label,
            )
            audio_path = config.audio_dir / make_audio_filename(paper_id)
            await text_to_speech(tts_text, audio_path, config.tts_voice, config.tts_rate)
            paper.audio_path = f"audio/{make_audio_filename(paper_id)}"
            paper.status = ProcessingStatus.AUDIO_GENERATED
            storage.add_paper(paper)
        except Exception as e:
            console.print(f"[yellow]Warning: TTS failed:[/yellow] {e}")
            console.print("  Summary was saved. Audio can be regenerated later.")
    else:
        console.print(f"[bold]Step {step_label}:[/bold] Skipping audio (--skip-audio).")


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


def _copy_to_icloud(config, paper, title, paper_id):
    """Copy audio to iCloud Drive for iPhone access."""
    import shutil

    try:
        config.icloud_dir.mkdir(parents=True, exist_ok=True)
        safe_title = title[:60].replace("/", "-").replace(":", " -")
        icloud_dest = config.icloud_dir / f"{safe_title} [{paper_id}].mp3"
        shutil.copy2(config.data_dir / paper.audio_path, icloud_dest)
        console.print(f"  iCloud:  Synced to {icloud_dest.name}")
    except Exception as e:
        console.print(f"[yellow]Warning: iCloud copy failed:[/yellow] {e}")


@main.command("import")
@click.argument("url")
@click.option("--file", "-f", "file_path", type=click.Path(exists=True), help="Read markdown from file instead of clipboard.")
@click.option("--skip-audio", is_flag=True, help="Skip TTS audio generation.")
@click.option("--tags", "-t", multiple=True, help="Tags to apply to this paper.")
@click.option("--force", is_flag=True, help="Re-import even if paper already exists.")
@click.pass_context
def import_paper(
    ctx: click.Context,
    url: str,
    file_path: str | None,
    skip_audio: bool,
    tags: tuple[str, ...],
    force: bool,
) -> None:
    """Import a pre-generated summary from clipboard or file.

    Reads markdown from the macOS clipboard (pbpaste) by default,
    or from a file with --file. Skips the Claude API summarization step.

    Examples:
      paper-assist import https://arxiv.org/abs/2503.10291
      paper-assist import https://example.com/blog/article --file summary.md
    """
    import subprocess

    if file_path:
        from pathlib import Path

        markdown = Path(file_path).read_text(encoding="utf-8")
    else:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        markdown = result.stdout

    if not markdown.strip():
        console.print("[red]No markdown content found.[/red]")
        if not file_path:
            console.print("Copy your summary to the clipboard first, or use --file.")
        return

    asyncio.run(
        _import_paper(ctx.obj, url, markdown, skip_audio, list(tags), force)
    )


async def _import_paper(
    obj: dict,
    url: str,
    markdown: str,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """Import pipeline: parse URL -> fetch metadata -> save summary -> TTS -> RSS."""
    from paper_assistant.web_article import is_arxiv_url

    if is_arxiv_url(url):
        await _import_arxiv_paper(obj, url, markdown, skip_audio, tags, force)
    else:
        await _import_web_article(obj, url, markdown, skip_audio, tags, force)


async def _import_arxiv_paper(
    obj: dict,
    url: str,
    markdown: str,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """Import pipeline for arXiv papers."""
    from paper_assistant.arxiv import fetch_metadata, parse_arxiv_url
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.storage import StorageManager
    from paper_assistant.summarizer import (
        SummarizationResult,
        find_one_pager,
        format_summary_file,
        parse_summary_sections,
    )

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    # Step 1: Parse URL and fetch metadata
    console.print("[bold]Step 1/4:[/bold] Parsing arXiv URL...")
    try:
        arxiv_id = parse_arxiv_url(url)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if storage.paper_exists(arxiv_id) and not force:
        console.print(
            f"[yellow]Paper {arxiv_id} already exists. Use --force to re-import.[/yellow]"
        )
        return

    console.print(f"[bold]Step 1/4:[/bold] Fetching metadata for {arxiv_id}...")
    try:
        metadata = await fetch_metadata(arxiv_id, config=config)
    except Exception as e:
        console.print(f"[red]Error fetching metadata:[/red] {e}")
        return

    paper_id = metadata.paper_id
    console.print(f"  Title: [cyan]{metadata.title}[/cyan]")
    console.print(f"  Authors: {', '.join(metadata.authors[:3])}")

    # Step 2: Parse and save summary
    console.print("[bold]Step 2/4:[/bold] Saving imported summary...")
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
        tags=tags,
        status=ProcessingStatus.PENDING,
        model_used="manual",
    )
    storage.add_paper(paper)

    summary_content = format_summary_file(metadata, result)
    summary_path = storage.save_summary(paper_id, summary_content)
    paper = storage.get_paper(paper_id)  # Re-fetch with updated summary_path

    # Step 3: Generate audio
    await _generate_audio_step(
        config, storage, paper, result, metadata, paper_id, skip_audio, "3/4"
    )

    # Step 4: Update RSS feed
    _update_feed_step(config, storage, paper, "4/4")

    # Copy audio to iCloud Drive
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    console.print()
    console.print("[green]Done![/green] Paper imported successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


async def _import_web_article(
    obj: dict,
    url: str,
    markdown: str,
    skip_audio: bool,
    tags: list[str],
    force: bool,
) -> None:
    """Import pipeline for web articles."""
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.storage import StorageManager
    from paper_assistant.summarizer import (
        SummarizationResult,
        find_one_pager,
        format_summary_file,
        parse_summary_sections,
    )
    from paper_assistant.web_article import fetch_article

    config = load_config(**obj)
    config.ensure_dirs()
    storage = StorageManager(config)

    # Step 1: Fetch article metadata
    console.print("[bold]Step 1/4:[/bold] Fetching article metadata...")
    try:
        metadata, _body_text = await fetch_article(url)
    except Exception as e:
        console.print(f"[red]Error fetching article metadata:[/red] {e}")
        return

    paper_id = metadata.paper_id
    if storage.paper_exists(paper_id) and not force:
        console.print(
            f"[yellow]Article {paper_id} already exists. Use --force to re-import.[/yellow]"
        )
        return

    console.print(f"  Title: [cyan]{metadata.title}[/cyan]")
    if metadata.authors:
        console.print(f"  Authors: {', '.join(metadata.authors[:3])}")

    # Step 2: Parse and save summary
    console.print("[bold]Step 2/4:[/bold] Saving imported summary...")
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
        tags=tags,
        status=ProcessingStatus.PENDING,
        model_used="manual",
    )
    storage.add_paper(paper)

    summary_content = format_summary_file(metadata, result)
    summary_path = storage.save_summary(paper_id, summary_content)
    paper = storage.get_paper(paper_id)

    # Step 3: Generate audio
    await _generate_audio_step(
        config, storage, paper, result, metadata, paper_id, skip_audio, "3/4"
    )

    # Step 4: Update RSS feed
    _update_feed_step(config, storage, paper, "4/4")

    # Copy audio to iCloud Drive
    if paper.audio_path and config.icloud_sync:
        _copy_to_icloud(config, paper, metadata.title, paper_id)

    console.print()
    console.print("[green]Done![/green] Article imported successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


@main.command("list")
@click.option(
    "--status",
    type=click.Choice(["all", "complete", "error", "pending"]),
    default="all",
    help="Filter by processing status.",
)
@click.option("--tag", "-t", help="Filter by tag.")
@click.pass_context
def list_papers(ctx: click.Context, status: str, tag: str | None) -> None:
    """List all processed papers."""
    from paper_assistant.config import load_config
    from paper_assistant.models import ProcessingStatus
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    status_filter = None if status == "all" else ProcessingStatus(status)
    papers = storage.list_papers(status=status_filter, tag=tag)

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
@click.pass_context
def show(ctx: click.Context, paper_id: str) -> None:
    """Display the summary for a specific paper."""
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)
    paper = storage.get_paper(paper_id)

    if not paper:
        console.print(f"[red]Paper {paper_id} not found.[/red]")
        return

    if not paper.summary_path:
        console.print(f"[yellow]Paper {paper_id} has no summary yet.[/yellow]")
        return

    content = (config.data_dir / paper.summary_path).read_text(encoding="utf-8")
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


@main.command("notion-sync")
@click.option("--paper", "paper_id", help="Sync only one paper by ID or Notion page ID.")
@click.option("--dry-run", is_flag=True, help="Preview sync actions without writing changes.")
@click.pass_context
def notion_sync(ctx: click.Context, paper_id: str | None, dry_run: bool) -> None:
    """Run manual two-way sync between local storage and Notion."""
    asyncio.run(_notion_sync(ctx.obj, paper_id, dry_run))


async def _notion_sync(obj: dict, paper_id: str | None, dry_run: bool) -> None:
    from paper_assistant.config import load_config
    from paper_assistant.notion import sync_notion
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
        console.print(f"[red]Notion sync failed:[/red] {e}")
        return

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
