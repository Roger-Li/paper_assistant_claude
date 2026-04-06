"""Shared helpers for fixture-backed tests."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from paper_assistant.models import PaperMetadata

HF_PAPERS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hf_papers"


def load_hf_metadata_payload(arxiv_id: str) -> dict[str, object]:
    return json.loads(
        (HF_PAPERS_FIXTURES_DIR / f"{arxiv_id}.metadata.json").read_text(encoding="utf-8")
    )


def load_hf_metadata_fixture(arxiv_id: str) -> PaperMetadata:
    payload = load_hf_metadata_payload(arxiv_id)
    return PaperMetadata(
        arxiv_id=payload["id"],
        arxiv_url=f"https://arxiv.org/abs/{payload['id']}",
        pdf_url=f"https://arxiv.org/pdf/{payload['id']}",
        title=payload["title"],
        authors=[author["name"] for author in payload["authors"]],
        abstract=payload["summary"],
        published=datetime.fromisoformat(payload["publishedAt"].replace("Z", "+00:00")),
        categories=[],
    )


def load_hf_markdown_fixture(arxiv_id: str) -> str:
    return (HF_PAPERS_FIXTURES_DIR / f"{arxiv_id}.markdown.md").read_text(encoding="utf-8")
