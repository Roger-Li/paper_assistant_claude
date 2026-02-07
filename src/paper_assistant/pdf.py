"""PDF text extraction for LLM consumption."""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf4llm
import pymupdf


def extract_text_from_pdf(pdf_path: Path, max_pages: int = 100) -> str:
    """Extract text from a PDF as Markdown using pymupdf4llm.

    Handles academic paper layouts including two-column, tables,
    headers, and mathematical notation (best-effort).

    Args:
        pdf_path: Path to the PDF file.
        max_pages: Maximum pages to process.

    Returns:
        Markdown string of extracted text.
    """
    page_count = get_pdf_page_count(pdf_path)
    pages = list(range(min(page_count, max_pages)))

    md_text = pymupdf4llm.to_markdown(str(pdf_path), pages=pages)
    return md_text


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF."""
    doc = pymupdf.open(str(pdf_path))
    count = len(doc)
    doc.close()
    return count


def encode_pdf_base64(pdf_path: Path) -> str:
    """Encode a PDF file as base64 for Claude API document content type."""
    data = pdf_path.read_bytes()
    return base64.standard_b64encode(data).decode("ascii")
