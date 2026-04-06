"""Web article fetching, metadata extraction, and URL utilities."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from paper_assistant.arxiv import parse_arxiv_url
from paper_assistant.models import PaperMetadata, SourceType


def is_arxiv_url(url: str) -> bool:
    """Return True if *url* resolves to an arXiv paper identifier."""
    try:
        parse_arxiv_url(url)
    except ValueError:
        return False
    return True


def slugify_url(url: str, max_length: int = 80) -> str:
    """Derive a human-readable, filesystem-safe slug from a URL.

    >>> slugify_url("https://www.thinkingmachines.ai/blog/on-policy-distillation/")
    'thinkingmachines-ai-blog-on-policy-distillation'
    """
    parsed = urlparse(url)
    raw = parsed.netloc + parsed.path
    # Strip www. prefix and trailing slash
    raw = re.sub(r"^www\.", "", raw).rstrip("/")
    # Replace non-alphanumeric chars with hyphens
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw)
    # Collapse consecutive hyphens and strip leading/trailing
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    # Truncate at word boundary
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0].rstrip("-")
    return slug


def slugify_title(title: str, max_length: int = 80) -> str:
    """Derive a human-readable, filesystem-safe slug from a title."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title)
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0].rstrip("-")
    return slug or "note"


async def fetch_article(
    url: str,
    *,
    timeout: float = 30.0,
) -> tuple[PaperMetadata, str]:
    """Fetch a web article and extract metadata + body text.

    Returns:
        (metadata, body_text) where body_text is the article content as plain text.
    """
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "paper-assistant/0.1"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    html = response.text
    slug = slugify_url(url)

    # Extract metadata from HTML meta tags
    title, authors, published, abstract = _extract_meta(html)
    if not title:
        title = slug  # fallback

    # Extract article body text
    body_text = _extract_body(html, url)

    metadata = PaperMetadata(
        source_type=SourceType.WEB,
        source_url=url,
        source_slug=slug,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
    )
    return metadata, body_text


def _extract_meta(
    html: str,
) -> tuple[str, list[str], datetime | None, str]:
    """Extract title, authors, published date, and description from HTML meta tags."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Title: og:title > <title> tag
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Authors: article:author, author meta, or byline
    authors: list[str] = []
    for meta in soup.find_all("meta", attrs={"name": "author"}):
        if meta.get("content"):
            authors.append(meta["content"].strip())
    if not authors:
        for meta in soup.find_all("meta", property="article:author"):
            if meta.get("content"):
                authors.append(meta["content"].strip())

    # Published date
    published: datetime | None = None
    for attr in ("article:published_time", "article:published"):
        tag = soup.find("meta", property=attr)
        if tag and tag.get("content"):
            try:
                published = datetime.fromisoformat(
                    tag["content"].replace("Z", "+00:00")
                )
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                pass

    # Description / abstract
    abstract = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        abstract = og_desc["content"].strip()
    elif (desc := soup.find("meta", attrs={"name": "description"})) and desc.get(
        "content"
    ):
        abstract = desc["content"].strip()

    return title, authors, published, abstract


def _extract_body(html: str, url: str) -> str:
    """Extract article body text, preferring trafilatura with BS4 fallback."""
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            output_format="txt",
        )
        if result and len(result.strip()) > 100:
            return result.strip()
    except Exception:
        pass

    # Fallback: strip tags with BeautifulSoup
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style elements
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text
