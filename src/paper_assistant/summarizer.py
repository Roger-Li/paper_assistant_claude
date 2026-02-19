"""Claude API integration for paper and article summarization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from paper_assistant.config import Config
from paper_assistant.models import PaperMetadata, SourceType
from paper_assistant.prompt import (
    ARTICLE_SYSTEM_PROMPT,
    ARTICLE_USER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)


@dataclass
class SummarizationResult:
    """Parsed result from Claude's response."""

    full_markdown: str
    one_pager: str
    sections: dict[str, str] = field(default_factory=dict)
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


async def summarize_paper_text(
    config: Config,
    metadata: PaperMetadata,
    paper_text: str,
) -> SummarizationResult:
    """Send extracted paper text to Claude for summarization.

    Args:
        config: Application configuration (API key, model).
        metadata: Paper metadata for context.
        paper_text: Markdown text extracted from PDF.

    Returns:
        SummarizationResult with full markdown and parsed sections.
    """
    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    user_message = USER_PROMPT_TEMPLATE.format(
        title=metadata.title,
        authors=", ".join(metadata.authors),
        arxiv_id=metadata.arxiv_id or "",
        paper_content=paper_text,
    )

    response = await client.messages.create(
        model=config.claude_model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    full_text = response.content[0].text
    sections = parse_summary_sections(full_text)

    return SummarizationResult(
        full_markdown=full_text,
        one_pager=find_one_pager(sections),
        sections=sections,
        model_used=config.claude_model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


async def summarize_paper_pdf(
    config: Config,
    metadata: PaperMetadata,
    pdf_path: Path,
) -> SummarizationResult:
    """Send raw PDF to Claude using native document support.

    More expensive but preserves figures and formatting.
    """
    from paper_assistant.pdf import encode_pdf_base64

    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    pdf_b64 = encode_pdf_base64(pdf_path)

    user_text = (
        f"Please analyze and summarize this ML research paper.\n\n"
        f"**Title**: {metadata.title}\n"
        f"**Authors**: {', '.join(metadata.authors)}\n"
        f"**arXiv ID**: {metadata.arxiv_id or ''}\n"
    )

    response = await client.messages.create(
        model=config.claude_model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    full_text = response.content[0].text
    sections = parse_summary_sections(full_text)

    return SummarizationResult(
        full_markdown=full_text,
        one_pager=find_one_pager(sections),
        sections=sections,
        model_used=config.claude_model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


async def summarize_article_text(
    config: Config,
    metadata: PaperMetadata,
    article_text: str,
) -> SummarizationResult:
    """Send web article text to Claude for summarization.

    Uses the article-specific prompt template (not the ML paper prompt).
    """
    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    user_message = ARTICLE_USER_PROMPT_TEMPLATE.format(
        title=metadata.title,
        authors=", ".join(metadata.authors) if metadata.authors else "Unknown",
        source_url=metadata.source_url or "",
        article_content=article_text,
    )

    response = await client.messages.create(
        model=config.claude_model,
        max_tokens=8192,
        system=ARTICLE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    full_text = response.content[0].text
    sections = parse_summary_sections(full_text)

    return SummarizationResult(
        full_markdown=full_text,
        one_pager=find_one_pager(sections),
        sections=sections,
        model_used=config.claude_model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def parse_summary_sections(markdown: str) -> dict[str, str]:
    """Parse Claude's markdown response into named sections.

    Splits on `# Header` lines and returns a dict mapping
    section names to their content (without the header line).
    """
    sections: dict[str, str] = {}
    current_section = ""
    current_lines: list[str] = []

    for line in markdown.split("\n"):
        # Match top-level headers (# Title)
        header_match = re.match(r"^#\s+(.+)$", line)
        if header_match:
            # Save previous section
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = header_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return sections


def find_one_pager(sections: dict[str, str]) -> str:
    """Find the one-pager section by flexible name matching.

    Handles variations like "One-Pager", "One-Pager Summary",
    "1. One-Pager Summary", etc.
    """
    for key in sections:
        if "one" in key.lower() and "pager" in key.lower():
            return sections[key]
    # Fallback: return first section content, or empty
    return next(iter(sections.values()), "")


def format_summary_file(metadata: PaperMetadata, summary: SummarizationResult) -> str:
    """Format the final Markdown file with YAML front matter."""
    authors_str = ", ".join(metadata.authors) if metadata.authors else "Unknown"
    # Escape quotes in title for YAML
    safe_title = metadata.title.replace('"', '\\"')

    if metadata.source_type == SourceType.WEB:
        # Web article format
        header_lines = [
            "---",
            f'title: "{safe_title}"',
            f"source_slug: {metadata.source_slug}",
            f"source_url: {metadata.source_url}",
            f"authors: {authors_str}",
        ]
        if metadata.published:
            header_lines.append(f"published: {metadata.published.isoformat()}")
        header_lines.extend(["---", ""])

        body_lines = [
            f"# {metadata.title}",
            "",
            f"**Source**: [{metadata.title}]({metadata.source_url})  ",
            f"**Authors**: {authors_str}",
            "",
            "---",
            "",
        ]
    else:
        # arXiv paper format (original behavior)
        header_lines = [
            "---",
            f'title: "{safe_title}"',
            f"arxiv_id: {metadata.arxiv_id}",
            f"authors: {authors_str}",
        ]
        if metadata.published:
            header_lines.append(f"published: {metadata.published.isoformat()}")
        if metadata.arxiv_url:
            header_lines.append(f"url: {metadata.arxiv_url}")
        header_lines.extend(["---", ""])

        body_lines = [
            f"# {metadata.title}",
            "",
        ]
        if metadata.arxiv_id and metadata.arxiv_url:
            body_lines.append(
                f"**arXiv**: [{metadata.arxiv_id}]({metadata.arxiv_url})  "
            )
        body_lines.extend([f"**Authors**: {authors_str}", "", "---", ""])

    header = "\n".join(header_lines)
    body = "\n".join(body_lines)
    return header + body + summary.full_markdown
