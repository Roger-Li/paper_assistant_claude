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
    """Add and summarize a paper from an arXiv URL.

    Example: paper-assist add https://arxiv.org/abs/2503.10291
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
    from paper_assistant.arxiv import download_pdf, fetch_metadata, parse_arxiv_url
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.podcast import generate_feed
    from paper_assistant.storage import StorageManager, make_pdf_filename
    from paper_assistant.summarizer import (
        format_summary_file,
        summarize_paper_pdf,
        summarize_paper_text,
    )
    from paper_assistant.tts import prepare_text_for_tts, text_to_speech
    from paper_assistant.storage import make_audio_filename

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
        metadata = await fetch_metadata(arxiv_id)
    except Exception as e:
        console.print(f"[red]Error fetching metadata:[/red] {e}")
        return

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
    pdf_path = config.pdfs_dir / make_pdf_filename(arxiv_id)
    try:
        await download_pdf(arxiv_id, pdf_path)
        paper.pdf_path = f"pdfs/{make_pdf_filename(arxiv_id)}"
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
        summary_path = storage.save_summary(arxiv_id, summary_content)
        paper = storage.get_paper(arxiv_id)  # Re-fetch with updated summary_path
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
    if not skip_audio:
        console.print("[bold]Step 4/5:[/bold] Generating audio...")
        try:
            tts_text = prepare_text_for_tts(
                result.full_markdown, metadata.title, metadata.authors
            )
            audio_path = config.audio_dir / make_audio_filename(arxiv_id)
            await text_to_speech(tts_text, audio_path, config.tts_voice, config.tts_rate)
            paper.audio_path = f"audio/{make_audio_filename(arxiv_id)}"
            paper.status = ProcessingStatus.AUDIO_GENERATED
            storage.add_paper(paper)
        except Exception as e:
            console.print(f"[yellow]Warning: TTS failed:[/yellow] {e}")
            console.print("  Summary was saved. Audio can be regenerated later.")
    else:
        console.print("[bold]Step 4/5:[/bold] Skipping audio (--skip-audio).")

    # Step 5: Update RSS feed
    console.print("[bold]Step 5/5:[/bold] Updating podcast feed...")
    try:
        all_papers = storage.list_papers()
        generate_feed(config, all_papers)
        paper.status = ProcessingStatus.COMPLETE
        storage.add_paper(paper)
    except Exception as e:
        console.print(f"[yellow]Warning: Feed generation failed:[/yellow] {e}")

    # Copy audio to iCloud Drive for iPhone access
    if paper.audio_path and config.icloud_sync:
        import shutil

        try:
            config.icloud_dir.mkdir(parents=True, exist_ok=True)
            safe_title = metadata.title[:60].replace("/", "-").replace(":", " -")
            icloud_dest = config.icloud_dir / f"{safe_title} [{arxiv_id}].mp3"
            shutil.copy2(config.data_dir / paper.audio_path, icloud_dest)
            console.print(f"  iCloud:  Synced to {icloud_dest.name}")
        except Exception as e:
            console.print(f"[yellow]Warning: iCloud copy failed:[/yellow] {e}")

    console.print()
    console.print(f"[green]Done![/green] Paper processed successfully.")
    console.print(f"  Summary: {summary_path}")
    if paper.audio_path:
        console.print(f"  Audio:   {config.data_dir / paper.audio_path}")


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

    Example: paper-assist import https://arxiv.org/abs/2503.10291
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
    from paper_assistant.arxiv import fetch_metadata, parse_arxiv_url
    from paper_assistant.config import load_config
    from paper_assistant.models import Paper, ProcessingStatus
    from paper_assistant.podcast import generate_feed
    from paper_assistant.storage import StorageManager, make_audio_filename
    from paper_assistant.summarizer import (
        SummarizationResult,
        find_one_pager,
        format_summary_file,
        parse_summary_sections,
    )
    from paper_assistant.tts import prepare_text_for_tts, text_to_speech

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
        metadata = await fetch_metadata(arxiv_id)
    except Exception as e:
        console.print(f"[red]Error fetching metadata:[/red] {e}")
        return

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
    summary_path = storage.save_summary(arxiv_id, summary_content)
    paper = storage.get_paper(arxiv_id)  # Re-fetch with updated summary_path

    # Step 3: Generate audio
    if not skip_audio:
        console.print("[bold]Step 3/4:[/bold] Generating audio...")
        try:
            tts_text = prepare_text_for_tts(
                markdown, metadata.title, metadata.authors
            )
            audio_path = config.audio_dir / make_audio_filename(arxiv_id)
            await text_to_speech(tts_text, audio_path, config.tts_voice, config.tts_rate)
            paper.audio_path = f"audio/{make_audio_filename(arxiv_id)}"
            paper.status = ProcessingStatus.AUDIO_GENERATED
            storage.add_paper(paper)
        except Exception as e:
            console.print(f"[yellow]Warning: TTS failed:[/yellow] {e}")
            console.print("  Summary was saved. Audio can be regenerated later.")
    else:
        console.print("[bold]Step 3/4:[/bold] Skipping audio (--skip-audio).")

    # Step 4: Update RSS feed
    console.print("[bold]Step 4/4:[/bold] Updating podcast feed...")
    try:
        all_papers = storage.list_papers()
        generate_feed(config, all_papers)
        paper.status = ProcessingStatus.COMPLETE
        storage.add_paper(paper)
    except Exception as e:
        console.print(f"[yellow]Warning: Feed generation failed:[/yellow] {e}")

    # Copy audio to iCloud Drive
    if paper.audio_path and config.icloud_sync:
        import shutil

        try:
            config.icloud_dir.mkdir(parents=True, exist_ok=True)
            safe_title = metadata.title[:60].replace("/", "-").replace(":", " -")
            icloud_dest = config.icloud_dir / f"{safe_title} [{arxiv_id}].mp3"
            shutil.copy2(config.data_dir / paper.audio_path, icloud_dest)
            console.print(f"  iCloud:  Synced to {icloud_dest.name}")
        except Exception as e:
            console.print(f"[yellow]Warning: iCloud copy failed:[/yellow] {e}")

    console.print()
    console.print(f"[green]Done![/green] Paper imported successfully.")
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
    table.add_column("arXiv ID", style="cyan", no_wrap=True)
    table.add_column("Title", max_width=50)
    table.add_column("Added", no_wrap=True)
    table.add_column("Status", style="green")
    table.add_column("Audio", justify="center")
    table.add_column("Tags")

    for p in papers:
        table.add_row(
            p.metadata.arxiv_id,
            p.metadata.title[:50] + ("..." if len(p.metadata.title) > 50 else ""),
            p.date_added.strftime("%Y-%m-%d"),
            p.status.value,
            "Y" if p.audio_path else "-",
            ", ".join(p.tags) if p.tags else "",
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(papers)} papers[/dim]")


@main.command()
@click.argument("arxiv_id")
@click.pass_context
def show(ctx: click.Context, arxiv_id: str) -> None:
    """Display the summary for a specific paper."""
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)
    paper = storage.get_paper(arxiv_id)

    if not paper:
        console.print(f"[red]Paper {arxiv_id} not found.[/red]")
        return

    if not paper.summary_path:
        console.print(f"[yellow]Paper {arxiv_id} has no summary yet.[/yellow]")
        return

    content = (config.data_dir / paper.summary_path).read_text(encoding="utf-8")
    console.print(Markdown(content))


@main.command()
@click.argument("arxiv_id")
@click.option("--keep-files", is_flag=True, help="Keep generated files, only remove from index.")
@click.confirmation_option(prompt="Are you sure you want to remove this paper?")
@click.pass_context
def remove(ctx: click.Context, arxiv_id: str, keep_files: bool) -> None:
    """Remove a paper from the index."""
    from paper_assistant.config import load_config
    from paper_assistant.storage import StorageManager

    config = load_config(**ctx.obj)
    storage = StorageManager(config)

    if storage.delete_paper(arxiv_id, delete_files=not keep_files):
        console.print(f"[green]Paper {arxiv_id} removed.[/green]")
    else:
        console.print(f"[red]Paper {arxiv_id} not found.[/red]")


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
