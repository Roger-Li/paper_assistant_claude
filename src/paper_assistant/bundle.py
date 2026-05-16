"""Portable bundle import/export for local Paper Assistant libraries."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from paper_assistant.config import Config
from paper_assistant.models import Paper
from paper_assistant.storage import StorageManager


BUNDLE_SCHEMA_VERSION = 1
BUNDLE_APP_NAME = "paper-assistant"
MANIFEST_NAME = "manifest.json"
FILES_PREFIX = "files"

_ASSET_ATTRS = ("summary_path", "transcript_path", "audio_path", "pdf_path")
_ALLOWED_ASSET_DIRS = {"papers", "transcripts", "audio", "pdfs"}
_NOTION_EXPORT_FIELDS = {"notion_page_id", "notion_modified_at", "last_synced_at"}


@dataclass
class BundleExportReport:
    """Summary of an exported bundle."""

    bundle_path: Path
    exported_papers: int
    exported_files: int
    paper_ids: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class BundleImportReport:
    """Summary of an imported bundle."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    imported_files: int = 0
    paper_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def touched_paper_ids(self) -> set[str]:
        return set(self.paper_ids)


def export_bundle(
    config: Config,
    storage: StorageManager,
    bundle_path: Path,
    *,
    paper_ids: Iterable[str] | None = None,
) -> BundleExportReport:
    """Export selected local records and their referenced assets to a zip bundle.

    Notion linkage fields are stripped from exported records so the bundle can move
    between machines without carrying Notion-specific state.
    """
    index = storage.load_index()
    selected_ids = list(paper_ids or sorted(index.papers))
    missing_ids = [paper_id for paper_id in selected_ids if paper_id not in index.papers]
    if missing_ids:
        raise ValueError(f"Paper(s) not found: {', '.join(missing_ids)}")

    bundle_path = bundle_path.expanduser()
    bundle_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_papers: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    asset_paths: list[tuple[str, Path]] = []

    for paper_id in selected_ids:
        paper = _strip_notion_metadata(index.papers[paper_id])
        manifest_papers[paper_id] = _paper_export_payload(paper)
        for rel_path in _iter_asset_paths(paper):
            try:
                safe_rel = _safe_asset_path(rel_path)
            except ValueError as exc:
                warnings.append(f"{paper_id}: skipped unsafe asset path {rel_path!r}: {exc}")
                continue
            full_path = _resolve_data_path(config, safe_rel)
            if not full_path.exists():
                warnings.append(f"{paper_id}: missing asset {rel_path}")
                continue
            asset_paths.append((safe_rel.as_posix(), full_path))

    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "app": BUNDLE_APP_NAME,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "papers": manifest_papers,
    }

    seen_assets: set[str] = set()
    with zipfile.ZipFile(bundle_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for rel_path, full_path in asset_paths:
            if rel_path in seen_assets:
                continue
            seen_assets.add(rel_path)
            zf.write(full_path, _bundle_asset_name(rel_path))

    return BundleExportReport(
        bundle_path=bundle_path,
        exported_papers=len(selected_ids),
        exported_files=len(seen_assets),
        paper_ids=selected_ids,
        warnings=warnings,
    )


def import_bundle(
    config: Config,
    storage: StorageManager,
    bundle_path: Path,
    *,
    paper_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> BundleImportReport:
    """Import a portable bundle into local storage.

    Existing records are skipped unless force=True. Forced imports preserve existing
    Notion sync metadata, reading status, archive state, and date_added. Tags are
    merged by union. Missing assets in the bundle do not clear existing asset paths.
    """
    config.ensure_dirs()
    report = BundleImportReport()
    selected_filter = set(paper_ids or [])

    with zipfile.ZipFile(bundle_path, mode="r") as zf:
        bundle_papers = _read_manifest(zf)
        if selected_filter:
            missing_ids = sorted(selected_filter - set(bundle_papers))
            if missing_ids:
                raise ValueError(f"Paper(s) not found in bundle: {', '.join(missing_ids)}")
            selected_ids = [paper_id for paper_id in bundle_papers if paper_id in selected_filter]
        else:
            selected_ids = list(bundle_papers)

        for paper_id in selected_ids:
            incoming = _strip_notion_metadata(bundle_papers[paper_id])
            existing = storage.get_paper(paper_id)
            copied_paths: set[str] = set()

            if existing is not None and not force:
                report.skipped += 1
                continue

            for attr in _ASSET_ATTRS:
                rel_path = getattr(incoming, attr)
                if rel_path is None:
                    continue
                try:
                    safe_rel = _safe_asset_path(rel_path)
                except ValueError as exc:
                    report.warnings.append(
                        f"{paper_id}: skipped unsafe asset path {rel_path!r}: {exc}"
                    )
                    setattr(incoming, attr, None)
                    continue

                arcname = _bundle_asset_name(safe_rel.as_posix())
                if arcname not in zf.namelist():
                    report.warnings.append(f"{paper_id}: bundle missing asset {rel_path}")
                    setattr(incoming, attr, getattr(existing, attr) if existing else None)
                    continue

                copied_paths.add(safe_rel.as_posix())
                if not dry_run:
                    destination = _resolve_data_path(config, safe_rel)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(arcname, "r") as src:
                        destination.write_bytes(src.read())

            if dry_run:
                if existing is None:
                    report.created += 1
                else:
                    report.updated += 1
                report.imported_files += len(copied_paths)
                report.paper_ids.append(paper_id)
                continue

            merged = _merge_for_import(existing, incoming)
            storage.add_paper(merged)
            if existing is None:
                report.created += 1
            else:
                report.updated += 1
            report.imported_files += len(copied_paths)
            report.paper_ids.append(paper_id)

    return report


def _strip_notion_metadata(paper: Paper) -> Paper:
    stripped = paper.model_copy(deep=True)
    stripped.notion_page_id = None
    stripped.notion_modified_at = None
    stripped.last_synced_at = None
    return stripped


def _paper_export_payload(paper: Paper) -> dict[str, object]:
    payload = paper.model_dump(mode="json")
    for field_name in _NOTION_EXPORT_FIELDS:
        payload.pop(field_name, None)
    return payload


def _merge_for_import(existing: Paper | None, incoming: Paper) -> Paper:
    if existing is None:
        return incoming

    merged = incoming.model_copy(deep=True)
    merged.date_added = existing.date_added
    merged.reading_status = existing.reading_status
    merged.archived_at = existing.archived_at
    merged.notion_page_id = existing.notion_page_id
    merged.notion_modified_at = existing.notion_modified_at
    merged.last_synced_at = existing.last_synced_at
    merged.tags = _merge_tags(existing.tags, incoming.tags)

    for attr in _ASSET_ATTRS:
        if getattr(merged, attr) is None:
            setattr(merged, attr, getattr(existing, attr))

    if merged.model_used is None:
        merged.model_used = existing.model_used
    if merged.token_count is None:
        merged.token_count = existing.token_count
    if merged.error_message is None:
        merged.error_message = existing.error_message

    merged.local_modified_at = max(existing.local_modified_at, incoming.local_modified_at)
    return merged


def _merge_tags(existing_tags: list[str], incoming_tags: list[str]) -> list[str]:
    merged: list[str] = []
    for tag in [*existing_tags, *incoming_tags]:
        if tag and tag not in merged:
            merged.append(tag)
    return merged


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Paper]:
    if MANIFEST_NAME not in zf.namelist():
        raise ValueError("Bundle is missing manifest.json")

    with zf.open(MANIFEST_NAME, "r") as manifest_file:
        data = json.loads(manifest_file.read().decode("utf-8"))

    if data.get("app") != BUNDLE_APP_NAME:
        raise ValueError("Bundle was not created by Paper Assistant")
    if data.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported bundle schema version: {data.get('schema_version')!r}"
        )

    raw_papers = data.get("papers")
    if not isinstance(raw_papers, dict):
        raise ValueError("Bundle manifest has no papers mapping")

    papers: dict[str, Paper] = {}
    for paper_id, raw_paper in raw_papers.items():
        paper = Paper.model_validate(raw_paper)
        if paper.metadata.paper_id != paper_id:
            raise ValueError(
                f"Bundle manifest key {paper_id!r} does not match paper_id "
                f"{paper.metadata.paper_id!r}"
            )
        papers[paper_id] = paper
    return papers


def _iter_asset_paths(paper: Paper) -> list[str]:
    return [
        rel_path
        for rel_path in (getattr(paper, attr) for attr in _ASSET_ATTRS)
        if rel_path is not None
    ]


def _safe_asset_path(rel_path: str) -> PurePosixPath:
    path = PurePosixPath(rel_path)
    if path.is_absolute():
        raise ValueError("absolute paths are not allowed")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("path traversal is not allowed")
    if not path.parts or path.parts[0] not in _ALLOWED_ASSET_DIRS:
        raise ValueError("asset path must be under papers/, transcripts/, audio/, or pdfs/")
    return path


def _resolve_data_path(config: Config, rel_path: PurePosixPath) -> Path:
    base = config.data_dir.resolve()
    target = (base / Path(*rel_path.parts)).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Asset path escapes data directory: {rel_path.as_posix()}")
    return target


def _bundle_asset_name(rel_path: str) -> str:
    return f"{FILES_PREFIX}/{rel_path}"
