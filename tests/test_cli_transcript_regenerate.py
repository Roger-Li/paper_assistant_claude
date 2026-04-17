"""Tests for `paper-assist transcript regenerate` CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from paper_assistant.cli import main
from paper_assistant.pipeline import TranscriptRegenerateResult


def _result(tmp_path: Path) -> TranscriptRegenerateResult:
    return TranscriptRegenerateResult(
        paper_id="2503.10291",
        title="Test Paper",
        transcript_path=tmp_path / "transcripts/2503.10291.md",
        audio_path=tmp_path / "audio/2503.10291.mp3",
        script_model="claude-haiku-test",
        backend_used="mlx",
        warnings=[],
    )


def test_transcript_regenerate_invokes_pipeline(tmp_path):
    runner = CliRunner()
    env = {"ANTHROPIC_API_KEY": "k", "PAPER_ASSIST_DATA_DIR": str(tmp_path)}

    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(tmp_path)),
    ) as regen:
        result = runner.invoke(main, ["transcript", "regenerate", "2503.10291"], env=env)

    assert result.exit_code == 0, result.output
    assert "Test Paper" in result.output
    assert "mlx" in result.output
    regen.assert_awaited_once()
    assert regen.await_args.kwargs["paper_id"] == "2503.10291"
    assert regen.await_args.kwargs["provided_script_markdown"] is None
    assert regen.await_args.kwargs["script_model_override"] is None


def test_transcript_regenerate_passes_model_override(tmp_path):
    runner = CliRunner()
    env = {"ANTHROPIC_API_KEY": "k", "PAPER_ASSIST_DATA_DIR": str(tmp_path)}

    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(tmp_path)),
    ) as regen:
        result = runner.invoke(
            main,
            ["transcript", "regenerate", "2503.10291", "--model", "claude-custom"],
            env=env,
        )

    assert result.exit_code == 0
    assert regen.await_args.kwargs["script_model_override"] == "claude-custom"


def test_transcript_regenerate_loads_script_file(tmp_path):
    runner = CliRunner()
    env = {"ANTHROPIC_API_KEY": "k", "PAPER_ASSIST_DATA_DIR": str(tmp_path)}

    script_file = tmp_path / "custom_script.md"
    script_file.write_text("A curated narration.", encoding="utf-8")

    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(return_value=_result(tmp_path)),
    ) as regen:
        result = runner.invoke(
            main,
            [
                "transcript",
                "regenerate",
                "2503.10291",
                "--script-file",
                str(script_file),
            ],
            env=env,
        )

    assert result.exit_code == 0
    assert regen.await_args.kwargs["provided_script_markdown"] == "A curated narration."


def test_transcript_regenerate_missing_paper_errors(tmp_path):
    runner = CliRunner()
    env = {"ANTHROPIC_API_KEY": "k", "PAPER_ASSIST_DATA_DIR": str(tmp_path)}

    with patch(
        "paper_assistant.pipeline.regenerate_transcript_and_audio",
        new=AsyncMock(side_effect=KeyError("missing")),
    ):
        result = runner.invoke(main, ["transcript", "regenerate", "nope"], env=env)

    assert result.exit_code != 0
    assert "not found" in result.output


def test_transcript_regenerate_empty_script_file_errors(tmp_path):
    runner = CliRunner()
    env = {"ANTHROPIC_API_KEY": "k", "PAPER_ASSIST_DATA_DIR": str(tmp_path)}

    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")

    result = runner.invoke(
        main,
        ["transcript", "regenerate", "2503.10291", "--script-file", str(empty)],
        env=env,
    )

    assert result.exit_code != 0
    assert "empty" in result.output.lower()


def test_tts_check_probes_backend(tmp_path):
    runner = CliRunner()
    env = {
        "ANTHROPIC_API_KEY": "k",
        "PAPER_ASSIST_DATA_DIR": str(tmp_path),
        "PAPER_ASSIST_TTS_BACKEND": "edge",  # avoid MLX network probe in tests
    }

    async def _fake_synthesize(self, text: str, out_path: Path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"probe-mp3")
        return out_path

    with patch("paper_assistant.tts.EdgeTTSBackend.synthesize", new=_fake_synthesize):
        result = runner.invoke(main, ["tts", "check"], env=env)

    assert result.exit_code == 0, result.output
    assert "TTS backend" in result.output
    assert "edge" in result.output
