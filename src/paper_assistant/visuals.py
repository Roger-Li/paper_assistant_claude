"""Best-effort key-visual extraction and injection for paper summaries.

Parses arXiv HTML markdown (as exposed by Hugging Face's ``papers/<id>.md``
endpoint) for image-backed figure/table candidates, and injects up to N image
Markdown blocks into an agent-generated summary near the relevant figure or
table discussion.

arXiv HTML emits images as::

    ![Image 1: ...](https://arxiv.org/html/<id>vK/xN.png)

    Figure 1: <caption text on a single line>

The first image preceding a ``Figure N:`` / ``Table N:`` caption header is
treated as the canonical visual for that label. Multi-panel figures are
collapsed to their lead panel — the goal is 1-3 crucial visuals per summary,
not exhaustive coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# arXiv HTML wraps alt text containing brackets (e.g. ``[Uncaptioned image]``)
# inside the markdown alt, so the alt match has to be non-greedy and tolerate
# ``]`` characters that aren't the closing bracket of the alt itself.
_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[(?P<alt>.*?)\]\((?P<url>https?://[^)\s]+)\)"
)

_CAPTION_HEADER_RE = re.compile(
    r"^\s*(?P<kind>Figure|Fig\.?|Table|Tab\.?)\s*(?P<number>\d+)\s*[:.]\s*(?P<text>.+?)\s*$",
    re.IGNORECASE,
)

_REFERENCE_RE = re.compile(
    r"\b(?P<kind>Figure|Fig\.?|Table|Tab\.?)\s*(?P<number>\d+)",
    re.IGNORECASE,
)

_ARXIV_HTML_PREFIX = "https://arxiv.org/html/"

DEFAULT_MAX_VISUALS = 3


def _normalize_kind(raw: str) -> str:
    return "table" if raw.lower().startswith("tab") else "figure"


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    match = re.match(r"^(.+?[.!?])(?:\s|$)", cleaned)
    if match:
        return match.group(1).strip()
    return cleaned


@dataclass(frozen=True)
class VisualCandidate:
    """One image-backed figure/table candidate parsed from arXiv HTML markdown."""

    kind: str  # "figure" or "table"
    number: int
    image_url: str
    caption: str
    alt_text: str = ""

    @property
    def label(self) -> str:
        return f"{self.kind.capitalize()} {self.number}"

    @property
    def short_caption(self) -> str:
        return _first_sentence(self.caption)

    def to_markdown(self) -> str:
        """Render as Markdown image with a compact alt-text caption."""
        short = self.short_caption
        alt = f"{self.label}: {short}" if short else self.label
        return f"![{alt}]({self.image_url})"


def extract_visual_candidates(markdown: str) -> list[VisualCandidate]:
    """Parse arXiv HTML markdown for image-backed figure/table candidates."""
    if not markdown:
        return []

    pending: list[tuple[str, str]] = []
    seen: set[tuple[str, int]] = set()
    candidates: list[VisualCandidate] = []

    for line in markdown.split("\n"):
        image_matches = list(_IMAGE_MARKDOWN_RE.finditer(line))
        for image_match in image_matches:
            url = image_match.group("url")
            if not url.startswith(_ARXIV_HTML_PREFIX):
                continue
            pending.append((image_match.group("alt"), url))

        caption_match = _CAPTION_HEADER_RE.match(line)
        if caption_match is not None:
            if pending:
                kind = _normalize_kind(caption_match.group("kind"))
                number = int(caption_match.group("number"))
                caption_text = caption_match.group("text").strip()
                key = (kind, number)
                if key not in seen:
                    alt, url = pending[0]
                    candidates.append(
                        VisualCandidate(
                            kind=kind,
                            number=number,
                            image_url=url,
                            caption=caption_text,
                            alt_text=alt,
                        )
                    )
                    seen.add(key)
            pending = []
            continue

        if image_matches:
            # Image-only line keeps the pending buffer for later captions.
            continue

        if line.strip():
            # Any non-blank, non-image, non-caption line breaks the
            # association — captions must appear immediately after images.
            pending = []

    return candidates


def inject_visuals(
    summary_markdown: str,
    candidates: list[VisualCandidate],
    *,
    max_visuals: int = DEFAULT_MAX_VISUALS,
) -> str:
    """Inject up to *max_visuals* candidate images near their first reference.

    Idempotent: candidates whose URL is already present in the summary are
    skipped, so re-running on a summary already enriched with images is a
    no-op. References inside fenced code blocks are ignored.
    """
    if not candidates or max_visuals <= 0 or not summary_markdown.strip():
        return summary_markdown

    by_label: dict[tuple[str, int], VisualCandidate] = {
        (c.kind, c.number): c for c in candidates
    }

    existing_urls: set[str] = {
        match.group("url") for match in _IMAGE_MARKDOWN_RE.finditer(summary_markdown)
    }
    already_injected = sum(1 for c in candidates if c.image_url in existing_urls)
    visuals_remaining = max_visuals - already_injected
    if visuals_remaining <= 0:
        return summary_markdown

    blocks = re.split(r"\n\s*\n", summary_markdown)
    output: list[str] = []
    used_keys: set[tuple[str, int]] = set()

    for block in blocks:
        output.append(block)

        if visuals_remaining <= 0:
            continue
        if _looks_like_code_block(block):
            continue

        for match in _REFERENCE_RE.finditer(block):
            if visuals_remaining <= 0:
                break
            kind = _normalize_kind(match.group("kind"))
            number = int(match.group("number"))
            key = (kind, number)
            if key in used_keys:
                continue
            candidate = by_label.get(key)
            if candidate is None:
                used_keys.add(key)
                continue
            if candidate.image_url in existing_urls:
                used_keys.add(key)
                continue
            output.append(candidate.to_markdown())
            used_keys.add(key)
            existing_urls.add(candidate.image_url)
            visuals_remaining -= 1

    return "\n\n".join(output)


def _looks_like_code_block(block: str) -> bool:
    for line in block.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            return True
    return False


def enrich_summary_with_visuals(
    *,
    full_markdown: str,
    source_markdown: str | None,
    max_visuals: int = DEFAULT_MAX_VISUALS,
) -> str:
    """Convenience wrapper used by add pipelines.

    Returns the summary markdown, possibly enriched with up to *max_visuals*
    image candidates extracted from the source HF/arXiv HTML markdown. Safe
    when *source_markdown* is None, empty, or contains no image candidates.
    """
    if not source_markdown:
        return full_markdown
    candidates = extract_visual_candidates(source_markdown)
    if not candidates:
        return full_markdown
    return inject_visuals(full_markdown, candidates, max_visuals=max_visuals)
