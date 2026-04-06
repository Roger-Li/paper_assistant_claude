"""Tests for Hugging Face paper-page metadata and markdown retrieval."""

from __future__ import annotations
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from paper_assistant.hf_papers import (
    HFPaperContentRejectedError,
    extract_markdown_body,
    fetch_markdown_body,
    fetch_metadata,
    metadata_from_api_payload,
)
from tests.helpers import load_hf_markdown_fixture, load_hf_metadata_payload


def _json_response(url: str, payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", url),
    )


def _text_response(url: str, text: str) -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("GET", url),
    )


@pytest.mark.parametrize(
    ("arxiv_id", "expected_first_author", "expected_author_count", "expected_year"),
    [
        ("2603.19835", "Chiyu Ma", 10, 2026),
        ("2503.10291", "Weiyun Wang", 15, 2025),
        ("2601.15621", "Hangrui Hu", 16, 2026),
    ],
)
def test_metadata_from_api_payload_maps_real_fixtures(
    arxiv_id: str,
    expected_first_author: str,
    expected_author_count: int,
    expected_year: int,
):
    payload = load_hf_metadata_payload(arxiv_id)

    metadata = metadata_from_api_payload(payload)

    assert metadata.arxiv_id == arxiv_id
    assert metadata.title == payload["title"]
    assert metadata.abstract == payload["summary"]
    assert metadata.authors[0] == expected_first_author
    assert len(metadata.authors) == expected_author_count
    assert metadata.published is not None
    assert metadata.published.year == expected_year
    assert metadata.categories == []
    assert metadata.arxiv_url == f"https://arxiv.org/abs/{arxiv_id}"
    assert metadata.pdf_url == f"https://arxiv.org/pdf/{arxiv_id}"


def test_metadata_from_api_payload_handles_missing_optional_fields():
    metadata = metadata_from_api_payload({"id": "1234.56789", "title": "Fallback Title"})

    assert metadata.arxiv_id == "1234.56789"
    assert metadata.title == "Fallback Title"
    assert metadata.authors == []
    assert metadata.abstract == ""
    assert metadata.published is None
    assert metadata.categories == []


@pytest.mark.parametrize("arxiv_id", ["2603.19835", "2503.10291"])
def test_extract_markdown_body_accepts_real_arxiv_html_fixtures(arxiv_id: str):
    body = extract_markdown_body(load_hf_markdown_fixture(arxiv_id))

    assert len(body) >= 2500
    assert "Abstract" in body
    assert "URL Source:" not in body
    assert "Markdown Content:" not in body


def test_extract_markdown_body_rejects_hf_paper_page_fallback_output():
    fallback_markdown = load_hf_markdown_fixture("2603.19835").replace(
        "URL Source: https://arxiv.org/html/2603.19835",
        "URL Source: https://huggingface.co/papers/2603.19835",
        1,
    )

    with pytest.raises(HFPaperContentRejectedError, match="arXiv HTML source"):
        extract_markdown_body(fallback_markdown)


def test_extract_markdown_body_rejects_short_wrapper_only_output():
    markdown = (
        "Title: Test Paper\n\n"
        "URL Source: https://arxiv.org/html/1234.56789\n\n"
        "Markdown Content:\n"
        "###### Abstract\n\nTiny.\n"
    )

    with pytest.raises(HFPaperContentRejectedError, match="too short"):
        extract_markdown_body(markdown)


@pytest.mark.asyncio
async def test_fetch_metadata_uses_http_and_maps_response():
    payload = load_hf_metadata_payload("2601.15621")
    get_mock = AsyncMock(
        return_value=_json_response("https://huggingface.co/api/papers/2601.15621", payload)
    )

    with patch("paper_assistant.hf_papers.httpx.AsyncClient.get", new=get_mock):
        metadata = await fetch_metadata("2601.15621")

    assert metadata.title == "Qwen3-TTS Technical Report"
    assert metadata.authors[-1] == "Junyang Lin"
    assert get_mock.await_count == 1


@pytest.mark.asyncio
async def test_fetch_markdown_body_uses_http_and_validates_response():
    markdown = load_hf_markdown_fixture("2603.19835")
    get_mock = AsyncMock(
        return_value=_text_response("https://huggingface.co/papers/2603.19835.md", markdown)
    )

    with patch("paper_assistant.hf_papers.httpx.AsyncClient.get", new=get_mock):
        body = await fetch_markdown_body("2603.19835")

    assert body.startswith("\\useunder")
    assert "Future-KL Influenced Policy Optimization" in body
    assert get_mock.await_count == 1
