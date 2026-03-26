"""Tests for the ``paper-assist create`` command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from paper_assistant.cli import main
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, SourceType
from paper_assistant.pipeline import LocalEntryResult


def _make_outcome(paper_id: str = "reading-note", title: str = "Reading Note") -> LocalEntryResult:
    paper = Paper(
        metadata=PaperMetadata(
            source_type=SourceType.NOTE,
            source_slug=paper_id,
            title=title,
        ),
        status=ProcessingStatus.COMPLETE,
    )
    return LocalEntryResult(paper=paper, summary_path=Path(f"/tmp/{paper_id}.md"))


class TestCreateCommand:
    def test_create_from_file(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody from file", encoding="utf-8")
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "PAPER_ASSIST_DATA_DIR": str(tmp_path),
            "PAPER_ASSIST_ICLOUD_SYNC": "false",
        }

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(),
        ) as create_local_entry:
            result = runner.invoke(
                main,
                ["create", "--title", "Reading Note", "--file", str(markdown_path)],
                env=env,
            )

        assert result.exit_code == 0
        assert "Local note created successfully." in result.output
        kwargs = create_local_entry.await_args.kwargs
        assert kwargs["title"] == "Reading Note"
        assert kwargs["markdown"] == "# Note\nBody from file"

    def test_create_from_clipboard(self, tmp_path):
        runner = CliRunner()
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "PAPER_ASSIST_DATA_DIR": str(tmp_path),
            "PAPER_ASSIST_ICLOUD_SYNC": "false",
        }

        with (
            patch(
                "paper_assistant.pipeline.create_local_entry",
                new_callable=AsyncMock,
                return_value=_make_outcome(paper_id="clip-note", title="Clip Note"),
            ) as create_local_entry,
            patch(
                "paper_assistant.cli.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["pbpaste"],
                    returncode=0,
                    stdout="# Note\nBody from clipboard",
                    stderr="",
                ),
            ),
        ):
            result = runner.invoke(main, ["create", "--title", "Clip Note"], env=env)

        assert result.exit_code == 0
        kwargs = create_local_entry.await_args.kwargs
        assert kwargs["markdown"] == "# Note\nBody from clipboard"
