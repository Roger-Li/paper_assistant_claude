# Paper Assistant — Coding Agent Playbook

This guide is for coding agents and contributors making changes in this repository.
For user-facing setup and usage, see [README.md](README.md).

## Purpose

Keep the project reliable while iterating quickly on:
- arXiv ingestion
- summarization
- audio generation
- local web UI/API
- RSS feed generation

## Code Map

```text
src/paper_assistant/
├── cli.py          # Click commands and end-to-end pipelines
├── config.py       # Config loading and directory management
├── models.py       # Pydantic models, processing/reading status enums
├── arxiv.py        # arXiv URL parsing, metadata fetch, PDF download
├── pdf.py          # PDF text extraction
├── prompt.py       # Claude prompt template
├── summarizer.py   # Summarization orchestration and parsing helpers
├── storage.py      # JSON index CRUD and file naming helpers
├── tts.py          # Markdown-to-speech conversion
├── podcast.py      # RSS feed generation
├── notion.py       # Notion API client + manual two-way sync orchestration
└── web/
    ├── app.py      # FastAPI app factory
    ├── routes.py   # HTML and JSON endpoints
    ├── templates/  # Jinja templates
    └── static/     # CSS and JS assets
```

## Operational Model

### Data source of truth

`index.json` is the only state database. `StorageManager` re-reads from disk each call to support mixed CLI + web usage.

### Data directory

Default is `~/.paper-assistant/` unless overridden by `PAPER_ASSIST_DATA_DIR`.

```text
~/.paper-assistant/
├── papers/
├── audio/
├── pdfs/
├── index.json
└── feed.xml
```

### Platform stance

- Primary runtime: macOS
- Linux supported with caveats (notably clipboard and iCloud behavior)

## Critical Invariants

1. Re-fetch paper after `save_summary`.
   - `storage.save_summary()` updates a different paper instance internally.
   - Always call `paper = storage.get_paper(arxiv_id)` before further mutations.

2. Keep `index.json` and file paths consistent.
   - `pdf_path`, `summary_path`, and `audio_path` are stored relative to `data_dir`.

3. Maintain sync metadata correctly.
   - `local_modified_at` must update when summary/tags/reading-status are edited locally.
   - `notion_page_id`, `notion_modified_at`, and `last_synced_at` are maintained by sync paths.
   - `archived_at` mirrors archive/read-status propagation.

4. Preserve async boundaries.
   - Network and TTS paths are async.
   - CLI commands must bridge with `asyncio.run()` only at command entry points.

5. TTS speaks full markdown summary content.
   - Audio generation uses full markdown, not only one-pager section.

6. FastAPI request models must remain at module level.
   - With `from __future__ import annotations`, nested request-body models can break type-hint resolution.

7. Feed/audio failures should degrade gracefully.
   - Summary import/add should still succeed when TTS or feed regeneration fails; report warning state.

8. Notion sync should remain manual and non-destructive by default.
   - `sync_notion(..., dry_run=True)` must not mutate local or Notion state.
   - Archive state should propagate via soft-archive fields, not hard delete.

## Config Contracts

Supported env vars (actual behavior in `config.py`):
- `ANTHROPIC_API_KEY` (required)
- `PAPER_ASSIST_DATA_DIR`
- `PAPER_ASSIST_MODEL`
- `PAPER_ASSIST_TTS_VOICE`
- `PAPER_ASSIST_ICLOUD_SYNC`
- `PAPER_ASSIST_ICLOUD_DIR`
- `PAPER_ASSIST_ARXIV_USER_AGENT`
- `PAPER_ASSIST_ARXIV_MAX_RETRIES`
- `PAPER_ASSIST_ARXIV_BACKOFF_BASE_SECONDS`
- `PAPER_ASSIST_ARXIV_BACKOFF_CAP_SECONDS`
- `PAPER_ASSIST_NOTION_SYNC_ENABLED`
- `PAPER_ASSIST_NOTION_TOKEN`
- `PAPER_ASSIST_NOTION_DATABASE_ID`
- `PAPER_ASSIST_NOTION_ARCHIVE_ON_DELETE`

Resolution order:
- CLI override -> env var -> `.env` -> default

## API Surface (Current)

HTML:
- `GET /`
- `GET /paper/{arxiv_id}`

JSON:
- `POST /api/add`
- `POST /api/import`
- `POST /api/paper/{arxiv_id}/tags`
- `DELETE /api/paper/{arxiv_id}/tags/{tag}`
- `DELETE /api/paper/{arxiv_id}`
- `GET /api/paper/{arxiv_id}/summary`
- `PUT /api/paper/{arxiv_id}/summary`
- `PUT /api/paper/{arxiv_id}/reading-status`
- `GET /api/papers` (supports `?sort=date_added|title|tag|arxiv_id&order=asc|desc&status=...&reading_status=...`)
- `GET /api/notion/sync/preview`
- `POST /api/notion/sync`
- `GET /feed.xml`

## Agent Workflow

When implementing changes, follow this order:

1. Confirm behavior in source before editing.
2. Change minimal modules necessary.
3. Keep storage/index invariants intact.
4. Update or add tests for changed behavior.
5. Update docs when user-facing or agent-facing behavior changes.

If touching pipelines (`add`, `import`, `serve`), verify:
- successful path
- duplicate existing paper path
- partial-failure behavior (TTS/feed warnings)

If touching Notion sync paths, verify:
- local-only record -> Notion create + link update
- remote-only record -> local import path
- timestamp conflict behavior (local newer vs remote newer)
- dry-run produces action report and no mutation
- archive propagation is soft (no local hard delete)
- markdown formatting (bold, italic, code, links, math) renders in Notion
- nested bullet/numbered lists preserve hierarchy via Notion `children` arrays
- `fetch_page_markdown` recursively fetches nested block children

## Testing Expectations

Run the full suite for meaningful changes:

```bash
pytest tests/
```

Favor targeted additions in:
- `tests/test_storage.py` for index/path invariants
- `tests/test_summarizer.py` for section parsing behavior
- `tests/test_web_*.py` for route contracts
- `tests/test_cli_*.py` if command behavior changes
- `tests/test_notion.py` for sync conflict/merge rules

## Definition of Done (Required)

A task is complete only when all are true:

- Behavior change is implemented and matches requested scope.
- No critical invariant is violated.
- Tests were added/updated where behavior changed.
- Test suite (or relevant subset, if constrained) was run and results reported.
- `README.md` is updated for user-facing changes.
- `CLAUDE.md` is updated for agent-facing workflow/invariant changes.
- Error messages remain actionable and do not silently hide hard failures.

## Prioritized Roadmap (Trimmed)

1. ~~Minor improvement - sorting entries by tag/date added/title; enable editing existing summaries to override and regenerate audio files.~~ (Done)
2. `regenerate-audio` command (`single` and `--all`) for imported/legacy entries.
3. ~~Improve Notion sync fidelity (block formatting coverage, larger-page performance, upload retries).~~ (Formatting and nested lists done; upload retries remain.)
4. Reachable podcast feed for phone clients (LAN/tunnel/hosted URL strategy).
5. Batch import for multiple arXiv entries + summary files.
6. Search across titles/tags/summaries.

## Non-Goals (Current)

- Moving from JSON index to SQL database.
- Over-engineering deployment workflows for cloud hosting.
- Expanding API surface without matching test coverage.
