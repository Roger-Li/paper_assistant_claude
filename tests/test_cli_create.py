"""Tests for the ``paper-assist create`` command."""

from __future__ import annotations

import json
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


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "ANTHROPIC_API_KEY": "test-key",
        "PAPER_ASSIST_DATA_DIR": str(tmp_path),
        "PAPER_ASSIST_ICLOUD_SYNC": "false",
    }


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

    def test_create_empty_input_exits_nonzero(self, tmp_path):
        """Empty input must not exit 0 — JSON callers would parse empty stdout."""
        runner = CliRunner()
        markdown_path = tmp_path / "empty.md"
        markdown_path.write_text("   \n", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(),
        ) as create_local_entry:
            result = runner.invoke(
                main,
                ["create", "--title", "Reading Note", "--file", str(markdown_path), "--json"],
                env=_env(tmp_path),
            )

        assert result.exit_code != 0
        assert "No markdown content found." in result.output
        create_local_entry.assert_not_awaited()

    def test_create_with_script_file(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")
        script_path = tmp_path / "transcript.md"
        script_path.write_text("Narration script body.", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(),
        ) as create_local_entry:
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--script-file",
                    str(script_path),
                    "--no-script-fallback",
                ],
                env=_env(tmp_path),
            )

        assert result.exit_code == 0
        kwargs = create_local_entry.await_args.kwargs
        assert kwargs["provided_script_markdown"] == "Narration script body."
        assert kwargs["skip_script_generation"] is True

    def test_create_empty_script_file_fails(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")
        script_path = tmp_path / "transcript.md"
        script_path.write_text("   \n", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(),
        ) as create_local_entry:
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--script-file",
                    str(script_path),
                ],
                env=_env(tmp_path),
            )

        assert result.exit_code != 0
        assert "--script-file was empty." in result.output
        create_local_entry.assert_not_awaited()

    def test_create_json_output_uses_post_dedupe_paper_id(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(paper_id="reading-note-2"),
        ):
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--json",
                ],
                env=_env(tmp_path),
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["paper_id"] == "reading-note-2"
        assert payload["title"] == "Reading Note"
        assert payload["summary_path"] == "/tmp/reading-note-2.md"
        assert payload["transcript_path"] is None
        assert payload["audio_path"] is None
        assert payload["warnings"] == []

    def test_create_json_output_stays_parseable_with_icloud_sync(self, tmp_path):
        """iCloud copy must not print to stdout in JSON mode (it would corrupt the JSON)."""
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")

        outcome = _make_outcome()
        outcome.paper.audio_path = "audio/reading-note.mp3"  # file does not exist

        env = _env(tmp_path)
        env["PAPER_ASSIST_ICLOUD_SYNC"] = "true"
        env["PAPER_ASSIST_ICLOUD_DIR"] = str(tmp_path / "icloud")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=outcome,
        ):
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--json",
                ],
                env=env,
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)  # would raise if iCloud printed to stdout
        assert payload["paper_id"] == "reading-note"
        assert any(w.startswith("iCloud copy failed:") for w in payload["warnings"])

    def test_create_cleanup_file_removed_on_success(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")
        cleanup_path = tmp_path / "synthesis.md"
        cleanup_path.write_text("artifact", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            return_value=_make_outcome(),
        ):
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--cleanup-file",
                    str(cleanup_path),
                ],
                env=_env(tmp_path),
            )

        assert result.exit_code == 0
        assert not cleanup_path.exists()

    def test_create_failure_preserves_artifacts_and_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        markdown_path = tmp_path / "note.md"
        markdown_path.write_text("# Note\nBody", encoding="utf-8")
        cleanup_path = tmp_path / "synthesis.md"
        cleanup_path.write_text("artifact", encoding="utf-8")

        with patch(
            "paper_assistant.pipeline.create_local_entry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("create boom"),
        ):
            result = runner.invoke(
                main,
                [
                    "create",
                    "--title",
                    "Reading Note",
                    "--file",
                    str(markdown_path),
                    "--cleanup-file",
                    str(cleanup_path),
                ],
                env=_env(tmp_path),
            )

        assert result.exit_code != 0
        assert "create boom" in result.output
        assert "Artifacts preserved for manual recovery:" in result.output
        assert cleanup_path.exists()
