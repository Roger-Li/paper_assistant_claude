"""Tests for the shared import pipeline and skill-oriented CLI commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from unittest.mock import AsyncMock, Mock, patch

import pytest
from click.testing import CliRunner

from paper_assistant.cli import main
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.pipeline import DuplicatePaperError, ImportResult, import_paper_summary
from paper_assistant.storage import StorageManager


@pytest.fixture
def config(tmp_data_dir):
    cfg = Config(
        anthropic_api_key="test-key",
        data_dir=tmp_data_dir,
        icloud_sync=False,
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def storage(config):
    return StorageManager(config)


def _metadata(
    paper_id: str = "2503.10291",
    title: str = "Paper Title",
) -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=paper_id,
        arxiv_url=f"https://arxiv.org/abs/{paper_id}",
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        title=title,
        authors=["Alice", "Bob"],
        abstract="Test abstract",
    )


def _existing_paper(metadata: PaperMetadata) -> Paper:
    now = datetime.now(timezone.utc)
    return Paper(
        metadata=metadata,
        date_added=now - timedelta(days=7),
        status=ProcessingStatus.ERROR,
        tags=["ml"],
        reading_status=ReadingStatus.READ,
        local_modified_at=now - timedelta(days=1),
        notion_modified_at=now - timedelta(days=3),
        last_synced_at=now - timedelta(days=2),
        archived_at=now - timedelta(hours=12),
        notion_page_id="notion-page-123",
        summary_path="papers/old-summary.md",
        audio_path="audio/old-audio.mp3",
        model_used="manual",
        token_count=99,
        error_message="old error",
    )


async def _write_audio(_text: str, output_path: Path, _voice: str, _rate: str) -> None:
    output_path.write_bytes(b"fake-audio")


@pytest.mark.asyncio
async def test_import_happy_path(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.text_to_speech", new=AsyncMock(side_effect=_write_audio)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
        )

    paper = storage.get_paper(metadata.paper_id)
    assert result.paper_id == metadata.paper_id
    assert result.title == metadata.title
    assert result.summary_path.exists()
    assert result.audio_path == config.audio_dir / f"{metadata.paper_id}.mp3"
    assert paper is not None
    assert paper.status == ProcessingStatus.COMPLETE
    assert paper.audio_path == f"audio/{metadata.paper_id}.mp3"
    assert paper.model_used == "claude-code"
    assert result.warnings == []


@pytest.mark.asyncio
async def test_import_model_provenance(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.model_used == "claude-code"


@pytest.mark.asyncio
async def test_import_model_with_version(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="codex/gpt-5.4",
            skip_audio=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.model_used == "codex/gpt-5.4"


@pytest.mark.asyncio
async def test_import_refetch_after_save(config, storage):
    metadata = _metadata()

    async def assert_refetched(*, paper: Paper, **_kwargs) -> None:
        assert paper.summary_path is not None

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch(
            "paper_assistant.pipeline._generate_audio_for_import",
            new=AsyncMock(side_effect=assert_refetched),
        ),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
        )


@pytest.mark.asyncio
async def test_import_duplicate_no_force(config, storage):
    metadata = _metadata()
    storage.add_paper(_existing_paper(metadata))

    with patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)):
        with pytest.raises(DuplicatePaperError):
            await import_paper_summary(
                config=config,
                storage=storage,
                url=metadata.arxiv_url or metadata.paper_id,
                markdown="# One-Pager\nImported body",
                model="claude-code",
            )


@pytest.mark.asyncio
async def test_import_duplicate_with_force(config, storage):
    metadata = _metadata()
    existing = _existing_paper(metadata)
    storage.add_paper(existing)

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="claude-code",
            force=True,
            skip_audio=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.date_added == existing.date_added
    assert paper.reading_status == existing.reading_status
    assert paper.notion_page_id == existing.notion_page_id
    assert paper.notion_modified_at == existing.notion_modified_at
    assert paper.last_synced_at == existing.last_synced_at
    assert paper.archived_at == existing.archived_at
    assert paper.error_message is None
    assert paper.model_used == "claude-code"
    assert result.summary_path.exists()
    assert "Updated body" in result.summary_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_force_merge_tags_union(config, storage):
    metadata = _metadata()
    storage.add_paper(_existing_paper(metadata))

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="claude-code",
            tags=["rl", "ml"],
            force=True,
            skip_audio=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.tags == ["ml", "rl"]


@pytest.mark.asyncio
async def test_force_merge_audio_keep_on_skip(config, storage):
    metadata = _metadata()
    existing = _existing_paper(metadata)
    storage.add_paper(existing)

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        text_to_speech = AsyncMock()
        with patch("paper_assistant.pipeline.text_to_speech", new=text_to_speech):
            result = await import_paper_summary(
                config=config,
                storage=storage,
                url=metadata.arxiv_url or metadata.paper_id,
                markdown="# One-Pager\nUpdated body",
                model="claude-code",
                force=True,
                skip_audio=True,
            )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.audio_path == existing.audio_path
    assert result.audio_path == config.data_dir / existing.audio_path
    text_to_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_merge_audio_replace(config, storage):
    metadata = _metadata()
    existing = _existing_paper(metadata)
    storage.add_paper(existing)

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.text_to_speech", new=AsyncMock(side_effect=_write_audio)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="claude-code",
            force=True,
            skip_audio=False,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.audio_path == f"audio/{metadata.paper_id}.mp3"


@pytest.mark.asyncio
async def test_import_sync_notion_called(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.pipeline.run_notion_sync", new=AsyncMock()) as sync_notion,
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
            sync_notion=True,
        )

    assert result.notion_synced is True
    assert result.notion_error is None
    sync_notion.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_sync_notion_skipped(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.pipeline.run_notion_sync", new=AsyncMock()) as sync_notion,
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
            sync_notion=False,
        )

    assert result.notion_synced is False
    assert result.notion_error is None
    sync_notion.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_notion_failure_nonfatal(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch(
            "paper_assistant.pipeline.run_notion_sync",
            new=AsyncMock(side_effect=RuntimeError("sync boom")),
        ),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
            sync_notion=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert result.notion_synced is False
    assert result.notion_error == "sync boom"


def _import_result(tmp_path: Path) -> ImportResult:
    return ImportResult(
        paper_id="2503.10291",
        title="Paper Title",
        summary_path=tmp_path / "papers" / "summary.md",
        audio_path=tmp_path / "audio" / "2503.10291.mp3",
        model_used="codex/gpt-5.4",
        notion_synced=True,
        notion_error=None,
        warnings=[],
    )


class TestSkillImportCli:
    def test_skill_import_cli_json(self, tmp_path):
        runner = CliRunner()
        summary_path = tmp_path / "summary.md"
        summary_path.write_text("# One-Pager\nBody", encoding="utf-8")
        result_payload = _import_result(tmp_path)

        with patch(
            "paper_assistant.cli._run_import_pipeline",
            new=AsyncMock(return_value=result_payload),
        ):
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(summary_path),
                    "--model",
                    "codex",
                    "--model-version",
                    "gpt-5.4",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["paper_id"] == "2503.10291"
        assert payload["model_used"] == "codex/gpt-5.4"
        assert payload["notion_synced"] is True

    def test_skill_import_cli_cleanup_success(self, tmp_path):
        runner = CliRunner()
        summary_path = tmp_path / "summary.md"
        pdf_path = tmp_path / "paper.pdf"
        summary_path.write_text("# One-Pager\nBody", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "paper_assistant.cli._run_import_pipeline",
            new=AsyncMock(return_value=_import_result(tmp_path)),
        ):
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(summary_path),
                    "--model",
                    "codex",
                    "--cleanup-file",
                    str(summary_path),
                    "--cleanup-file",
                    str(pdf_path),
                ],
            )

        assert result.exit_code == 0
        assert not summary_path.exists()
        assert not pdf_path.exists()

    def test_skill_import_cli_cleanup_failure(self, tmp_path):
        runner = CliRunner()
        summary_path = tmp_path / "summary.md"
        pdf_path = tmp_path / "paper.pdf"
        summary_path.write_text("# One-Pager\nBody", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "paper_assistant.cli._run_import_pipeline",
            new=AsyncMock(side_effect=RuntimeError("import boom")),
        ):
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(summary_path),
                    "--model",
                    "codex",
                    "--cleanup-file",
                    str(summary_path),
                    "--cleanup-file",
                    str(pdf_path),
                ],
            )

        assert result.exit_code != 0
        assert "Artifacts preserved for manual recovery" in result.output
        assert summary_path.name in result.output
        assert pdf_path.name in result.output
        assert summary_path.exists()
        assert pdf_path.exists()

    def test_skill_import_cli_cleanup_rejects_nontmp(self):
        runner = CliRunner()
        repo_file = Path("cleanup-nontmp.txt")
        repo_file.write_text("not temp", encoding="utf-8")

        try:
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(repo_file),
                    "--model",
                    "codex",
                    "--cleanup-file",
                    str(repo_file),
                ],
            )
        finally:
            if repo_file.exists():
                repo_file.unlink()

        assert result.exit_code != 0
        assert "must be under" in result.output

    def test_skill_import_cli_cleanup_accepts_repo_artifacts(self):
        runner = CliRunner()
        artifact_dir = Path(".artifacts/test-skill-import")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary_path = artifact_dir / "summary.md"
        pdf_path = artifact_dir / "paper.pdf"
        summary_path.write_text("# One-Pager\nBody", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF")

        try:
            with patch(
                "paper_assistant.cli._run_import_pipeline",
                new=AsyncMock(return_value=_import_result(artifact_dir)),
            ):
                result = runner.invoke(
                    main,
                    [
                        "skill-import",
                        "https://arxiv.org/abs/2503.10291",
                        "--file",
                        str(summary_path),
                        "--model",
                        "codex",
                        "--cleanup-file",
                        str(summary_path),
                        "--cleanup-file",
                        str(pdf_path),
                    ],
                )
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)

        assert result.exit_code == 0


class TestNotionPreflightCli:
    def test_notion_preflight_cli(self, tmp_path):
        runner = CliRunner()
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "PAPER_ASSIST_DATA_DIR": str(tmp_path),
        }

        with patch("paper_assistant.notion.preflight_notion", new=AsyncMock()):
            result = runner.invoke(main, ["notion-preflight"], env=env)

        assert result.exit_code == 0
        assert "Notion preflight passed." in result.output
