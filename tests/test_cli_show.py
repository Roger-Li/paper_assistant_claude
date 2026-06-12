"""Tests for ``paper-assist show --body`` (normalized summary export)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from paper_assistant.cli import main
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata
from paper_assistant.storage import StorageManager
from paper_assistant.summarizer import SummarizationResult, format_summary_file


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "PAPER_ASSIST_DATA_DIR": str(tmp_path),
        "PAPER_ASSIST_ICLOUD_SYNC": "false",
    }


def _seed_summary(tmp_path: Path, body: str) -> None:
    config = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_path,
        icloud_sync=False,
    )
    config.ensure_dirs()
    storage = StorageManager(config)
    metadata = PaperMetadata(
        arxiv_id="2503.10291",
        title="Sample Paper",
        authors=["Alice"],
        abstract="Abstract",
        arxiv_url="https://arxiv.org/abs/2503.10291",
        pdf_url="https://arxiv.org/pdf/2503.10291",
    )
    storage.add_paper(Paper(metadata=metadata))
    result = SummarizationResult(
        full_markdown=body,
        one_pager=body,
        sections={"One-Pager": body},
        model_used="manual",
    )
    storage.save_summary("2503.10291", format_summary_file(metadata, result))


class TestShowBody:
    def test_body_strips_wrapper_via_normalize(self, tmp_path):
        body = "# One-Pager\nPlain body text with **bold**."
        _seed_summary(tmp_path, body)
        runner = CliRunner()

        result = runner.invoke(main, ["show", "2503.10291", "--body"], env=_env(tmp_path))

        assert result.exit_code == 0
        assert result.output.strip() == body
        # No YAML front matter or generated header leaks through.
        assert not result.output.startswith("---")
        assert "arxiv_id:" not in result.output

    def test_body_missing_paper_exits_nonzero(self, tmp_path):
        runner = CliRunner()

        result = runner.invoke(main, ["show", "nope", "--body"], env=_env(tmp_path))

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_body_no_summary_exits_nonzero(self, tmp_path):
        config = Config(
            anthropic_api_key="test-key",
            data_dir=tmp_path,
            icloud_sync=False,
        )
        config.ensure_dirs()
        StorageManager(config).add_paper(
            Paper(
                metadata=PaperMetadata(
                    arxiv_id="2503.10291",
                    title="Sample Paper",
                    authors=["Alice"],
                    abstract="Abstract",
                )
            )
        )
        runner = CliRunner()

        result = runner.invoke(main, ["show", "2503.10291", "--body"], env=_env(tmp_path))

        assert result.exit_code != 0
        assert "no summary" in result.output
