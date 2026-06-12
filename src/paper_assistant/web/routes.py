"""Web UI routes for Paper Assistant."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from paper_assistant.config import Config
from paper_assistant.storage import StorageManager

logger = logging.getLogger(__name__)


class ImportRequest(BaseModel):
    url: str
    markdown: str
    tags: list[str] = Field(default_factory=list)
    skip_audio: bool = False
    skip_transcript: bool = False
    script_markdown: str | None = None
    skip_script_generation: bool = False


class CreateRequest(BaseModel):
    title: str
    markdown: str
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    skip_audio: bool = False
    skip_transcript: bool = False


class TranscriptRegenerateRequest(BaseModel):
    model: str | None = None
    script_markdown: str | None = None


class TagsRequest(BaseModel):
    tags: list[str]


class TagRenameRequest(BaseModel):
    from_tag: str
    to_tag: str


class BulkTagRenameRequest(BaseModel):
    renames: list[TagRenameRequest]


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
    from paper_assistant.search import get_search_manager

    router = APIRouter()
    storage = StorageManager(config)
    search_mgr = get_search_manager(config)

    def list_all_tags() -> list[str]:
        return sorted({tag for paper in storage.list_papers() for tag in paper.tags})

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
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "papers": papers,
                "all_tags": list_all_tags(),
                "active_tag": tag,
                "total": len(papers),
                "active_sort": sort,
                "active_order": order,
                "active_status": status,
                "active_reading_status": reading_status,
                "all_statuses": [s.value for s in ProcessingStatus],
                "all_reading_statuses": [rs.value for rs in ReadingStatus],
                "search_available": search_mgr is not None,
            },
        )

    @router.get("/paper/{paper_id:path}", response_class=HTMLResponse)
    async def paper_detail(request: Request, paper_id: str):
        """Single paper view with rendered summary."""
        paper = storage.get_paper(paper_id)
        if paper is None:
            return templates.TemplateResponse(
                request,
                "paper.html",
                {"paper": None, "summary": "", "paper_id": paper_id},
            )

        summary = ""
        if paper.summary_path:
            summary_path = config.data_dir / paper.summary_path
            if summary_path.exists():
                summary = summary_path.read_text(encoding="utf-8")

        return templates.TemplateResponse(
            request,
            "paper.html",
            {
                "paper": paper,
                "summary": summary,
                "paper_id": paper_id,
            },
        )

    @router.post("/api/add")
    async def api_add_paper(
        url: str,
        skip_audio: bool = False,
        skip_transcript: bool = False,
        tags: list[str] | None = None,
    ):
        """API endpoint to add a paper or web article (triggers the full pipeline)."""
        if not config.anthropic_api_key:
            return {"error": "ANTHROPIC_API_KEY is required for summarization."}

        from paper_assistant.audio_assets import render_audio_assets
        from paper_assistant.models import Paper, ProcessingStatus
        from paper_assistant.podcast import generate_feed
        from paper_assistant.web_article import is_arxiv_url

        if is_arxiv_url(url):
            return await _api_add_arxiv(url, skip_audio, skip_transcript, tags)

        # Web article path
        from paper_assistant.summarizer import format_summary_file, summarize_article_text
        from paper_assistant.web_article import fetch_article

        try:
            metadata, body_text = await fetch_article(url)
            paper_id = metadata.paper_id

            if storage.paper_exists(paper_id):
                return {"error": f"Article {paper_id} already exists", "paper_id": paper_id}

            paper = Paper(metadata=metadata, status=ProcessingStatus.PENDING, tags=tags or [])
            storage.add_paper(paper)

            result = await summarize_article_text(config, metadata, body_text)
            summary_content = format_summary_file(metadata, result)
            storage.save_summary(paper_id, summary_content)
            paper = storage.get_paper(paper_id)
            paper.model_used = result.model_used
            paper.token_count = result.input_tokens + result.output_tokens
            storage.add_paper(paper)

            audio_result = await render_audio_assets(
                config=config,
                storage=storage,
                paper=paper,
                source_markdown=result.full_markdown,
                skip_transcript=skip_transcript,
                skip_audio=skip_audio,
            )
            paper = storage.get_paper(paper_id) or paper
            paper.status = ProcessingStatus.COMPLETE
            storage.add_paper(paper)

            all_papers = storage.list_papers()
            generate_feed(config, all_papers)

            if search_mgr:
                try:
                    search_mgr.sync_paper(paper_id, storage)
                except Exception:
                    logger.warning("Search index update failed for %s", paper_id)

            response = {
                "status": "ok",
                "paper_id": paper_id,
                "title": metadata.title,
                "transcript_path": (
                    f"transcripts/{paper_id}.md"
                    if paper.transcript_path
                    else None
                ),
                "audio_path": paper.audio_path,
                "backend_used": audio_result.backend_used,
            }
            if audio_result.warnings:
                response["warnings"] = audio_result.warnings
            return response
        except Exception as e:
            return {"error": str(e)}

    async def _api_add_arxiv(
        url: str,
        skip_audio: bool,
        skip_transcript: bool,
        tags: list[str] | None,
    ):
        """Internal: add an arXiv paper via the full pipeline."""
        from paper_assistant.arxiv import (
            download_pdf,
            fetch_metadata as fetch_arxiv_metadata,
            parse_arxiv_url,
        )
        from paper_assistant.audio_assets import render_audio_assets
        from paper_assistant.hf_papers import (
            fetch_markdown_body as fetch_hf_markdown_body,
            fetch_metadata as fetch_hf_metadata,
        )
        from paper_assistant.models import Paper, ProcessingStatus
        from paper_assistant.pdf import extract_text_from_pdf
        from paper_assistant.podcast import generate_feed
        from paper_assistant.storage import make_pdf_filename
        from paper_assistant.summarizer import format_summary_file, summarize_paper_text

        try:
            arxiv_id = parse_arxiv_url(url)
        except ValueError as e:
            return {"error": str(e)}

        if storage.paper_exists(arxiv_id):
            return {"error": f"Paper {arxiv_id} already exists", "paper_id": arxiv_id}

        try:
            try:
                metadata = await fetch_hf_metadata(arxiv_id, config=config)
            except Exception as exc:
                logger.warning("HF metadata unavailable for %s in WebUI add flow: %s", arxiv_id, exc)
                metadata = await fetch_arxiv_metadata(arxiv_id, config=config)

            paper = Paper(metadata=metadata, status=ProcessingStatus.PENDING, tags=tags or [])
            storage.add_paper(paper)

            try:
                paper_text = await fetch_hf_markdown_body(arxiv_id, config=config)
            except Exception as exc:
                logger.warning("HF markdown unavailable for %s in WebUI add flow: %s", arxiv_id, exc)
                pdf_path = config.pdfs_dir / make_pdf_filename(arxiv_id)
                await download_pdf(arxiv_id, pdf_path, config=config)
                paper.pdf_path = f"pdfs/{make_pdf_filename(arxiv_id)}"
                paper_text = extract_text_from_pdf(pdf_path)

            paper.status = ProcessingStatus.FETCHED
            storage.add_paper(paper)

            result = await summarize_paper_text(config, metadata, paper_text)

            from paper_assistant.visuals import enrich_summary_with_visuals

            result.full_markdown = enrich_summary_with_visuals(
                full_markdown=result.full_markdown,
                source_markdown=paper_text,
            )

            summary_content = format_summary_file(metadata, result)
            storage.save_summary(arxiv_id, summary_content)
            paper = storage.get_paper(arxiv_id)
            paper.model_used = result.model_used
            paper.token_count = result.input_tokens + result.output_tokens
            storage.add_paper(paper)

            audio_result = await render_audio_assets(
                config=config,
                storage=storage,
                paper=paper,
                source_markdown=result.full_markdown,
                skip_transcript=skip_transcript,
                skip_audio=skip_audio,
            )
            paper = storage.get_paper(arxiv_id) or paper
            paper.status = ProcessingStatus.COMPLETE
            storage.add_paper(paper)

            all_papers = storage.list_papers()
            generate_feed(config, all_papers)

            if search_mgr:
                try:
                    search_mgr.sync_paper(arxiv_id, storage)
                except Exception:
                    logger.warning("Search index update failed for %s", arxiv_id)

            response = {
                "status": "ok",
                "paper_id": arxiv_id,
                "title": metadata.title,
                "transcript_path": (
                    f"transcripts/{arxiv_id}.md"
                    if paper.transcript_path
                    else None
                ),
                "audio_path": paper.audio_path,
                "backend_used": audio_result.backend_used,
            }
            if audio_result.warnings:
                response["warnings"] = audio_result.warnings
            return response
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/import")
    async def api_import_paper(req: ImportRequest):
        """API endpoint to import a pre-generated summary."""
        from paper_assistant.pipeline import DuplicatePaperError, import_paper_summary

        try:
            result = await import_paper_summary(
                config=config,
                storage=storage,
                url=req.url,
                markdown=req.markdown,
                model="manual",
                tags=req.tags,
                skip_audio=req.skip_audio,
                skip_transcript=req.skip_transcript,
                provided_script_markdown=req.script_markdown,
                skip_script_generation=req.skip_script_generation,
            )
        except DuplicatePaperError as e:
            return {"error": str(e), "paper_id": e.paper_id}
        except Exception as e:
            return {"error": str(e)}

        response = {
            "status": "ok",
            "paper_id": result.paper_id,
            "title": result.title,
            "transcript_path": (
                str(result.transcript_path) if result.transcript_path else None
            ),
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "backend_used": result.backend_used,
        }
        if result.warnings:
            response["warnings"] = result.warnings
        return response

    @router.post("/api/create")
    async def api_create_note(req: CreateRequest):
        """API endpoint to create a local markdown-backed note entry."""
        from paper_assistant.pipeline import create_local_entry

        try:
            outcome = await create_local_entry(
                config=config,
                storage=storage,
                title=req.title,
                markdown=req.markdown,
                source_url=req.source_url,
                tags=req.tags,
                skip_audio=req.skip_audio,
                skip_transcript=req.skip_transcript,
            )
        except Exception as e:
            return {"error": str(e)}

        response = {
            "status": "ok",
            "paper_id": outcome.paper.metadata.paper_id,
            "title": outcome.paper.metadata.title,
            "transcript_path": outcome.paper.transcript_path,
            "audio_path": outcome.paper.audio_path,
        }
        if outcome.warnings:
            response["warnings"] = outcome.warnings
        return response

    @router.post("/api/paper/{paper_id:path}/tags")
    async def api_add_tags(paper_id: str, req: TagsRequest):
        """Add tags to a paper."""
        try:
            tags = storage.add_tags(paper_id, req.tags)
            if search_mgr:
                try:
                    search_mgr.sync_paper(paper_id, storage)
                except Exception:
                    logger.warning("Search index update failed for %s", paper_id)
            return {"status": "ok", "tags": tags}
        except KeyError:
            return {"error": f"Paper {paper_id} not found"}

    @router.delete("/api/paper/{paper_id:path}/tags/{tag}")
    async def api_remove_tag(paper_id: str, tag: str):
        """Remove a tag from a paper."""
        try:
            tags = storage.remove_tag(paper_id, tag)
            if search_mgr:
                try:
                    search_mgr.sync_paper(paper_id, storage)
                except Exception:
                    logger.warning("Search index update failed for %s", paper_id)
            return {"status": "ok", "tags": tags}
        except KeyError:
            return {"error": f"Paper {paper_id} not found"}

    @router.put("/api/tags/rename")
    async def api_rename_tags(req: BulkTagRenameRequest):
        """Rename tags across all local papers."""
        report = storage.rename_tags(
            [(rename.from_tag, rename.to_tag) for rename in req.renames]
        )
        if not report["renames"]:
            return {"error": "No valid tag rename operations provided"}

        if search_mgr and report.get("changed_paper_ids"):
            try:
                search_mgr.batch_sync(report["changed_paper_ids"], storage)
            except Exception:
                logger.warning("Search index batch update failed after tag rename")

        return {
            "status": "ok",
            **report,
            "all_tags": list_all_tags(),
        }

    @router.delete("/api/paper/{paper_id:path}")
    async def api_delete_paper(paper_id: str):
        """Delete a paper and its files."""
        if storage.delete_paper(paper_id, delete_files=True):
            from paper_assistant.podcast import generate_feed
            all_papers = storage.list_papers()
            generate_feed(config, all_papers)
            if search_mgr:
                try:
                    search_mgr.delete_paper(paper_id)
                except Exception:
                    logger.warning("Search index delete failed for %s", paper_id)
            return {"status": "ok"}
        return {"error": f"Paper {paper_id} not found"}

    @router.put("/api/paper/{paper_id:path}/reading-status")
    async def api_set_reading_status(paper_id: str, req: ReadingStatusRequest):
        """Set the reading status of a paper."""
        from paper_assistant.models import ReadingStatus

        try:
            rs = ReadingStatus(req.reading_status)
        except ValueError:
            return {"error": f"Invalid reading status: {req.reading_status}"}
        try:
            result = storage.set_reading_status(paper_id, rs)
            if search_mgr:
                try:
                    search_mgr.sync_paper(paper_id, storage)
                except Exception:
                    logger.warning("Search index update failed for %s", paper_id)
            return {"status": "ok", "reading_status": result.value}
        except KeyError:
            return {"error": f"Paper {paper_id} not found"}

    @router.get("/api/search")
    async def api_search(q: str = "", limit: int = 10, mode: str = "hybrid"):
        """Search papers via qmd index."""
        from starlette.responses import JSONResponse

        from paper_assistant.search import EmbeddingsNotAvailableError

        if not search_mgr:
            return JSONResponse(
                {"error": "Search is not configured. Install qmd and set PAPER_ASSIST_QMD_ENABLED=true."},
                status_code=503,
            )
        if not q.strip():
            return {"results": []}

        try:
            results = search_mgr.search(q, limit=limit, mode=mode)
        except EmbeddingsNotAvailableError:
            logger.info("Hybrid/vector search unavailable (no embeddings), falling back to text")
            try:
                results = search_mgr.search(q, limit=limit, mode="text")
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)
        except RuntimeError as e:
            err_msg = str(e)
            if "not found" in err_msg.lower() or "collection" in err_msg.lower():
                return JSONResponse(
                    {"error": "Search index not initialized. Run `paper-assist index-setup` first."},
                    status_code=503,
                )
            return JSONResponse({"error": err_msg}, status_code=500)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

        return {
            "results": [
                {
                    "paper_id": r.paper_id,
                    "title": r.title,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in results
            ],
        }

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
                "paper_id": p.metadata.paper_id,
                "arxiv_id": p.metadata.arxiv_id,
                "source_type": p.metadata.source_type.value,
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
        from paper_assistant.notion import describe_exception, sync_notion

        try:
            report = await sync_notion(
                config=config,
                storage=storage,
                paper_id=paper,
                dry_run=True,
            )
            return {"status": "ok", "report": report.to_dict()}
        except Exception as e:
            return {"error": describe_exception(e)}

    @router.post("/api/notion/sync")
    async def api_notion_sync(req: NotionSyncRequest | None = None):
        """Run Notion sync manually."""
        from paper_assistant.notion import describe_exception, sync_notion

        payload = req or NotionSyncRequest()
        try:
            report = await sync_notion(
                config=config,
                storage=storage,
                paper_id=payload.paper_id,
                dry_run=payload.dry_run,
            )
            if not payload.dry_run and search_mgr and report.touched_paper_ids:
                try:
                    search_mgr.batch_sync(report.touched_paper_ids, storage)
                except Exception:
                    logger.warning("Search index batch update failed after Notion sync")
            return {"status": "ok", "report": report.to_dict()}
        except Exception as e:
            return {"error": describe_exception(e)}

    @router.get("/api/paper/{paper_id:path}/summary")
    async def api_get_summary(paper_id: str):
        """Get the raw markdown summary body for editing."""
        from paper_assistant.summarizer import normalize_summary_body

        paper = storage.get_paper(paper_id)
        if paper is None:
            return {"error": f"Paper {paper_id} not found"}

        if not paper.summary_path:
            return {"error": "No summary available", "markdown": ""}

        summary_path = config.data_dir / paper.summary_path
        if not summary_path.exists():
            return {"error": "Summary file missing", "markdown": ""}

        raw = summary_path.read_text(encoding="utf-8")
        return {"markdown": normalize_summary_body(raw)}

    @router.put("/api/paper/{paper_id:path}/summary")
    async def api_update_summary(paper_id: str, req: UpdateSummaryRequest):
        """Update a paper's summary and optionally regenerate audio."""
        from paper_assistant.audio_assets import render_audio_assets
        from paper_assistant.models import ProcessingStatus
        from paper_assistant.podcast import generate_feed
        from paper_assistant.summarizer import (
            SummarizationResult,
            find_one_pager,
            format_summary_file,
            parse_summary_sections,
        )

        paper = storage.get_paper(paper_id)
        if paper is None:
            return {"error": f"Paper {paper_id} not found"}

        if not req.markdown.strip():
            return {"error": "Summary cannot be empty"}

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
            storage.save_summary(paper_id, summary_content)
            paper = storage.get_paper(paper_id)
        except Exception as e:
            return {"error": f"Failed to save summary: {e}"}

        audio_result = await render_audio_assets(
            config=config,
            storage=storage,
            paper=paper,
            source_markdown=req.markdown,
            skip_transcript=not req.regenerate_audio,
            skip_audio=not req.regenerate_audio,
        )
        paper = storage.get_paper(paper_id) or paper
        if paper.audio_path:
            paper.status = ProcessingStatus.COMPLETE
            storage.add_paper(paper)

        try:
            all_papers = storage.list_papers()
            generate_feed(config, all_papers)
        except Exception:
            pass

        if search_mgr:
            try:
                search_mgr.sync_paper(paper_id, storage)
            except Exception:
                logger.warning("Search index update failed for %s", paper_id)

        response = {
            "status": "ok",
            "paper_id": paper_id,
            "title": paper.metadata.title,
            "transcript_path": paper.transcript_path,
            "audio_path": paper.audio_path,
            "backend_used": audio_result.backend_used,
        }
        if audio_result.warnings:
            response["warnings"] = audio_result.warnings
        return response

    @router.post("/api/paper/{paper_id:path}/transcript/regenerate")
    async def api_regenerate_transcript(
        paper_id: str,
        req: TranscriptRegenerateRequest | None = None,
    ):
        """Regenerate narration transcript + audio for an existing paper."""
        from paper_assistant.pipeline import regenerate_transcript_and_audio

        payload = req or TranscriptRegenerateRequest()
        try:
            result = await regenerate_transcript_and_audio(
                config=config,
                storage=storage,
                paper_id=paper_id,
                provided_script_markdown=payload.script_markdown,
                script_model_override=payload.model,
            )
        except KeyError:
            return {"error": f"Paper {paper_id} not found"}
        except ValueError as e:
            return {"error": str(e)}

        if search_mgr:
            try:
                search_mgr.sync_paper(paper_id, storage)
            except Exception:
                logger.warning("Search index update failed for %s", paper_id)

        response = {
            "status": "ok",
            "paper_id": result.paper_id,
            "title": result.title,
            "transcript_path": (
                str(result.transcript_path) if result.transcript_path else None
            ),
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "backend_used": result.backend_used,
            "script_model": result.script_model,
        }
        if result.warnings:
            response["warnings"] = result.warnings
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
