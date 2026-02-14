"""Web UI routes for Paper Assistant."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from paper_assistant.config import Config
from paper_assistant.storage import StorageManager


class ImportRequest(BaseModel):
    url: str
    markdown: str
    tags: list[str] = []
    skip_audio: bool = False


class TagsRequest(BaseModel):
    tags: list[str]


class UpdateSummaryRequest(BaseModel):
    markdown: str
    regenerate_audio: bool = True


class ReadingStatusRequest(BaseModel):
    reading_status: str


class NotionSyncRequest(BaseModel):
    paper_id: str | None = None
    dry_run: bool = False


def create_router(config: Config, templates: Jinja2Templates) -> APIRouter:
    """Create the router with all web UI endpoints."""
    router = APIRouter()
    storage = StorageManager(config)

    @router.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        tag: str | None = None,
        status: str | None = None,
        reading_status: str | None = None,
        sort: str = "date_added",
        order: str = "desc",
    ):
        """Dashboard: list all papers with optional filters and sorting."""
        from paper_assistant.models import ProcessingStatus, ReadingStatus

        valid_sorts = {"date_added", "title", "tag", "arxiv_id"}
        if sort not in valid_sorts:
            sort = "date_added"
        reverse = order != "asc"

        # Convert string params to enums (ignore invalid values)
        status_enum = None
        if status:
            try:
                status_enum = ProcessingStatus(status)
            except ValueError:
                pass

        reading_status_enum = None
        if reading_status:
            try:
                reading_status_enum = ReadingStatus(reading_status)
            except ValueError:
                pass

        papers = storage.list_papers(
            tag=tag, status=status_enum, reading_status=reading_status_enum,
            sort_by=sort, reverse=reverse,
        )
        all_tags = sorted(
            {t for p in storage.list_papers() for t in p.tags}
        )
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "papers": papers,
                "all_tags": all_tags,
                "active_tag": tag,
                "total": len(papers),
                "active_sort": sort,
                "active_order": order,
                "active_status": status,
                "active_reading_status": reading_status,
                "all_statuses": [s.value for s in ProcessingStatus],
                "all_reading_statuses": [rs.value for rs in ReadingStatus],
            },
        )

    @router.get("/paper/{arxiv_id}", response_class=HTMLResponse)
    async def paper_detail(request: Request, arxiv_id: str):
        """Single paper view with rendered summary."""
        paper = storage.get_paper(arxiv_id)
        if paper is None:
            return templates.TemplateResponse(
                "paper.html",
                {"request": request, "paper": None, "summary": "", "arxiv_id": arxiv_id},
            )

        summary = ""
        if paper.summary_path:
            summary_path = config.data_dir / paper.summary_path
            if summary_path.exists():
                summary = summary_path.read_text(encoding="utf-8")

        return templates.TemplateResponse(
            "paper.html",
            {
                "request": request,
                "paper": paper,
                "summary": summary,
                "arxiv_id": arxiv_id,
            },
        )

    @router.post("/api/add")
    async def api_add_paper(url: str, skip_audio: bool = False, tags: list[str] | None = None):
        """API endpoint to add a paper (triggers the full pipeline)."""
        from paper_assistant.arxiv import download_pdf, fetch_metadata, parse_arxiv_url
        from paper_assistant.models import Paper, ProcessingStatus
        from paper_assistant.pdf import extract_text_from_pdf
        from paper_assistant.podcast import generate_feed
        from paper_assistant.storage import make_audio_filename, make_pdf_filename
        from paper_assistant.summarizer import (
            format_summary_file,
            summarize_paper_text,
        )
        from paper_assistant.tts import prepare_text_for_tts, text_to_speech

        try:
            arxiv_id = parse_arxiv_url(url)
        except ValueError as e:
            return {"error": str(e)}

        if storage.paper_exists(arxiv_id):
            return {"error": f"Paper {arxiv_id} already exists", "arxiv_id": arxiv_id}

        # Run pipeline
        try:
            metadata = await fetch_metadata(arxiv_id, config=config)

            paper = Paper(metadata=metadata, status=ProcessingStatus.PENDING, tags=tags or [])
            storage.add_paper(paper)

            pdf_path = config.pdfs_dir / make_pdf_filename(arxiv_id)
            await download_pdf(arxiv_id, pdf_path, config=config)
            paper.pdf_path = f"pdfs/{make_pdf_filename(arxiv_id)}"
            paper.status = ProcessingStatus.FETCHED
            storage.add_paper(paper)

            paper_text = extract_text_from_pdf(pdf_path)
            result = await summarize_paper_text(config, metadata, paper_text)
            summary_content = format_summary_file(metadata, result)
            storage.save_summary(arxiv_id, summary_content)
            paper = storage.get_paper(arxiv_id)  # Re-fetch with updated summary_path

            if not skip_audio:
                tts_text = prepare_text_for_tts(
                    result.full_markdown, metadata.title, metadata.authors
                )
                audio_path = config.audio_dir / make_audio_filename(arxiv_id)
                await text_to_speech(
                    tts_text, audio_path, config.tts_voice, config.tts_rate
                )
                paper.audio_path = f"audio/{make_audio_filename(arxiv_id)}"

            paper.status = ProcessingStatus.COMPLETE
            paper.model_used = result.model_used
            paper.token_count = result.input_tokens + result.output_tokens
            storage.add_paper(paper)

            all_papers = storage.list_papers()
            generate_feed(config, all_papers)

            return {
                "status": "ok",
                "arxiv_id": arxiv_id,
                "title": metadata.title,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/import")
    async def api_import_paper(req: ImportRequest):
        """API endpoint to import a pre-generated summary."""
        from paper_assistant.arxiv import fetch_metadata, parse_arxiv_url
        from paper_assistant.models import Paper, ProcessingStatus
        from paper_assistant.podcast import generate_feed
        from paper_assistant.storage import make_audio_filename
        from paper_assistant.summarizer import (
            SummarizationResult,
            find_one_pager,
            format_summary_file,
            parse_summary_sections,
        )
        from paper_assistant.tts import prepare_text_for_tts, text_to_speech

        try:
            arxiv_id = parse_arxiv_url(req.url)
        except ValueError as e:
            return {"error": str(e)}

        if storage.paper_exists(arxiv_id):
            return {"error": f"Paper {arxiv_id} already exists", "arxiv_id": arxiv_id}

        try:
            metadata = await fetch_metadata(arxiv_id, config=config)

            sections = parse_summary_sections(req.markdown)
            one_pager = find_one_pager(sections)

            result = SummarizationResult(
                full_markdown=req.markdown,
                one_pager=one_pager,
                sections=sections,
                model_used="manual",
            )

            paper = Paper(
                metadata=metadata,
                status=ProcessingStatus.PENDING,
                model_used="manual",
                tags=req.tags,
            )
            storage.add_paper(paper)

            summary_content = format_summary_file(metadata, result)
            storage.save_summary(arxiv_id, summary_content)
            paper = storage.get_paper(arxiv_id)  # Re-fetch with updated summary_path

            if not req.skip_audio:
                tts_text = prepare_text_for_tts(
                    req.markdown, metadata.title, metadata.authors
                )
                audio_path = config.audio_dir / make_audio_filename(arxiv_id)
                await text_to_speech(
                    tts_text, audio_path, config.tts_voice, config.tts_rate
                )
                paper.audio_path = f"audio/{make_audio_filename(arxiv_id)}"

            paper.status = ProcessingStatus.COMPLETE
            storage.add_paper(paper)

            all_papers = storage.list_papers()
            generate_feed(config, all_papers)

            return {
                "status": "ok",
                "arxiv_id": arxiv_id,
                "title": metadata.title,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/paper/{arxiv_id}/tags")
    async def api_add_tags(arxiv_id: str, req: TagsRequest):
        """Add tags to a paper."""
        try:
            tags = storage.add_tags(arxiv_id, req.tags)
            return {"status": "ok", "tags": tags}
        except KeyError:
            return {"error": f"Paper {arxiv_id} not found"}

    @router.delete("/api/paper/{arxiv_id}/tags/{tag}")
    async def api_remove_tag(arxiv_id: str, tag: str):
        """Remove a tag from a paper."""
        try:
            tags = storage.remove_tag(arxiv_id, tag)
            return {"status": "ok", "tags": tags}
        except KeyError:
            return {"error": f"Paper {arxiv_id} not found"}

    @router.delete("/api/paper/{arxiv_id}")
    async def api_delete_paper(arxiv_id: str):
        """Delete a paper and its files."""
        if storage.delete_paper(arxiv_id, delete_files=True):
            from paper_assistant.podcast import generate_feed
            all_papers = storage.list_papers()
            generate_feed(config, all_papers)
            return {"status": "ok"}
        return {"error": f"Paper {arxiv_id} not found"}

    @router.put("/api/paper/{arxiv_id}/reading-status")
    async def api_set_reading_status(arxiv_id: str, req: ReadingStatusRequest):
        """Set the reading status of a paper."""
        from paper_assistant.models import ReadingStatus

        try:
            rs = ReadingStatus(req.reading_status)
        except ValueError:
            return {"error": f"Invalid reading status: {req.reading_status}"}
        try:
            result = storage.set_reading_status(arxiv_id, rs)
            return {"status": "ok", "reading_status": result.value}
        except KeyError:
            return {"error": f"Paper {arxiv_id} not found"}

    @router.get("/api/papers")
    async def api_list_papers(
        tag: str | None = None,
        status: str | None = None,
        reading_status: str | None = None,
        sort: str = "date_added",
        order: str = "desc",
    ):
        """JSON API: list all papers."""
        from paper_assistant.models import ProcessingStatus, ReadingStatus

        valid_sorts = {"date_added", "title", "tag", "arxiv_id"}
        if sort not in valid_sorts:
            sort = "date_added"
        reverse = order != "asc"

        status_enum = None
        if status:
            try:
                status_enum = ProcessingStatus(status)
            except ValueError:
                pass

        reading_status_enum = None
        if reading_status:
            try:
                reading_status_enum = ReadingStatus(reading_status)
            except ValueError:
                pass

        papers = storage.list_papers(
            tag=tag, status=status_enum, reading_status=reading_status_enum,
            sort_by=sort, reverse=reverse,
        )
        return [
            {
                "arxiv_id": p.metadata.arxiv_id,
                "title": p.metadata.title,
                "authors": p.metadata.authors,
                "date_added": p.date_added.isoformat(),
                "status": p.status.value,
                "reading_status": p.reading_status.value,
                "archived": p.archived_at is not None,
                "notion_page_id": p.notion_page_id,
                "has_audio": p.audio_path is not None,
                "tags": p.tags,
            }
            for p in papers
        ]

    @router.get("/api/notion/sync/preview")
    async def api_notion_sync_preview(paper: str | None = None):
        """Preview Notion sync actions without writing changes."""
        from paper_assistant.notion import sync_notion

        try:
            report = await sync_notion(
                config=config,
                storage=storage,
                paper_id=paper,
                dry_run=True,
            )
            return {"status": "ok", "report": report.to_dict()}
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/notion/sync")
    async def api_notion_sync(req: NotionSyncRequest | None = None):
        """Run Notion sync manually."""
        from paper_assistant.notion import sync_notion

        payload = req or NotionSyncRequest()
        try:
            report = await sync_notion(
                config=config,
                storage=storage,
                paper_id=payload.paper_id,
                dry_run=payload.dry_run,
            )
            return {"status": "ok", "report": report.to_dict()}
        except Exception as e:
            return {"error": str(e)}

    @router.get("/api/paper/{arxiv_id}/summary")
    async def api_get_summary(arxiv_id: str):
        """Get the raw markdown summary body for editing."""
        paper = storage.get_paper(arxiv_id)
        if paper is None:
            return {"error": f"Paper {arxiv_id} not found"}

        if not paper.summary_path:
            return {"error": "No summary available", "markdown": ""}

        summary_path = config.data_dir / paper.summary_path
        if not summary_path.exists():
            return {"error": "Summary file missing", "markdown": ""}

        raw = summary_path.read_text(encoding="utf-8")

        # Strip YAML front matter and title/authors header block
        # (format_summary_file regenerates these on save)
        body = raw
        if body.startswith("---"):
            end_idx = body.find("---", 3)
            if end_idx != -1:
                body = body[end_idx + 3 :].lstrip()

        hr_idx = body.find("\n---\n")
        if hr_idx != -1 and hr_idx < 400:
            body = body[hr_idx + 5 :].lstrip()

        return {"markdown": body}

    @router.put("/api/paper/{arxiv_id}/summary")
    async def api_update_summary(arxiv_id: str, req: UpdateSummaryRequest):
        """Update a paper's summary and optionally regenerate audio."""
        from paper_assistant.models import ProcessingStatus
        from paper_assistant.podcast import generate_feed
        from paper_assistant.storage import make_audio_filename
        from paper_assistant.summarizer import (
            SummarizationResult,
            find_one_pager,
            format_summary_file,
            parse_summary_sections,
        )
        from paper_assistant.tts import prepare_text_for_tts, text_to_speech

        paper = storage.get_paper(arxiv_id)
        if paper is None:
            return {"error": f"Paper {arxiv_id} not found"}

        if not req.markdown.strip():
            return {"error": "Summary cannot be empty"}

        # Save the updated summary
        try:
            sections = parse_summary_sections(req.markdown)
            one_pager = find_one_pager(sections)
            result = SummarizationResult(
                full_markdown=req.markdown,
                one_pager=one_pager,
                sections=sections,
                model_used=paper.model_used or "manual-edit",
            )
            summary_content = format_summary_file(paper.metadata, result)
            storage.save_summary(arxiv_id, summary_content)
            paper = storage.get_paper(arxiv_id)  # Re-fetch after save_summary
        except Exception as e:
            return {"error": f"Failed to save summary: {e}"}

        # Optionally regenerate audio (graceful degradation)
        audio_warning = None
        if req.regenerate_audio:
            try:
                tts_text = prepare_text_for_tts(
                    req.markdown, paper.metadata.title, paper.metadata.authors
                )
                audio_path = config.audio_dir / make_audio_filename(arxiv_id)
                await text_to_speech(
                    tts_text, audio_path, config.tts_voice, config.tts_rate
                )
                paper.audio_path = f"audio/{make_audio_filename(arxiv_id)}"
                paper.status = ProcessingStatus.COMPLETE
                storage.add_paper(paper)
            except Exception as e:
                audio_warning = f"Summary saved but audio regeneration failed: {e}"
        elif paper.audio_path:
            # Preserve COMPLETE status when skipping audio regen for papers that had audio
            paper.status = ProcessingStatus.COMPLETE
            storage.add_paper(paper)

        # Regenerate feed (non-critical)
        try:
            all_papers = storage.list_papers()
            generate_feed(config, all_papers)
        except Exception:
            pass

        response = {"status": "ok", "arxiv_id": arxiv_id, "title": paper.metadata.title}
        if audio_warning:
            response["warning"] = audio_warning
        return response

    @router.get("/feed.xml")
    async def rss_feed():
        """Serve the RSS podcast feed."""
        if config.feed_path.exists():
            return Response(
                content=config.feed_path.read_text(encoding="utf-8"),
                media_type="application/rss+xml",
            )
        # Generate on-the-fly if no file exists
        from paper_assistant.podcast import generate_feed

        papers = storage.list_papers()
        xml = generate_feed(config, papers)
        return Response(content=xml, media_type="application/rss+xml")

    return router
