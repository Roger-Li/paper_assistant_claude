"""Derived narration script generation via Claude.

Rewrites a stored paper summary into a 5–8 minute spoken-word script
suitable for a single narrator. The prompt lives alongside this module
at ``paper_assistant/prompts/audio_script_instructions.md`` so it ships
inside the installed wheel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anthropic

from paper_assistant.config import Config
from paper_assistant.models import PaperMetadata, SourceType


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "audio_script_instructions.md"


class AudioScriptError(Exception):
    """Narration script could not be generated."""


@dataclass
class AudioScriptResult:
    script_markdown: str
    model_used: str
    input_tokens: int = 0
    output_tokens: int = 0


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AudioScriptError(
            f"Audio-script prompt missing at {PROMPT_PATH}."
        ) from exc


def _format_user_message(markdown: str, metadata: PaperMetadata) -> str:
    source_kind = {
        SourceType.ARXIV: "arXiv paper",
        SourceType.WEB: "article",
        SourceType.NOTE: "note",
    }.get(metadata.source_type, "paper")

    authors = ", ".join(metadata.authors) if metadata.authors else "Unknown"
    identity_lines = [
        f"Source: {source_kind}",
        f"Title: {metadata.title}",
        f"Authors: {authors}",
    ]
    if metadata.arxiv_id:
        identity_lines.append(f"arXiv ID: {metadata.arxiv_id}")
    if metadata.source_url:
        identity_lines.append(f"URL: {metadata.source_url}")

    identity = "\n".join(identity_lines)

    return (
        f"{identity}\n\n"
        "Here is the stored summary. Write the narration script as instructed:\n\n"
        "---\n\n"
        f"{markdown}\n"
    )


async def generate_audio_script(
    markdown: str,
    metadata: PaperMetadata,
    config: Config,
    model: str | None = None,
) -> AudioScriptResult:
    """Generate a narration script from a stored summary body."""
    if not config.anthropic_api_key:
        raise AudioScriptError(
            "ANTHROPIC_API_KEY is required for narration script generation."
        )
    if not markdown.strip():
        raise AudioScriptError("Source markdown is empty.")

    chosen_model = model or config.audio_script_model
    system_prompt = _load_system_prompt()

    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    try:
        response = await client.messages.create(
            model=chosen_model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": _format_user_message(markdown, metadata)}
            ],
        )
    except anthropic.APIError as exc:
        raise AudioScriptError(f"Claude API error: {exc}") from exc
    except Exception as exc:
        raise AudioScriptError(f"Unexpected script generation error: {exc}") from exc

    if not response.content:
        raise AudioScriptError("Claude returned no content for narration script.")

    text = response.content[0].text.strip()
    if not text:
        raise AudioScriptError("Claude returned an empty narration script.")

    return AudioScriptResult(
        script_markdown=text,
        model_used=chosen_model,
        input_tokens=getattr(response.usage, "input_tokens", 0),
        output_tokens=getattr(response.usage, "output_tokens", 0),
    )
