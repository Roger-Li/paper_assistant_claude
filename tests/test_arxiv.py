"""Tests for paper_assistant.arxiv URL parsing and request resilience."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from paper_assistant.arxiv import ArxivRateLimitError, fetch_metadata, parse_arxiv_url
from paper_assistant.config import Config

ATOM_ENTRY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2503.10291v1</id>
    <updated>2025-03-13T00:00:00Z</updated>
    <published>2025-03-13T00:00:00Z</published>
    <title>Test Paper</title>
    <summary>Test abstract</summary>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <arxiv:primary_category term="cs.AI" />
    <category term="cs.AI" />
  </entry>
</feed>
"""


def _metadata_response(
    status_code: int = 200,
    *,
    headers: dict[str, str] | None = None,
    text: str = ATOM_ENTRY_XML,
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers=headers,
        text=text,
        request=httpx.Request("GET", "https://export.arxiv.org/api/query"),
    )


def _test_config(**overrides: object) -> Config:
    kwargs = {
        "anthropic_api_key": "test-key",
        "arxiv_max_retries": 2,
        "arxiv_backoff_base_seconds": 0.01,
        "arxiv_backoff_cap_seconds": 0.05,
    }
    kwargs.update(overrides)
    return Config(**kwargs)


class TestParseArxivUrl:
    def test_abs_url(self):
        assert parse_arxiv_url("https://arxiv.org/abs/2503.10291") == "2503.10291"

    def test_pdf_url(self):
        assert parse_arxiv_url("https://arxiv.org/pdf/2503.10291") == "2503.10291"

    def test_versioned_url(self):
        assert parse_arxiv_url("https://arxiv.org/abs/2503.10291v2") == "2503.10291"

    def test_pdf_extension(self):
        assert parse_arxiv_url("https://arxiv.org/pdf/2503.10291.pdf") == "2503.10291"

    def test_bare_id(self):
        assert parse_arxiv_url("2503.10291") == "2503.10291"

    def test_bare_id_versioned(self):
        assert parse_arxiv_url("2503.10291v3") == "2503.10291"

    def test_five_digit_id(self):
        assert parse_arxiv_url("2501.09898") == "2501.09898"

    def test_www_prefix(self):
        assert parse_arxiv_url("https://www.arxiv.org/abs/2503.10291") == "2503.10291"

    def test_whitespace_stripped(self):
        assert parse_arxiv_url("  2503.10291  ") == "2503.10291"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid arXiv"):
            parse_arxiv_url("https://example.com/paper")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_arxiv_url("")

    def test_random_string_raises(self):
        with pytest.raises(ValueError):
            parse_arxiv_url("not-an-arxiv-id")


class TestArxivRetries:
    @pytest.mark.asyncio
    async def test_fetch_metadata_retries_429_then_succeeds(self):
        get_mock = AsyncMock(
            side_effect=[
                _metadata_response(429, headers={"Retry-After": "1"}),
                _metadata_response(200),
            ]
        )
        sleep_mock = AsyncMock()
        with (
            patch("paper_assistant.arxiv.httpx.AsyncClient.get", new=get_mock),
            patch("paper_assistant.arxiv.asyncio.sleep", new=sleep_mock),
        ):
            metadata = await fetch_metadata("2503.10291", config=_test_config())

        assert metadata.title == "Test Paper"
        assert get_mock.await_count == 2
        assert sleep_mock.await_args_list[0].args[0] == 1.0

    @pytest.mark.asyncio
    async def test_fetch_metadata_honors_http_date_retry_after(self):
        now = datetime(2026, 2, 14, 10, 0, 0, tzinfo=timezone.utc)
        retry_at = now + timedelta(seconds=15)
        get_mock = AsyncMock(
            side_effect=[
                _metadata_response(429, headers={"Retry-After": format_datetime(retry_at)}),
                _metadata_response(200),
            ]
        )
        sleep_mock = AsyncMock()
        with (
            patch("paper_assistant.arxiv.httpx.AsyncClient.get", new=get_mock),
            patch("paper_assistant.arxiv.asyncio.sleep", new=sleep_mock),
            patch("paper_assistant.arxiv._utc_now", return_value=now),
        ):
            await fetch_metadata("2503.10291", config=_test_config())

        assert sleep_mock.await_args_list[0].args[0] == 15.0

    @pytest.mark.asyncio
    async def test_fetch_metadata_retries_transport_and_5xx_then_succeeds(self):
        get_mock = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timed out"),
                _metadata_response(503, text="unavailable"),
                _metadata_response(200),
            ]
        )
        sleep_mock = AsyncMock()
        with (
            patch("paper_assistant.arxiv.httpx.AsyncClient.get", new=get_mock),
            patch("paper_assistant.arxiv.asyncio.sleep", new=sleep_mock),
        ):
            metadata = await fetch_metadata("2503.10291", config=_test_config())

        assert metadata.arxiv_id == "2503.10291"
        assert get_mock.await_count == 3
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_metadata_does_not_retry_non_retryable_4xx(self):
        get_mock = AsyncMock(side_effect=[_metadata_response(404, text="not found")])
        sleep_mock = AsyncMock()
        with (
            patch("paper_assistant.arxiv.httpx.AsyncClient.get", new=get_mock),
            patch("paper_assistant.arxiv.asyncio.sleep", new=sleep_mock),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_metadata("2503.10291", config=_test_config())

        assert get_mock.await_count == 1
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_fetch_metadata_raises_rate_limit_error_after_max_retries(self):
        get_mock = AsyncMock(
            side_effect=[
                _metadata_response(429, headers={"Retry-After": "5"}),
                _metadata_response(429, headers={"Retry-After": "12"}),
            ]
        )
        sleep_mock = AsyncMock()
        with (
            patch("paper_assistant.arxiv.httpx.AsyncClient.get", new=get_mock),
            patch("paper_assistant.arxiv.asyncio.sleep", new=sleep_mock),
        ):
            with pytest.raises(ArxivRateLimitError, match="rate limit"):
                await fetch_metadata("2503.10291", config=_test_config(arxiv_max_retries=1))

        assert get_mock.await_count == 2
        assert sleep_mock.await_count == 1
