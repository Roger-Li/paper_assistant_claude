"""ArXiv metadata fetching and PDF downloading."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from paper_assistant.config import Config
from paper_assistant.models import PaperMetadata

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
DEFAULT_ARXIV_USER_AGENT = (
    "paper-assistant/0.1 (+https://arxiv.org/help/api/user-manual; "
    "set PAPER_ASSIST_ARXIV_USER_AGENT with contact email)"
)
DEFAULT_ARXIV_MAX_RETRIES = 6
DEFAULT_ARXIV_BACKOFF_BASE_SECONDS = 2.0
DEFAULT_ARXIV_BACKOFF_CAP_SECONDS = 90.0

# Matches arXiv URLs and bare IDs
ARXIV_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$"
)
BARE_ID_PATTERN = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")

# arXiv Atom XML namespaces
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
logger = logging.getLogger(__name__)


class PaperNotFoundError(Exception):
    """Raised when an arXiv paper is not found."""


class ArxivRateLimitError(Exception):
    """Raised when arXiv returns rate limiting repeatedly."""

    def __init__(self, attempts: int, retry_after_seconds: float | None = None):
        self.attempts = attempts
        self.retry_after_seconds = retry_after_seconds
        wait_seconds = max(1, int(round(retry_after_seconds))) if retry_after_seconds else 30
        super().__init__(
            "arXiv API rate limit exceeded after "
            f"{attempts} attempts. Retry in about {wait_seconds}s. "
            "Set PAPER_ASSIST_ARXIV_USER_AGENT with app name and contact email."
        )


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


def _resolve_request_policy(config: Config | None) -> tuple[str, int, float, float]:
    if config is not None:
        return (
            config.arxiv_user_agent,
            max(0, config.arxiv_max_retries),
            max(0.1, config.arxiv_backoff_base_seconds),
            max(0.1, config.arxiv_backoff_cap_seconds),
        )

    user_agent = os.getenv("PAPER_ASSIST_ARXIV_USER_AGENT", DEFAULT_ARXIV_USER_AGENT)
    max_retries = int(os.getenv("PAPER_ASSIST_ARXIV_MAX_RETRIES", DEFAULT_ARXIV_MAX_RETRIES))
    backoff_base = float(
        os.getenv("PAPER_ASSIST_ARXIV_BACKOFF_BASE_SECONDS", DEFAULT_ARXIV_BACKOFF_BASE_SECONDS)
    )
    backoff_cap = float(
        os.getenv("PAPER_ASSIST_ARXIV_BACKOFF_CAP_SECONDS", DEFAULT_ARXIV_BACKOFF_CAP_SECONDS)
    )
    return (
        user_agent,
        max(0, max_retries),
        max(0.1, backoff_base),
        max(0.1, backoff_cap),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None

    stripped = value.strip()
    try:
        seconds = float(stripped)
        return max(0.0, seconds)
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(stripped)
        if retry_at is None:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - _utc_now()).total_seconds())
    except (TypeError, ValueError):
        return None


def _compute_backoff_delay(attempt: int, base_seconds: float, cap_seconds: float) -> float:
    exp = min(cap_seconds, base_seconds * (2**attempt))
    return max(0.0, min(cap_seconds, exp * random.uniform(0.8, 1.2)))


async def _arxiv_get_with_retries(
    *,
    client: httpx.AsyncClient,
    url: str,
    request_label: str,
    user_agent: str,
    max_retries: int,
    backoff_base_seconds: float,
    backoff_cap_seconds: float,
    accept: str,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    total_attempts = max_retries + 1

    for attempt in range(total_attempts):
        try:
            resp = await client.get(
                url,
                params=params,
                headers={"User-Agent": user_agent, "Accept": accept},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == max_retries:
                raise
            delay = _compute_backoff_delay(attempt, backoff_base_seconds, backoff_cap_seconds)
            logger.warning(
                "arXiv %s request failed (%s), retry %d/%d in %.1fs",
                request_label,
                exc.__class__.__name__,
                attempt + 1,
                total_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        if resp.status_code == 429:
            retry_after_seconds = _parse_retry_after_seconds(resp.headers.get("Retry-After"))
            if attempt == max_retries:
                raise ArxivRateLimitError(
                    attempts=total_attempts,
                    retry_after_seconds=retry_after_seconds,
                )
            delay = (
                retry_after_seconds
                if retry_after_seconds is not None
                else _compute_backoff_delay(attempt, backoff_base_seconds, backoff_cap_seconds)
            )
            logger.warning(
                "arXiv %s rate limited (429), retry %d/%d in %.1fs",
                request_label,
                attempt + 1,
                total_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        if 500 <= resp.status_code < 600:
            if attempt == max_retries:
                resp.raise_for_status()
            delay = _compute_backoff_delay(attempt, backoff_base_seconds, backoff_cap_seconds)
            logger.warning(
                "arXiv %s server error (%d), retry %d/%d in %.1fs",
                request_label,
                resp.status_code,
                attempt + 1,
                total_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        resp.raise_for_status()
        return resp

    raise RuntimeError("Unreachable: arXiv retry loop exited without response")


async def fetch_metadata(arxiv_id: str, config: Config | None = None) -> PaperMetadata:
    """Fetch paper metadata from the arXiv Atom API.

    Uses: http://export.arxiv.org/api/query?id_list=2503.10291

    Returns:
        PaperMetadata with all fields populated.

    Raises:
        PaperNotFoundError: If arXiv returns no results.
        httpx.HTTPError: On network failures.
    """
    user_agent, max_retries, backoff_base, backoff_cap = _resolve_request_policy(config)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await _arxiv_get_with_retries(
            client=client,
            url=ARXIV_API_URL,
            request_label="metadata",
            params={"id_list": arxiv_id},
            user_agent=user_agent,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base,
            backoff_cap_seconds=backoff_cap,
            accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
        )

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


async def download_pdf(arxiv_id: str, output_path: Path, config: Config | None = None) -> Path:
    """Download the PDF from arXiv.

    Returns:
        Path to the downloaded PDF.
    """
    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
    user_agent, max_retries, backoff_base, backoff_cap = _resolve_request_policy(config)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await _arxiv_get_with_retries(
            client=client,
            url=url,
            request_label="pdf",
            user_agent=user_agent,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base,
            backoff_cap_seconds=backoff_cap,
            accept="application/pdf, */*;q=0.1",
        )

    output_path.write_bytes(resp.content)
    return output_path
