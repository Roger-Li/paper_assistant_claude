"""ArXiv metadata fetching and PDF downloading."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from paper_assistant.models import PaperMetadata

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"

# Matches arXiv URLs and bare IDs
ARXIV_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$"
)
BARE_ID_PATTERN = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")

# arXiv Atom XML namespaces
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"


class PaperNotFoundError(Exception):
    """Raised when an arXiv paper is not found."""


def parse_arxiv_url(url: str) -> str:
    """Extract arXiv ID from a URL or bare ID string.

    Supports:
      - https://arxiv.org/abs/2503.10291
      - https://arxiv.org/pdf/2503.10291
      - https://arxiv.org/abs/2503.10291v2
      - 2503.10291

    Returns:
        The arXiv ID (e.g., "2503.10291").

    Raises:
        ValueError: If the input is not a valid arXiv URL or ID.
    """
    url = url.strip()

    # Try bare ID first
    match = BARE_ID_PATTERN.match(url)
    if match:
        return match.group(1)

    # Try URL pattern
    match = ARXIV_PATTERN.match(url)
    if match:
        return match.group(1)

    raise ValueError(
        f"Invalid arXiv URL or ID: {url!r}. "
        "Expected format: https://arxiv.org/abs/XXXX.XXXXX or a bare arXiv ID."
    )


async def fetch_metadata(arxiv_id: str) -> PaperMetadata:
    """Fetch paper metadata from the arXiv Atom API.

    Uses: http://export.arxiv.org/api/query?id_list=2503.10291

    Returns:
        PaperMetadata with all fields populated.

    Raises:
        PaperNotFoundError: If arXiv returns no results.
        httpx.HTTPError: On network failures.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.get(ARXIV_API_URL, params={"id_list": arxiv_id})
                if resp.status_code == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                last_exc = e
                await asyncio.sleep(3 * (attempt + 1))
        else:
            if last_exc:
                raise last_exc
            resp.raise_for_status()  # raise on final failure

    root = ElementTree.fromstring(resp.text)
    entries = root.findall(f"{{{ATOM_NS}}}entry")

    if not entries:
        raise PaperNotFoundError(f"No paper found for arXiv ID: {arxiv_id}")

    entry = entries[0]

    # Check for error (arXiv returns an entry with id containing "Error")
    entry_id = entry.findtext(f"{{{ATOM_NS}}}id", "")
    if "Error" in entry_id:
        raise PaperNotFoundError(f"No paper found for arXiv ID: {arxiv_id}")

    title = entry.findtext(f"{{{ATOM_NS}}}title", "").strip()
    # Collapse newlines in title (arXiv wraps long titles)
    title = re.sub(r"\s+", " ", title)

    abstract = entry.findtext(f"{{{ATOM_NS}}}summary", "").strip()

    authors = [
        author.findtext(f"{{{ATOM_NS}}}name", "").strip()
        for author in entry.findall(f"{{{ATOM_NS}}}author")
    ]

    published_str = entry.findtext(f"{{{ATOM_NS}}}published", "")
    published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))

    categories = [
        cat.get("term", "")
        for cat in entry.findall(f"{{{ARXIV_NS}}}primary_category")
    ]
    # Also grab all categories
    categories += [
        cat.get("term", "")
        for cat in entry.findall(f"{{{ATOM_NS}}}category")
        if cat.get("term", "") not in categories
    ]

    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
        categories=categories,
        arxiv_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
        pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
    )


async def download_pdf(arxiv_id: str, output_path: Path) -> Path:
    """Download the PDF from arXiv.

    Respects arXiv rate limiting with a 3-second delay.

    Returns:
        Path to the downloaded PDF.
    """
    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)

    # Rate limit: arXiv asks for 3s between requests
    await asyncio.sleep(3)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    output_path.write_bytes(resp.content)
    return output_path
