"""Hugging Face paper-page client for arXiv metadata and markdown content."""

from __future__ import annotations

from datetime import datetime, timezone
import re

import httpx

from paper_assistant.config import Config
from paper_assistant.models import PaperMetadata

HF_PAPERS_API_URL = "https://huggingface.co/api/papers/{arxiv_id}"
HF_PAPERS_MARKDOWN_URL = "https://huggingface.co/papers/{arxiv_id}.md"
HF_TIMEOUT_SECONDS = 30.0
HF_MIN_BODY_CHARS = 2500
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"


class HFPaperContentRejectedError(Exception):
    """Raised when HF markdown content is unavailable or fails the arXiv-quality gate."""


def _request_headers(config: Config | None, accept: str) -> dict[str, str]:
    user_agent = config.arxiv_user_agent if config is not None else "paper-assistant/0.1"
    return {"User-Agent": user_agent, "Accept": accept}


async def _fetch(url: str, *, config: Config | None, accept: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HF_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(url, headers=_request_headers(config, accept))
    response.raise_for_status()
    return response


def _parse_published_at(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_author_names(authors: object) -> list[str]:
    if not isinstance(authors, list):
        return []

    names: list[str] = []
    for author in authors:
        if isinstance(author, dict):
            name = str(author.get("name") or "").strip()
        else:
            name = str(author).strip()
        if name:
            names.append(name)
    return names


def metadata_from_api_payload(payload: dict[str, object]) -> PaperMetadata:
    arxiv_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()

    if not arxiv_id:
        raise ValueError("HF paper payload is missing an 'id' field.")
    if not title:
        raise ValueError("HF paper payload is missing a 'title' field.")

    published = _parse_published_at(payload.get("publishedAt") or payload.get("published_at"))

    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors=_extract_author_names(payload.get("authors")),
        abstract=str(payload.get("summary") or "").strip(),
        published=published,
        categories=[],
        arxiv_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
        pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
    )


async def fetch_metadata(arxiv_id: str, config: Config | None = None) -> PaperMetadata:
    """Fetch arXiv paper metadata from Hugging Face paper pages."""
    response = await _fetch(
        HF_PAPERS_API_URL.format(arxiv_id=arxiv_id),
        config=config,
        accept="application/json, */*;q=0.1",
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("HF paper metadata response was not a JSON object.")
    return metadata_from_api_payload(payload)


async def fetch_markdown(arxiv_id: str, config: Config | None = None) -> str:
    """Fetch the raw markdown export for an HF paper page."""
    response = await _fetch(
        HF_PAPERS_MARKDOWN_URL.format(arxiv_id=arxiv_id),
        config=config,
        accept="text/markdown, text/plain;q=0.9, */*;q=0.1",
    )
    return response.text


def _is_setext_underline(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 3 and stripped[0] in "-=" and all(ch == stripped[0] for ch in stripped)


def _has_abstract_heading(body: str) -> bool:
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().rstrip(":").lower()
            if heading == "abstract":
                return True
            continue

        if stripped.rstrip(":").lower() == "abstract":
            if idx + 1 < len(lines) and _is_setext_underline(lines[idx + 1]):
                return True

    return False


def extract_markdown_body(
    markdown: str,
    *,
    min_chars: int = HF_MIN_BODY_CHARS,
) -> str:
    """Strip the HF wrapper and accept only substantial arXiv HTML markdown."""
    lines = markdown.splitlines()
    body_start_idx: int | None = None
    url_source: str | None = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("URL Source:"):
            url_source = stripped.partition(":")[2].strip()
        if stripped == "Markdown Content:":
            body_start_idx = idx + 1
            break

    if body_start_idx is None:
        raise HFPaperContentRejectedError("HF markdown wrapper is missing 'Markdown Content:'.")
    if not url_source or not url_source.startswith("https://arxiv.org/html/"):
        raise HFPaperContentRejectedError("HF markdown did not come from an arXiv HTML source.")

    body = "\n".join(lines[body_start_idx:]).strip()
    if not body:
        raise HFPaperContentRejectedError("HF markdown body was empty after stripping the wrapper.")

    leading_block = "\n".join(body.splitlines()[:6])
    if re.search(r"(?m)^(?:URL Source|Markdown Content):", leading_block):
        raise HFPaperContentRejectedError("HF markdown wrapper was not stripped cleanly.")
    if not _has_abstract_heading(body):
        raise HFPaperContentRejectedError("HF markdown body does not contain an Abstract heading.")
    if len(body) < min_chars:
        raise HFPaperContentRejectedError(
            f"HF markdown body is too short ({len(body)} chars; need at least {min_chars})."
        )

    return body


async def fetch_markdown_body(arxiv_id: str, config: Config | None = None) -> str:
    """Fetch and validate the arXiv HTML markdown body from HF."""
    markdown = await fetch_markdown(arxiv_id, config=config)
    return extract_markdown_body(markdown)
