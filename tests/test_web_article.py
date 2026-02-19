"""Tests for web article URL handling and metadata extraction."""

from __future__ import annotations

import pytest

from paper_assistant.web_article import is_arxiv_url, slugify_url


class TestIsArxivUrl:
    def test_arxiv_abs_url(self):
        assert is_arxiv_url("https://arxiv.org/abs/2503.10291")

    def test_arxiv_pdf_url(self):
        assert is_arxiv_url("https://arxiv.org/pdf/2503.10291")

    def test_bare_arxiv_id(self):
        assert is_arxiv_url("2503.10291")

    def test_bare_arxiv_id_with_version(self):
        assert is_arxiv_url("2503.10291v2")

    def test_non_arxiv_url(self):
        assert not is_arxiv_url("https://example.com/blog/post")

    def test_similar_but_not_arxiv(self):
        assert not is_arxiv_url("https://notarxiv.org/abs/2503.10291")

    def test_empty_string(self):
        assert not is_arxiv_url("")


class TestSlugifyUrl:
    def test_basic_url(self):
        slug = slugify_url("https://example.com/blog/my-post")
        assert slug == "example-com-blog-my-post"

    def test_strips_www(self):
        slug = slugify_url("https://www.example.com/blog")
        assert slug == "example-com-blog"

    def test_strips_trailing_slash(self):
        slug = slugify_url("https://example.com/blog/post/")
        assert slug == "example-com-blog-post"

    def test_collapses_special_chars(self):
        slug = slugify_url("https://example.com/blog/my--special===post")
        assert "-" * 2 not in slug

    def test_lowercase(self):
        slug = slugify_url("https://Example.COM/Blog/POST")
        assert slug == slug.lower()

    def test_truncation(self):
        long_url = "https://example.com/" + "a" * 200
        slug = slugify_url(long_url, max_length=40)
        assert len(slug) <= 40

    def test_realistic_url(self):
        slug = slugify_url("https://www.thinkingmachines.ai/blog/on-policy-distillation/")
        assert "thinkingmachines" in slug
        assert "on-policy-distillation" in slug
