"""Tests for the HF-first arXiv add pipeline."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from paper_assistant.cli import _add_arxiv_paper
from paper_assistant.config import Config
from paper_assistant.hf_papers import HFPaperContentRejectedError, extract_markdown_body
from paper_assistant.models import ProcessingStatus
from paper_assistant.storage import StorageManager
from paper_assistant.summarizer import SummarizationResult
from tests.helpers import HF_PAPERS_FIXTURES_DIR, load_hf_metadata_fixture


def _load_body_fixture(arxiv_id: str) -> str:
    raw = (HF_PAPERS_FIXTURES_DIR / f"{arxiv_id}.markdown.md").read_text(encoding="utf-8")
    return extract_markdown_body(raw)


def _config(tmp_path: Path) -> Config:
    cfg = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
    )
    cfg.ensure_dirs()
    return cfg


def _obj(tmp_path: Path) -> dict[str, object]:
    return {
        "anthropic_api_key": "test-key",
        "data_dir": str(tmp_path),
        "icloud_sync": False,
    }


def _summary_result() -> SummarizationResult:
    return SummarizationResult(
        full_markdown="# One-Pager\nSummary body",
        one_pager="Summary body",
        sections={"One-Pager": "Summary body"},
        model_used="claude-sonnet-test",
        input_tokens=11,
        output_tokens=17,
    )


@pytest.mark.asyncio
async def test_add_arxiv_uses_hf_markdown_before_pdf(tmp_path):
    metadata = load_hf_metadata_fixture("2603.19835")
    body = _load_body_fixture("2603.19835")

    with (
        patch("paper_assistant.hf_papers.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.arxiv.fetch_metadata", new=AsyncMock()) as fetch_arxiv_metadata,
        patch(
            "paper_assistant.hf_papers.fetch_markdown_body",
            new=AsyncMock(return_value=body),
        ),
        patch("paper_assistant.arxiv.download_pdf", new=AsyncMock()) as download_pdf,
        patch(
            "paper_assistant.summarizer.summarize_paper_text",
            new=AsyncMock(return_value=_summary_result()),
        ) as summarize_paper_text,
        patch("paper_assistant.podcast.generate_feed", new=Mock()),
    ):
        await _add_arxiv_paper(
            _obj(tmp_path),
            "https://huggingface.co/papers/2603.19835",
            native_pdf=False,
            skip_audio=True,
            tags=["hf-first"],
            force=False,
        )

    storage = StorageManager(_config(tmp_path))
    paper = storage.get_paper("2603.19835")

    assert paper is not None
    assert paper.status == ProcessingStatus.COMPLETE
    assert paper.pdf_path is None
    assert paper.summary_path is not None
    assert paper.tags == ["hf-first"]
    assert summarize_paper_text.await_args.args[2] == body
    fetch_arxiv_metadata.assert_not_awaited()
    download_pdf.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_arxiv_falls_back_to_pdf_when_hf_markdown_rejected(tmp_path):
    metadata = load_hf_metadata_fixture("2503.10291")

    with (
        patch("paper_assistant.hf_papers.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch(
            "paper_assistant.hf_papers.fetch_markdown_body",
            new=AsyncMock(side_effect=HFPaperContentRejectedError("too short")),
        ),
        patch("paper_assistant.arxiv.download_pdf", new=AsyncMock()) as download_pdf,
        patch(
            "paper_assistant.pdf.extract_text_from_pdf",
            return_value="Extracted PDF body",
        ) as extract_text_from_pdf,
        patch(
            "paper_assistant.summarizer.summarize_paper_text",
            new=AsyncMock(return_value=_summary_result()),
        ) as summarize_paper_text,
        patch("paper_assistant.podcast.generate_feed", new=Mock()),
    ):
        await _add_arxiv_paper(
            _obj(tmp_path),
            "https://arxiv.org/abs/2503.10291",
            native_pdf=False,
            skip_audio=True,
            tags=[],
            force=False,
        )

    storage = StorageManager(_config(tmp_path))
    paper = storage.get_paper("2503.10291")

    assert paper is not None
    assert paper.status == ProcessingStatus.COMPLETE
    assert paper.pdf_path == "pdfs/2503.10291.pdf"
    assert summarize_paper_text.await_args.args[2] == "Extracted PDF body"
    download_pdf.assert_awaited_once()
    extract_text_from_pdf.assert_called_once()
