# Add First-Class Local Note Entries

Status: implemented in the current codebase.

## Summary
Add a third source type, `SourceType.NOTE`, for locally authored or bookmarked markdown that is not fetchable from arXiv or the web. These entries will be created from `title + markdown + optional source_url + tags`, get a title-derived slug as `paper_id`, store the pasted markdown as the main saved document, and work with existing tags, reading status, audio, feed, detail page, and manual Notion sync flows.

## Public Interfaces
- Add `SourceType.NOTE = "note"` in `models.py`.
- Add `paper-assist create --title ... [--source-url ...] [--file ...] [--skip-audio] [--tags ...]`.
- Add `POST /api/create` with a module-level request model:
  - `title: str`
  - `markdown: str`
  - `source_url: str | None = None`
  - `tags: list[str] = []`
  - `skip_audio: bool = False`
- Extend the optional Notion schema with:
  - `source_type` (`select`, values `arxiv`, `web`, `note`) for round-trip fidelity
  - `source_url` (`rich_text`) to preserve bookmark links

## Implementation Changes
- Add `slugify_title(title)` and unique-slug resolution that appends `-2`, `-3`, etc. when a title slug already exists in `index.json`.
- Implement a shared create-local-entry pipeline used by both CLI and `/api/create`; it should:
  - never fetch `source_url`
  - save the pasted markdown directly into the existing `summary_path`
  - run existing audio/feed steps after save
  - return the final slug-based `paper_id`
- Update storage/formatting so NOTE summaries use note-aware metadata:
  - `make_summary_filename(...)` uses `[Note][{paper_id}] ...` for notes
  - formatted markdown includes `source_type: note`, `source_slug`, and optional `source_url`
  - note headers omit empty URL/author lines instead of rendering `None` or `by .`
- Replace scattered `SourceType.WEB` assumptions with explicit NOTE handling in:
  - summary formatting
  - TTS source labeling and intro generation
  - feed link generation
  - detail-page metadata rendering and action labels
- Update Notion sync to carry NOTE metadata safely:
  - `NotionPaper` gains `source_type` and `source_url`
  - create/update writes those properties when the DB has them
  - parse/import uses `source_type=note` to recreate local NOTE entries
  - fallback remains `arxiv_id -> ARXIV`, `source_slug -> WEB` when `source_type` is absent
  - syncing a NOTE entry without Notion `source_type` should warn that round-trip fidelity is reduced
- Update UI with a dedicated “Add a local article / note” form on the index page:
  - required title
  - optional source URL
  - tags
  - markdown textarea
  - separate submit/status handling from the existing import form

## Test Plan
- `tests/test_models.py`: NOTE serialization and `paper_id` behavior.
- `tests/test_storage.py`: note CRUD, `[Note]` filenames, duplicate-title slug suffixing.
- `tests/test_web.py`: `POST /api/create` success, optional `source_url`, duplicate title behavior, note detail rendering without URL/authors.
- Add `tests/test_cli_create.py`: CLI create with file/clipboard-style input.
- `tests/test_notion.py`: note sync create/update, remote note import, `source_url` round-trip, warning when `source_type` is missing.
- Add/extend coverage for TTS intro with empty authors and feed link fallback to local `/paper/{paper_id}` when no external URL exists.

## Assumptions
- The pasted markdown is the main document for NOTE entries; this change does not introduce a separate raw-content field.
- The new UI flow is source-type specific and does not replace or overload `/api/import`.
- NOTE entries participate in the existing manual Notion sync flow; no auto-sync checkbox is included in this scope.
- The create form asks only for title, optional source URL, tags, and markdown; authors/published date stay out of scope for v1.
- Verification is now green: `PYTHONPATH=src /Users/liyuanzhe/ml-env/bin/python -m pytest tests/ -q` passed.
