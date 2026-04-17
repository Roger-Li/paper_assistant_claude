"""Tests for the shared import pipeline and skill-oriented CLI commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from click.testing import CliRunner

from paper_assistant.arxiv import ArxivRateLimitError, PaperNotFoundError
from paper_assistant.cli import _normalize_skill_markdown, main
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.pipeline import DuplicatePaperError, ImportResult, import_paper_summary
from paper_assistant.storage import StorageManager
from tests.helpers import load_hf_metadata_fixture


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


async def _write_audio(_text: str, output_path: Path, _voice: str = "", _rate: str = "") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"fake-audio")


def _stub_render_audio_assets(regenerate_audio: bool = True):
    """Return a render_audio_assets replacement that writes a fake mp3 file."""
    from paper_assistant.audio_assets import AudioAssetsResult
    from paper_assistant.models import ProcessingStatus
    from paper_assistant.storage import make_audio_filename

    async def _stub(*, config, storage, paper, skip_audio, skip_transcript, **_kw):
        paper_id = paper.metadata.paper_id
        result = AudioAssetsResult()
        if skip_audio:
            fresh = storage.get_paper(paper_id) or paper
            if fresh.audio_path:
                result.audio_path = config.data_dir / fresh.audio_path
            if fresh.transcript_path:
                result.transcript_path = config.data_dir / fresh.transcript_path
            return result
        if not regenerate_audio:
            return result
        audio_path = config.audio_dir / make_audio_filename(paper_id)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-audio")
        fresh = storage.get_paper(paper_id) or paper
        fresh.audio_path = f"audio/{make_audio_filename(paper_id)}"
        fresh.status = ProcessingStatus.AUDIO_GENERATED
        storage.add_paper(fresh)
        result.audio_path = audio_path
        result.backend_used = "edge"
        return result

    return _stub


def _seed_existing_transcript_and_audio(
    config: Config,
    storage: StorageManager,
    paper: Paper,
    *,
    transcript_text: str = "Old narration",
    audio_bytes: bytes = b"old-audio",
) -> Paper:
    transcript_rel = f"transcripts/{paper.metadata.paper_id}.md"
    audio_rel = paper.audio_path or f"audio/{paper.metadata.paper_id}.mp3"

    transcript_file = config.data_dir / transcript_rel
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(transcript_text, encoding="utf-8")

    audio_file = config.data_dir / audio_rel
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(audio_bytes)

    paper.transcript_path = transcript_rel
    paper.audio_path = audio_rel
    storage.add_paper(paper)
    return storage.get_paper(paper.metadata.paper_id) or paper


@pytest.mark.asyncio
async def test_import_happy_path(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch(
            "paper_assistant.pipeline.render_audio_assets",
            new=AsyncMock(side_effect=_stub_render_audio_assets()),
        ),
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
async def test_import_prefers_hf_metadata_before_arxiv(config, storage):
    metadata = load_hf_metadata_fixture("2601.15621")

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.fetch_arxiv_metadata", new=AsyncMock()) as fetch_arxiv,
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
        )

    assert result.paper_id == "2601.15621"
    assert result.title == "Qwen3-TTS Technical Report"
    fetch_arxiv.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_falls_back_to_arxiv_metadata_when_hf_fails(config, storage):
    metadata = load_hf_metadata_fixture("2503.10291")

    with (
        patch(
            "paper_assistant.pipeline.fetch_hf_metadata",
            new=AsyncMock(side_effect=RuntimeError("hf down")),
        ),
        patch(
            "paper_assistant.pipeline.fetch_arxiv_metadata",
            new=AsyncMock(return_value=metadata),
        ) as fetch_arxiv,
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nImported body",
            model="claude-code",
            skip_audio=True,
        )

    paper = storage.get_paper("2503.10291")
    assert paper is not None
    assert result.title == "VisualPRM: An Effective Process Reward Model for Multimodal Reasoning"
    fetch_arxiv.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_falls_back_to_summary_derived_metadata(config, storage):
    markdown = (
        "# One-Pager\n\n"
        "*Fallback Paper, arXiv preprint (2026), Alice Example, Bob Example*\n\n"
        "This summary paragraph is enough to populate a fallback abstract.\n"
    )

    with (
        patch(
            "paper_assistant.pipeline.fetch_hf_metadata",
            new=AsyncMock(side_effect=httpx.ReadTimeout("hf down")),
        ),
        patch(
            "paper_assistant.pipeline.fetch_arxiv_metadata",
            new=AsyncMock(side_effect=ArxivRateLimitError(attempts=3, retry_after_seconds=45)),
        ),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url="https://arxiv.org/abs/2603.19835",
            markdown=markdown,
            model="claude-code",
            skip_audio=True,
        )

    paper = storage.get_paper("2603.19835")
    assert paper is not None
    assert result.paper_id == "2603.19835"
    assert result.title == "Fallback Paper"
    assert paper.metadata.authors == ["Alice Example", "Bob Example"]
    assert paper.metadata.abstract == "This summary paragraph is enough to populate a fallback abstract."


@pytest.mark.asyncio
async def test_import_does_not_guess_metadata_when_paper_is_missing(config, storage):
    request = httpx.Request("GET", "https://huggingface.co/api/papers/9999.99999")
    response = httpx.Response(404, request=request)
    hf_404 = httpx.HTTPStatusError("not found", request=request, response=response)

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(side_effect=hf_404)),
        patch(
            "paper_assistant.pipeline.fetch_arxiv_metadata",
            new=AsyncMock(side_effect=PaperNotFoundError("No paper found for arXiv ID: 9999.99999")),
        ),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        with pytest.raises(PaperNotFoundError):
            await import_paper_summary(
                config=config,
                storage=storage,
                url="9999.99999",
                markdown="# One-Pager\nGuessed body",
                model="claude-code",
                skip_audio=True,
            )

    assert storage.get_paper("9999.99999") is None


@pytest.mark.asyncio
async def test_import_force_reuses_existing_metadata_on_transient_remote_failures(config, storage):
    metadata = load_hf_metadata_fixture("2601.15621")
    existing = _existing_paper(metadata)
    storage.add_paper(existing)

    markdown = (
        "# One-Pager\n\n"
        "*Completely Different Guess, arXiv preprint (2026), Wrong Author*\n\n"
        "This should never replace the verified metadata.\n"
    )

    with (
        patch(
            "paper_assistant.pipeline.fetch_hf_metadata",
            new=AsyncMock(side_effect=httpx.ReadTimeout("hf timeout")),
        ),
        patch(
            "paper_assistant.pipeline.fetch_arxiv_metadata",
            new=AsyncMock(side_effect=ArxivRateLimitError(attempts=3, retry_after_seconds=45)),
        ),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url="https://huggingface.co/papers/2601.15621",
            markdown=markdown,
            model="claude-code",
            skip_audio=True,
            force=True,
        )

    paper = storage.get_paper("2601.15621")
    assert paper is not None
    assert result.title == metadata.title
    assert paper.metadata.title == metadata.title
    assert paper.metadata.authors == metadata.authors
    assert paper.metadata.abstract == metadata.abstract


@pytest.mark.asyncio
async def test_import_model_provenance(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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

    from paper_assistant.audio_assets import AudioAssetsResult

    async def assert_refetched(*, paper: Paper, **_kwargs) -> AudioAssetsResult:
        assert paper.summary_path is not None
        return AudioAssetsResult()

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch(
            "paper_assistant.pipeline.render_audio_assets",
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

    with patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)):
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
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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

    stub = _stub_render_audio_assets()
    render_mock = AsyncMock(side_effect=stub)

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.pipeline.render_audio_assets", new=render_mock),
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
    assert paper.audio_path == existing.audio_path
    assert result.audio_path == config.data_dir / existing.audio_path
    render_mock.assert_awaited_once()
    assert render_mock.await_args.kwargs["skip_audio"] is True


@pytest.mark.asyncio
async def test_force_merge_audio_replace(config, storage):
    metadata = _metadata()
    existing = _existing_paper(metadata)
    storage.add_paper(existing)

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch(
            "paper_assistant.pipeline.render_audio_assets",
            new=AsyncMock(side_effect=_stub_render_audio_assets()),
        ),
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
async def test_force_import_with_script_file_and_no_fallback_skips_api_generation(config, storage):
    metadata = _metadata()
    existing = _seed_existing_transcript_and_audio(
        config,
        storage,
        _existing_paper(metadata),
    )

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_assets._try_generate_script",
            new_callable=AsyncMock,
        ) as try_script,
    ):
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _write_audio
        get_backend.return_value = fake

        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="codex/gpt-5.4",
            force=True,
            provided_script_markdown="Fresh scripted narration.",
            skip_script_generation=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.transcript_path == f"transcripts/{metadata.paper_id}.md"
    assert result.transcript_path == config.transcripts_dir / f"{metadata.paper_id}.md"
    assert result.transcript_path.read_text(encoding="utf-8") == "Fresh scripted narration."
    assert result.audio_path == config.audio_dir / f"{metadata.paper_id}.mp3"
    try_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_import_skip_audio_preserves_assets_even_with_no_fallback(config, storage):
    metadata = _metadata()
    existing = _seed_existing_transcript_and_audio(
        config,
        storage,
        _existing_paper(metadata),
    )

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_assets._try_generate_script",
            new_callable=AsyncMock,
        ) as try_script,
    ):
        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="codex/gpt-5.4",
            force=True,
            skip_audio=True,
            skip_script_generation=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.transcript_path == existing.transcript_path
    assert paper.audio_path == existing.audio_path
    assert result.transcript_path == config.data_dir / existing.transcript_path
    assert result.audio_path == config.data_dir / existing.audio_path
    get_backend.assert_not_called()
    try_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_import_no_fallback_without_script_clears_transcript_but_keeps_audio_path_fresh(
    config, storage
):
    metadata = _metadata()
    _seed_existing_transcript_and_audio(
        config,
        storage,
        _existing_paper(metadata),
    )

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
        patch("paper_assistant.pipeline.generate_feed", new=Mock()),
        patch("paper_assistant.audio_assets.get_tts_backend") as get_backend,
        patch(
            "paper_assistant.audio_assets._try_generate_script",
            new_callable=AsyncMock,
        ) as try_script,
    ):
        fake = AsyncMock()
        fake.name = "mlx"
        fake.synthesize.side_effect = _write_audio
        get_backend.return_value = fake

        result = await import_paper_summary(
            config=config,
            storage=storage,
            url=metadata.arxiv_url or metadata.paper_id,
            markdown="# One-Pager\nUpdated body",
            model="codex/gpt-5.4",
            force=True,
            skip_script_generation=True,
        )

    paper = storage.get_paper(metadata.paper_id)
    assert paper is not None
    assert paper.transcript_path is None
    assert result.transcript_path is None
    assert result.audio_path == config.audio_dir / f"{metadata.paper_id}.mp3"
    assert any("Skipped narration script generation" in w for w in result.warnings)
    try_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_sync_notion_called(config, storage):
    metadata = _metadata()

    with (
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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
        patch("paper_assistant.pipeline.fetch_hf_metadata", new=AsyncMock(return_value=metadata)),
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
    def test_normalize_skill_markdown_unwraps_agent_paragraphs(self):
        wrapped = (
            "# One-Pager\n\n"
            "This paper argues that many \"Aha moment\" behaviors in LLMs are better\n"
            "explained by how models externalize uncertainty than by the presence of\n"
            "surface markers such as \"Wait\" alone.\n\n"
            "- Introduces a closed-world, information-theoretic view of reasoning as\n"
            "  self-conditioning over a target variable `Y`.\n\n"
            "> **TL;DR:** Good reasoning is not just step execution; it is uncertainty\n"
            "> made explicit early enough to steer future computation.\n"
        )

        normalized = _normalize_skill_markdown(wrapped)

        assert (
            'This paper argues that many "Aha moment" behaviors in LLMs are better '
            'explained by how models externalize uncertainty than by the presence of '
            'surface markers such as "Wait" alone.'
        ) in normalized
        assert (
            "- Introduces a closed-world, information-theoretic view of reasoning as "
            "self-conditioning over a target variable `Y`."
        ) in normalized
        assert (
            "> **TL;DR:** Good reasoning is not just step execution; it is uncertainty "
            "made explicit early enough to steer future computation."
        ) in normalized

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

    def test_skill_import_cli_normalizes_hard_wrapped_markdown(self, tmp_path):
        runner = CliRunner()
        summary_path = tmp_path / "summary.md"
        summary_path.write_text(
            "# One-Pager\n\n"
            "This paper argues that many \"Aha moment\" behaviors in LLMs are better\n"
            "explained by how models externalize uncertainty than by the presence of\n"
            "surface markers such as \"Wait\" alone.\n",
            encoding="utf-8",
        )

        with patch(
            "paper_assistant.cli._run_import_pipeline",
            new=AsyncMock(return_value=_import_result(tmp_path)),
        ) as run_import:
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(summary_path),
                    "--model",
                    "codex",
                ],
            )

        assert result.exit_code == 0
        forwarded_markdown = run_import.await_args.kwargs["markdown"]
        assert "\nexplained by how models externalize uncertainty" not in forwarded_markdown
        assert (
            'This paper argues that many "Aha moment" behaviors in LLMs are better '
            'explained by how models externalize uncertainty than by the presence of '
            'surface markers such as "Wait" alone.'
        ) in forwarded_markdown

    def test_skill_import_cli_no_script_fallback_plumbed(self, tmp_path):
        runner = CliRunner()
        summary_path = tmp_path / "summary.md"
        summary_path.write_text("# One-Pager\nBody", encoding="utf-8")

        with patch(
            "paper_assistant.cli._run_import_pipeline",
            new=AsyncMock(return_value=_import_result(tmp_path)),
        ) as run_import:
            result = runner.invoke(
                main,
                [
                    "skill-import",
                    "https://arxiv.org/abs/2503.10291",
                    "--file",
                    str(summary_path),
                    "--model",
                    "codex",
                    "--no-script-fallback",
                ],
            )

        assert result.exit_code == 0
        assert run_import.await_args.kwargs["skip_script_generation"] is True

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
