# Paper Assistant — Agent Playbook

For user-facing setup and usage, see [README.md](README.md).

## Key Facts

- `index.json` is the only state database. `StorageManager` re-reads from disk each call — never cache instances across operations.
- Config resolution: CLI flag > env var > `.env` > default.
- `ANTHROPIC_API_KEY` is optional at load time; validated lazily in `summarizer.py` at point of use. Read-only commands (`search`, `list`, `serve`) work without it.
- Design docs for implemented features live in `docs/`. Roadmap is in `docs/roadmap.md`.

## Critical Invariants

1. **Re-fetch paper after `save_summary`.**
   `storage.save_summary()` updates a different paper instance internally.
   Always call `paper = storage.get_paper(paper_id)` before further mutations.

1b. **Use `paper_id` as the universal key.**
   `PaperMetadata.paper_id` resolves to `arxiv_id` for arXiv papers, `source_slug` for web articles and local notes.
   All storage/route/CLI calls use `paper_id`, never raw `arxiv_id` for lookups.

1c. **`SourceType.NOTE` uses title-derived unique slugs.**
   Local markdown entries are keyed by `source_slug`, not by title text.
   Deduplicate collisions by appending `-2`, `-3`, etc. before persisting.

1d. **`--force` imports must merge, not replace, existing state.**
   Preserve `date_added`, `reading_status`, `notion_page_id`, `notion_modified_at`, `last_synced_at`, and `archived_at`.
   Merge tags by union; never remove existing tags during re-import.
   Preserve `audio_path` only when `--skip-audio` is set; otherwise clear and regenerate audio.

2. **Keep `index.json` and file paths consistent.**
   `pdf_path`, `summary_path`, and `audio_path` are stored relative to `data_dir`.

3. **Maintain sync metadata correctly.**
   `local_modified_at` must update when summary/tags/reading-status are edited locally.
   `notion_page_id`, `notion_modified_at`, and `last_synced_at` are maintained by sync paths.
   `archived_at` mirrors archive/read-status propagation.

4. **Preserve async boundaries.**
   Network and TTS paths are async.
   CLI commands must bridge with `asyncio.run()` only at command entry points.

4b. **arXiv metadata `429`s should fail over quickly.**
   Metadata fetches should prefer the abs-page fallback over burning the full API retry budget.

5. **TTS speaks full markdown summary content**, not only one-pager section.

5b. **Browser Reader Mode is separate from generated audio.**
   - Prefer browser default/local non-novelty voices.
   - Prefer chunked utterances with sentence highlighting driven by boundary events.
   - Speech stays scoped to prose blocks — do not read tables, equations, or code verbatim.
   - Keyboard controls: `K` or `Space` pauses/resumes, `Escape` stops.
   - Sentence fragments may be focusable/clickable; global playback shortcuts must still work.
   - Keep `docs/design-browser-reader-mode.md` aligned when changing this feature.
   - Client-side only — no `index.json`, storage, or API changes unless explicitly requested.
   - `tts.py` and generated MP3 files remain the source for saved audio/podcast behavior.

6. **FastAPI request models must remain at module level.**
   With `from __future__ import annotations`, nested request-body models break type-hint resolution.

7. **Feed/audio failures should degrade gracefully.**
   Summary import/add should still succeed when TTS or feed regeneration fails.

7b. **qmd search is optional.**
   `get_search_manager(config)` returns `None` when disabled or unavailable.
   All mutation hooks guard with `if search_mgr:` and catch exceptions.
   Search failures never break primary operations.
   `search/` directory contains derived docs (`{paper_id}.md`) — never index raw summary files.
   Single-paper mutations use `sync_paper()`; multi-paper mutations use `batch_sync()`.
   `qmd_command` is `list[str]` internally; env var is shell-style → `shlex.split()`.
   Every qmd invocation passes `--index <qmd_index_name>` for isolation.

8. **Notion sync should remain manual and non-destructive by default.**
   `sync_notion(..., dry_run=True)` must not mutate local or Notion state.
   Archive state propagates via soft-archive fields, not hard delete.

8b. **Note round-trip fidelity in Notion depends on optional schema fields.**
   `source_type` preserves NOTE vs WEB when importing remote-only pages.
   `source_url` preserves bookmarked links for non-arXiv entries.

8c. **Notion block writes must respect the API's nested-child depth limit.**
   Create/update paths should append deeper descendants recursively after shallower parent blocks exist.

## Skill Workflow Gotchas

- `prompts/paper_summary_instructions.md` is the shared summary instruction source for Claude Code, Codex, and manual workflows. Agent summaries should use normal Markdown paragraphs, not hard-wrapped prose.
- Paper content retrieval prefers `hf papers info <id>` for metadata and `hf papers read <id>` for body content (redirected to a file to avoid shell output truncation); PDF download is the fallback path only.
- `skill-import` must always receive the canonical arXiv URL (`https://arxiv.org/abs/<id>`), not HuggingFace or other source URLs, so that `paper_id` resolves to the arXiv ID.
- Skill-based summary workflows sync Notion by default unless the user explicitly opts out with `--no-sync-notion`.
- Artifacts live under `.artifacts/summarize-paper/` during skill runs.

## Web UI / Frontend

- CSS theme uses Pico CSS 2 as base + custom `style.css` with design tokens (`:root` variables prefixed `--pa-*`).
- Fonts: Fraunces (display/headings, variable with SOFT/WONK axes) + Source Sans 3 (body/UI). Loaded via Google Fonts `@import` in `style.css`.
- JS-critical selectors: all element IDs, `data-paper-id`, `onclick`/`onchange` handlers, `.reading-status-select`, `.tag-chip` (used in `renderTags()` JS), `.error` (injected via `innerHTML`), all `reader-*` classes. Do not rename or remove these.
- Reader mode CSS is self-contained — avoid modifying `.reader-*` rules unless specifically requested.
- Status badge classes follow `status-{value}` pattern matching `ProcessingStatus` enum values in `models.py`.
- Editable install serves source static files directly; browser cache (`Cmd+Shift+R`) is the usual culprit when CSS changes don't appear.

## Agent Workflow

When implementing changes:

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
- `_read_rich_markdown` preserves inline formatting when converting Notion rich_text back to markdown; `_read_plain_text` is only for non-markdown contexts
- Math in table cells: `_escape_math_pipes_in_tables` and `_normalise_display_math` handle `|` and `$$` inside table rows; both skip fenced code blocks
- Mermaid code blocks are stored as Notion code blocks with language `"mermaid"` (Notion may not render as diagrams via API)

## Testing

```bash
pytest tests/
```

Target files:
- `tests/test_storage.py` — index/path invariants
- `tests/test_summarizer.py` — section parsing
- `tests/test_web_*.py` — route contracts
- `tests/test_cli_*.py` — command behavior
- `tests/test_notion.py` — sync conflict/merge rules
- `tests/test_search.py` — SearchManager, search doc generation, degraded behavior

Browser Reader Mode: automated coverage at the HTML contract level; manual desktop Brave/Chromium QA for speech events, sentence progression, highlight behavior, and keyboard shortcuts.

## Definition of Done

- Behavior change matches requested scope.
- No critical invariant is violated.
- Tests added/updated where behavior changed.
- Test suite run and results reported.
- `README.md` updated for user-facing changes; `CLAUDE.md` updated for agent-facing changes.
- Error messages remain actionable.

## Non-Goals

- Moving from JSON index to SQL database.
- Over-engineering deployment workflows for cloud hosting.
- Expanding API surface without matching test coverage.
