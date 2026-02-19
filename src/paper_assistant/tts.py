"""Text-to-speech conversion using edge-tts."""

from __future__ import annotations

import re
from pathlib import Path

import edge_tts


async def text_to_speech(
    text: str,
    output_path: Path,
    voice: str = "en-US-AriaNeural",
    rate: str = "+0%",
) -> Path:
    """Convert text to MP3 audio using edge-tts.

    Args:
        text: Plain text to convert.
        output_path: Where to save the MP3 file.
        voice: edge-tts voice name.
        rate: Speech rate adjustment (e.g., "+10%", "-5%").

    Returns:
        Path to the generated audio file.
    """
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(output_path))
    return output_path


def prepare_text_for_tts(
    markdown: str,
    title: str,
    authors: list[str],
    source_label: str = "paper",
) -> str:
    """Prepare markdown summary for TTS consumption.

    Strips markdown formatting, adds an intro line, and cleans up
    text for natural speech. Converts the full summary verbatim.
    """
    # Build intro
    if len(authors) > 3:
        author_str = f"{authors[0]}, {authors[1]}, {authors[2]}, and others"
    else:
        author_str = ", ".join(authors)

    intro = f"This is a summary of the {source_label}: {title}, by {author_str}.\n\n"

    text = markdown

    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Remove bold/italic markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)

    # Remove markdown links, keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove code blocks (before inline code to avoid backtick conflicts)
    text = re.sub(r"```[\s\S]*?```", "", text)

    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Replace bullet points with natural speech
    text = re.sub(r"^\s*[-*+]\s+", "  ", text, flags=re.MULTILINE)

    # Remove LaTeX math
    text = re.sub(r"\$\$[\s\S]*?\$\$", " (equation omitted) ", text)
    text = re.sub(r"\$([^$]+)\$", r"\1", text)

    # Clean up citations like (Section 3, p.5)
    text = re.sub(r"\((?:Section|§)\s*[\d.]+,?\s*p\.?\s*\d+\)", "", text)

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    return intro + text.strip()


async def list_available_voices(language: str = "en") -> list[dict]:
    """List available edge-tts voices for a language."""
    voices = await edge_tts.list_voices()
    return [v for v in voices if v["Locale"].startswith(language)]
