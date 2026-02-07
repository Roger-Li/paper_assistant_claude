"""Tests for paper_assistant.arxiv URL parsing."""

import pytest

from paper_assistant.arxiv import parse_arxiv_url


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
