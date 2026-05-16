"""Tests for portable bundle import/export."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from paper_assistant.bundle import export_bundle, import_bundle
from paper_assistant.cli import main
from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus
from paper_assistant.storage import StorageManager


def _config(data_dir: Path) -> Config:
    cfg = Config(anthropic_api_key="test-key", data_dir=data_dir, icloud_sync=False)
    cfg.ensure_dirs()
    return cfg


def _paper(
    paper_id: str = "2503.10291",
    *,
    title: str = "VisualPRM: An Effective Process Reward Model",
    tags: list[str] | None = None,
    local_modified_at: datetime | None = None,
) -> Paper:
    metadata = PaperMetadata(
        arxiv_id=paper_id,
        arxiv_url=f"https://arxiv.org/abs/{paper_id}",
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        title=title,
        authors=["Alice", "Bob"],
        abstract="We propose...",
        published=datetime(2025, 3, 13, tzinfo=timezone.utc),
        categories=["cs.LG"],
    )
    return Paper(
        metadata=metadata,
        tags=tags or [],
        status=ProcessingStatus.COMPLETE,
        local_modified_at=local_modified_at or datetime(2025, 3, 14, tzinfo=timezone.utc),
        model_used="claude-test",
    )


def _add_paper_with_assets(config: Config, paper: Paper) -> Paper:
    storage = StorageManager(config)
    paper_id = paper.metadata.paper_id
    storage.add_paper(paper)
    storage.save_summary(paper_id, f"# {paper.metadata.title}\n\nSummary for {paper_id}")
    storage.save_transcript(paper_id, f"Narration for {paper_id}")
    storage.save_audio(paper_id, b"fake mp3")

    fresh = storage.get_paper(paper_id)
    assert fresh is not None
    pdf_path = config.pdfs_dir / f"{paper_id}.pdf"
    pdf_path.write_bytes(b"%PDF")
    fresh.pdf_path = f"pdfs/{paper_id}.pdf"
    fresh.local_modified_at = paper.local_modified_at
    storage.add_paper(fresh)
    final = storage.get_paper(paper_id)
    assert final is not None
    return final


def test_export_bundle_strips_notion_metadata_and_includes_assets(tmp_path):
    config = _config(tmp_path)
    storage = StorageManager(config)
    paper = _paper(tags=["rl"])
    paper.notion_page_id = "notion-page-123"
    paper.notion_modified_at = datetime(2025, 3, 15, tzinfo=timezone.utc)
    paper.last_synced_at = datetime(2025, 3, 16, tzinfo=timezone.utc)
    _add_paper_with_assets(config, paper)

    bundle_path = tmp_path / "library.zip"
    report = export_bundle(config, storage, bundle_path)

    assert report.exported_papers == 1
    assert report.exported_files == 4
    with zipfile.ZipFile(bundle_path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        exported = manifest["papers"]["2503.10291"]
        assert "notion_page_id" not in exported
        assert "notion_modified_at" not in exported
        assert "last_synced_at" not in exported
        assert "files/papers/" in "\n".join(zf.namelist())
        assert "files/transcripts/2503.10291.md" in zf.namelist()
        assert "files/audio/2503.10291.mp3" in zf.namelist()
        assert "files/pdfs/2503.10291.pdf" in zf.namelist()


def test_import_bundle_skips_existing_by_default(tmp_path):
    source_config = _config(tmp_path / "source")
    source_storage = StorageManager(source_config)
    _add_paper_with_assets(source_config, _paper(tags=["incoming"]))
    bundle_path = tmp_path / "library.zip"
    export_bundle(source_config, source_storage, bundle_path)

    dest_config = _config(tmp_path / "dest")
    dest_storage = StorageManager(dest_config)
    existing = _paper(tags=["existing"])
    existing.summary_path = "papers/existing.md"
    dest_storage.add_paper(existing)
    (dest_config.papers_dir / "existing.md").write_text("old summary", encoding="utf-8")

    report = import_bundle(dest_config, dest_storage, bundle_path)

    assert report.created == 0
    assert report.updated == 0
    assert report.skipped == 1
    assert report.imported_files == 0

    untouched = dest_storage.get_paper("2503.10291")
    assert untouched is not None
    assert untouched.tags == ["existing"]
    assert untouched.summary_path == "papers/existing.md"
    assert (dest_config.papers_dir / "existing.md").read_text(encoding="utf-8") == "old summary"


def test_import_bundle_force_merges_existing_and_preserves_notion_metadata(tmp_path):
    source_config = _config(tmp_path / "source")
    source_storage = StorageManager(source_config)
    incoming_time = datetime(2025, 3, 20, tzinfo=timezone.utc)
    _add_paper_with_assets(
        source_config,
        _paper(tags=["incoming"], local_modified_at=incoming_time),
    )
    bundle_path = tmp_path / "library.zip"
    export_bundle(source_config, source_storage, bundle_path)

    dest_config = _config(tmp_path / "dest")
    dest_storage = StorageManager(dest_config)
    existing = _paper(tags=["existing"])
    existing.date_added = datetime(2025, 1, 1, tzinfo=timezone.utc)
    existing.reading_status = ReadingStatus.READ
    existing.notion_page_id = "notion-page-123"
    existing.notion_modified_at = datetime(2025, 3, 10, tzinfo=timezone.utc)
    existing.last_synced_at = datetime(2025, 3, 11, tzinfo=timezone.utc)
    existing.summary_path = "papers/existing.md"
    dest_storage.add_paper(existing)
    (dest_config.papers_dir / "existing.md").write_text("old summary", encoding="utf-8")

    report = import_bundle(dest_config, dest_storage, bundle_path, force=True)

    assert report.created == 0
    assert report.updated == 1
    assert report.skipped == 0
    assert report.imported_files == 4

    merged = dest_storage.get_paper("2503.10291")
    assert merged is not None
    assert merged.tags == ["existing", "incoming"]
    assert merged.date_added == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert merged.reading_status == ReadingStatus.READ
    assert merged.notion_page_id == "notion-page-123"
    assert merged.notion_modified_at == datetime(2025, 3, 10, tzinfo=timezone.utc)
    assert merged.last_synced_at == datetime(2025, 3, 11, tzinfo=timezone.utc)
    assert merged.local_modified_at == incoming_time
    assert (dest_config.data_dir / merged.summary_path).read_text(encoding="utf-8").endswith(
        "Summary for 2503.10291"
    )


def test_bundle_cli_export_import_json(tmp_path):
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_config = _config(source_dir)
    _add_paper_with_assets(source_config, _paper(tags=["cli"]))
    bundle_path = tmp_path / "cli-bundle.zip"
    runner = CliRunner()

    export_result = runner.invoke(
        main,
        ["--data-dir", str(source_dir), "bundle", "export", str(bundle_path), "--json"],
    )

    assert export_result.exit_code == 0
    export_data = json.loads(export_result.output)
    assert export_data["exported_papers"] == 1

    import_result = runner.invoke(
        main,
        ["--data-dir", str(dest_dir), "bundle", "import", str(bundle_path), "--json"],
    )

    assert import_result.exit_code == 0
    import_data = json.loads(import_result.output)
    assert import_data["created"] == 1
    assert import_data["updated"] == 0
    assert import_data["skipped"] == 0

    imported = StorageManager(_config(dest_dir)).get_paper("2503.10291")
    assert imported is not None
    assert imported.tags == ["cli"]
