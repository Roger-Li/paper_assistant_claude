"""Tests for `paper-assist visuals` CLI helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from paper_assistant.cli import main


def test_visuals_inject_overwrites_summary_with_enriched_markdown(tmp_path):
    runner = CliRunner()
    summary_file = tmp_path / "summary.md"
    source_file = tmp_path / "paper.md"
    summary_file.write_text("See Figure 1.", encoding="utf-8")
    source_file.write_text("Figure 1:Architecture.", encoding="utf-8")

    with patch(
        "paper_assistant.visuals.enrich_summary_with_visuals_async",
        new=AsyncMock(return_value="See Figure 1.\n\n![Figure 1](https://arxiv.org/html/x/x1.png)"),
    ) as enrich:
        result = runner.invoke(
            main,
            [
                "visuals",
                "inject",
                "--summary-file",
                str(summary_file),
                "--source-file",
                str(source_file),
                "--arxiv-id",
                "2605.28814",
            ],
            env={"PAPER_ASSIST_DATA_DIR": str(tmp_path)},
        )

    assert result.exit_code == 0, result.output
    assert "Injected verified visuals" in result.output
    assert "https://arxiv.org/html/x/x1.png" in summary_file.read_text(encoding="utf-8")
    enrich.assert_awaited_once()
    assert enrich.await_args.kwargs["arxiv_id"] == "2605.28814"
