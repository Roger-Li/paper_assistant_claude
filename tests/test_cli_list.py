"""Tests for ``paper-assist list`` JSON output."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from paper_assistant.cli import main
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, SourceType
from paper_assistant.storage import StorageManager
from paper_assistant.summarizer import SummarizationResult, format_summary_file


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "PAPER_ASSIST_DATA_DIR": str(tmp_path),
        "PAPER_ASSIST_ICLOUD_SYNC": "false",
    }


def _seed_papers(data_dir: Path) -> None:
    config = Config(
        anthropic_api_key="test-key",
        data_dir=data_dir,
        icloud_sync=False,
    )
    config.ensure_dirs()
    storage = StorageManager(config)

    arxiv_meta = PaperMetadata(
        arxiv_id="2503.10291",
        title="Sample Paper",
        authors=["Alice"],
        abstract="Abstract",
        arxiv_url="https://arxiv.org/abs/2503.10291",
        pdf_url="https://arxiv.org/pdf/2503.10291",
    )
    storage.add_paper(Paper(metadata=arxiv_meta, tags=["rl"]))
    result = SummarizationResult(
        full_markdown="# One-Pager\nBody",
        one_pager="Body",
        sections={"One-Pager": "Body"},
        model_used="manual",
    )
    storage.save_summary("2503.10291", format_summary_file(arxiv_meta, result))

    note_meta = PaperMetadata(
        source_type=SourceType.NOTE,
        source_slug="local-note",
        title="Local Note",
        authors=[],
    )
    storage.add_paper(Paper(metadata=note_meta, tags=["notes"]))


class TestListJson:
    def test_list_json_fields(self, tmp_path):
        _seed_papers(tmp_path)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--json"], env=_env(tmp_path))

        assert result.exit_code == 0
        entries = json.loads(result.output)
        by_id = {e["paper_id"]: e for e in entries}
        assert set(by_id) == {"2503.10291", "local-note"}

        arxiv_entry = by_id["2503.10291"]
        assert arxiv_entry["title"] == "Sample Paper"
        assert arxiv_entry["tags"] == ["rl"]
        assert arxiv_entry["source_type"] == "arxiv"
        assert arxiv_entry["arxiv_id"] == "2503.10291"
        assert arxiv_entry["has_audio"] is False
        # ISO-8601 date_added
        assert "T" in arxiv_entry["date_added"]
        # Absolute summary path under the data dir
        summary_path = Path(arxiv_entry["summary_path"])
        assert summary_path.is_absolute()
        assert summary_path.is_file()
        assert str(summary_path).startswith(str(tmp_path))

        note_entry = by_id["local-note"]
        assert note_entry["source_type"] == "note"
        assert note_entry["summary_path"] is None
        assert note_entry["arxiv_id"] is None

    def test_list_json_tag_filter(self, tmp_path):
        _seed_papers(tmp_path)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--tag", "notes", "--json"], env=_env(tmp_path))

        assert result.exit_code == 0
        entries = json.loads(result.output)
        assert [e["paper_id"] for e in entries] == ["local-note"]

    def test_list_json_empty_index(self, tmp_path):
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--json"], env=_env(tmp_path))

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_list_json_relative_data_dir_yields_absolute_paths(self, tmp_path, monkeypatch):
        """summary_path must stay absolute even with a relative PAPER_ASSIST_DATA_DIR."""
        monkeypatch.chdir(tmp_path)
        _seed_papers(tmp_path / "data")
        runner = CliRunner()
        env = _env(tmp_path)
        env["PAPER_ASSIST_DATA_DIR"] = "data"

        result = runner.invoke(main, ["list", "--json"], env=env)

        assert result.exit_code == 0
        entries = json.loads(result.output)
        by_id = {e["paper_id"]: e for e in entries}
        summary_path = Path(by_id["2503.10291"]["summary_path"])
        assert summary_path.is_absolute()
        assert summary_path.is_file()
