# Paper Assistant — Coding Agent Playbook

This guide is for coding agents and contributors making changes in this repository.
For user-facing setup and usage, see [README.md](README.md).

## Purpose

Keep the project reliable while iterating quickly on:
- arXiv ingestion and web article ingestion
- local markdown note ingestion / bookmarking
- summarization
- audio generation
- local web UI/API
- RSS feed generation

## Code Map

```text
src/paper_assistant/
├── cli.py          # Click commands and end-to-end pipelines
├── config.py       # Config loading and directory management
├── models.py       # Pydantic models, processing/reading status enums, SourceType
├── arxiv.py        # arXiv URL parsing, metadata fetch, PDF download
├── pipeline.py     # Shared local-entry/import helpers incl. import_paper_summary(), ImportResult, DuplicatePaperError
├── web_article.py  # Web article URL detection, slug generation, HTML fetch/extract
├── pdf.py          # PDF text extraction
├── prompt.py       # Prompt templates (paper + article variants)
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

Shared prompt/skill assets:
- `prompts/paper_summary_instructions.md` — tracked summary instructions read by Claude Code, Codex, and manual workflows; agent summaries should use normal Markdown paragraphs instead of hard-wrapped prose
- `.claude/commands/summarize.md` — Claude Code thin adapter for the skill-based summary flow
- `skills/codex/summarize-paper/SKILL.md` — in-repo Codex skill source
- `.artifacts/summarize-paper/` — repo-local working directory for agent PDF/text/summary artifacts during skill runs
- Skill-based summary workflows sync Notion by default unless the user explicitly opts out with `--no-sync-notion`.

## Design Docs

- `docs/design-add-first-class-local-note-entries.md` — design and implementation notes for local markdown-backed note entries and note-aware Notion sync
- `docs/design-web-article-support.md` — original design and migration plan for non-arXiv web article support
- `docs/design-browser-reader-mode.md` — current Browser Reader Mode architecture, constraints, playback model, and QA expectations
- `docs/design-workflow-optimization.md` — workflow optimization roadmap: MCP servers, slash commands, synthesis prompts, `SourceType.NOTE`

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
   - Always call `paper = storage.get_paper(paper_id)` before further mutations.

1b. Use `paper_id` as the universal key.
   - `PaperMetadata.paper_id` resolves to `arxiv_id` for arXiv papers, `source_slug` for web articles and local notes.
   - All storage/route/CLI calls use `paper_id`, never raw `arxiv_id` for lookups.

1c. `SourceType.NOTE` uses title-derived unique slugs.
   - Local markdown entries are keyed by `source_slug`, not by title text.
   - Deduplicate collisions by appending `-2`, `-3`, etc. before persisting.

1d. `--force` imports must merge, not replace, existing state.
   - Preserve `date_added`, `reading_status`, `notion_page_id`, `notion_modified_at`, `last_synced_at`, and `archived_at`.
   - Merge tags by union; never remove existing tags during re-import.
   - Preserve `audio_path` only when `--skip-audio` is set; otherwise clear and regenerate audio.

2. Keep `index.json` and file paths consistent.
   - `pdf_path`, `summary_path`, and `audio_path` are stored relative to `data_dir`.

3. Maintain sync metadata correctly.
   - `local_modified_at` must update when summary/tags/reading-status are edited locally.
   - `notion_page_id`, `notion_modified_at`, and `last_synced_at` are maintained by sync paths.
   - `archived_at` mirrors archive/read-status propagation.

4. Preserve async boundaries.
   - Network and TTS paths are async.
   - CLI commands must bridge with `asyncio.run()` only at command entry points.

4b. arXiv metadata `429`s should fail over quickly.
   - Metadata fetches should prefer the abs-page fallback over burning the full API retry budget.
   - If both API and abs-page metadata are rate-limited, surface the delay and let the caller retry later.

5. TTS speaks full markdown summary content.
   - Audio generation uses full markdown, not only one-pager section.

5b. Browser Reader Mode is separate from generated audio.
   - The paper detail page may offer browser-native read-aloud with sentence highlighting.
   - Prefer browser default/local non-novelty voices when choosing defaults.
   - Prefer chunked utterances with sentence highlighting driven by boundary events when available, instead of one utterance per sentence.
   - Reader Mode should preserve rendered technical content visually when feasible, but speech should stay scoped to prose blocks rather than reading tables, equations, or code verbatim.
   - Reader Mode keyboard controls are part of the user-facing contract: `K` or `Space` pauses/resumes and `Escape` stops.
   - Sentence fragments may be focusable/clickable; global playback shortcuts should still work while those fragments have focus and should only defer to real typing targets.
   - When changing this feature, keep `docs/design-browser-reader-mode.md` aligned with the current implementation and limitations.
   - This is client-side only and must not require `index.json`, storage, or API changes unless explicitly requested.
   - `tts.py` and generated MP3 files remain the source for saved audio/podcast behavior.

6. FastAPI request models must remain at module level.
   - With `from __future__ import annotations`, nested request-body models can break type-hint resolution.

7. Feed/audio failures should degrade gracefully.
   - Summary import/add should still succeed when TTS or feed regeneration fails; report warning state.

8. Notion sync should remain manual and non-destructive by default.
   - `sync_notion(..., dry_run=True)` must not mutate local or Notion state.
   - Archive state should propagate via soft-archive fields, not hard delete.

8b. Note round-trip fidelity in Notion depends on optional schema fields.
   - `source_type` preserves NOTE vs WEB when importing remote-only pages.
   - `source_url` preserves bookmarked links for non-arXiv entries.

8c. Notion block writes must respect the API's nested-child depth limit.
   - Do not send arbitrarily deep list trees in a single `POST /pages` or `PATCH /blocks/{id}/children` payload.
   - Create/update paths should append deeper descendants recursively after the shallower parent blocks exist.

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

## CLI Surface (Current)

- `paper-assist add <url>`
- `paper-assist import <url>` (supports `--model` for summary provenance)
- `paper-assist skill-import <url>` (shared import helper + JSON output for agent workflows; normalizes agent hard-wrapped prose before saving)
- `paper-assist extract-text <pdf-path>` (PDF-to-markdown fallback for skills)
- `paper-assist notion-preflight` (checks Notion DB reachability/schema before `--sync-notion` runs)
- `paper-assist create --title ...`
- `paper-assist notion-sync [--paper ...] [--dry-run]`

## API Surface (Current)

HTML:
- `GET /`
- `GET /paper/{paper_id}` (supports both arXiv IDs and URL-derived slugs)

JSON:
- `POST /api/add` (auto-detects arXiv URLs vs web article URLs)
- `POST /api/import` (auto-detects arXiv URLs vs web article URLs)
- `POST /api/create` (local markdown-backed note entry with title + optional source URL)
- `POST /api/paper/{paper_id}/tags`
- `DELETE /api/paper/{paper_id}/tags/{tag}`
- `PUT /api/tags/rename`
- `DELETE /api/paper/{paper_id}`
- `GET /api/paper/{paper_id}/summary`
- `PUT /api/paper/{paper_id}/summary`
- `PUT /api/paper/{paper_id}/reading-status`
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
- `fetch_page_markdown` recursively fetches nested block children (lists and tables)
- `_read_rich_markdown` preserves inline formatting (bold, italic, code, strikethrough, links, math) when converting Notion rich_text back to markdown; `_read_plain_text` is only for non-markdown contexts
- Math in table cells: `_escape_math_pipes_in_tables` replaces `|` with `\vert ` inside `$...$` in table rows to prevent cell splitting; `_normalise_display_math` downgrades `$$...$$` to `$...$` in table rows to prevent breaking table structure. Both functions skip lines inside fenced code blocks.
- Mermaid code blocks are stored as Notion code blocks with language `"mermaid"`; Notion may not render them as diagrams when created via API (platform limitation)

## Testing Expectations

Run the full suite for meaningful changes:

```bash
pytest tests/
```

Favor targeted additions in:
- `tests/test_storage.py` for index/path invariants
- `tests/test_summarizer.py` for section parsing behavior
- `tests/test_web_*.py` for route contracts
- `tests/test_cli_*.py` if command behavior changes (including `create`)
- `tests/test_notion.py` for sync conflict/merge rules

For browser Reader Mode changes, keep automated coverage at the HTML contract level and do manual desktop Brave/Chromium QA for speech events, sentence progression, highlight behavior, and keyboard shortcuts.

## Definition of Done (Required)

A task is complete only when all are true:

- Behavior change is implemented and matches requested scope.
- No critical invariant is violated.
- Tests were added/updated where behavior changed.
- Test suite (or relevant subset, if constrained) was run and results reported.
- `README.md` is updated for user-facing changes.
- `AGENTS.md` and `CLAUDE.md` are updated together for agent-facing workflow/invariant changes.
- Error messages remain actionable and do not silently hide hard failures.

## Prioritized Roadmap (Trimmed)

1. ~~Minor improvement - sorting entries by tag/date added/title; enable editing existing summaries to override and regenerate audio files.~~ (Done)
2. `regenerate-audio` command (`single` and `--all`) for imported/legacy entries.
3. ~~Improve Notion sync fidelity (block formatting coverage, larger-page performance, upload retries).~~ (Formatting and nested lists done; upload retries remain.)
4. Reachable podcast feed for phone clients (LAN/tunnel/hosted URL strategy).
5. Batch import for multiple arXiv entries + summary files.
6. Search across titles/tags/summaries.
7. ~~Support non-arXiv web articles (blog posts, technical articles).~~ (Done — see `docs/design-web-article-support.md`)
8. Refactor: Extract shared pipeline logic from `cli.py` and `web/routes.py` into a `pipeline.py` module. Both files duplicate add/import workflows 4× (add/import × arxiv/web). (~400 lines of duplication.)
9. Refactor: Split `NotionClient` in `notion.py` (470 LOC) — extract property mapping, block fetching, and page building into focused helpers.
10. Refactor: Break `_ast_node_to_blocks` (143 LOC) into per-block-type sub-functions for tables, lists, and code blocks.
11. Workflow optimization: `--sync-notion` flag on `add`/`import` commands + web API. (See `docs/design-workflow-optimization.md` R1, R6)
12. `SourceType.NOTE` + `paper-assist create` command + local note web/API flow are implemented; synthesis prompt templates (lit review, comparison, study guide) remain TODO for user finalization. (See R2)
13. ~~Skill-based single-paper summary workflow for Claude Code/Codex.~~ (Done — tracked prompt asset, `/summarize`, Codex `summarize-paper` skill, `paper-assist skill-import`, `paper-assist extract-text`)
14. Academic paper search MCP server integration — optional, for paper discovery in lit reviews. (See R3)
15. Evaluate community research skills for adoption/inspiration. (See R7)

## Non-Goals (Current)

- Moving from JSON index to SQL database.
- Over-engineering deployment workflows for cloud hosting.
- Expanding API surface without matching test coverage.
