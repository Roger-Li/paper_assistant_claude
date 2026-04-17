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
   Force × skip matrix for audio assets:
   - `skip_audio=True` (master switch): preserve both `audio_path` and `transcript_path`.
   - `skip_transcript=True` alone: preserve `transcript_path`; regenerate audio from raw summary.
   - Neither flag: clear both and regenerate through `render_audio_assets()`.

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

5. **TTS input is the derived narration script when available.**
   Audio is synthesized from `transcript_path` (written to `transcripts/{paper_id}.md`)
   via `prepare_script_for_tts()` when present; otherwise the full markdown summary
   goes through `prepare_text_for_tts()` as a fallback. Never synthesize from the
   one-pager section alone.

5a. **Audio-asset generation is centralized in `audio_assets.render_audio_assets()`.**
   Every inline TTS call site (CLI add/import/skill-import, `POST /api/add`,
   `POST /api/create`, `POST /api/import`, `PUT /api/paper/{id}/summary`, the new
   `POST /api/paper/{id}/transcript/regenerate`, `paper-assist transcript regenerate`,
   and `pipeline.create_local_entry`) delegates audio work through this helper.
   Skill-driven imports that already generated a transcript pass
   `provided_script_markdown` and may set `skip_script_generation=True`
   to suppress silent Anthropic fallback.
   Backends raise typed errors (`MlxConfigError`, `MlxTransientError`, `EdgeTTSError`,
   `FfmpegMissingError`); the helper converts them to warnings so import flows
   degrade gracefully (invariant 7).

5c. **Primary TTS backend is local MLX; edge-tts is graceful fallback.**
   `config.tts_backend` defaults to `"mlx"` and targets an OpenAI-compatible
   `/v1/audio/speech` endpoint at `config.mlx_tts_url`. `MlxTransientError`
   triggers edge fallback when `tts_edge_fallback` is set; `MlxConfigError`
   (4xx responses) does NOT fall back — the warning surfaces the misconfiguration.
   `mlx_tts_voice` is the generic MLX/OpenAI-style selector and is the main
   stable voice pin for the current oMLX Qwen3-TTS CustomVoice server. Use a
   server-supported voice ID such as `ryan` there. `mlx_tts_speaker` is only a
   best-effort model-specific selector for backends that explicitly support a
   separate `speaker` field; some OpenAI-compatible servers ignore it.
   ffmpeg is recommended (`brew install ffmpeg`) for long-paper multi-chunk MP3
   concatenation on the MLX path.

5d. **`normalize_summary_body()` is the single source of truth** for stripping
   YAML front matter + the duplicated title/metadata header from stored summaries.
   All edit/regen/narration entry points should use it when loading from disk.

5b. **Browser Reader Mode was removed.** (2026-04-17, roadmap 2d.)
   The client-side Web Speech feature was dropped because it drifted out of sync
   with the transcript-backed MLX audio pipeline. The saved MP3 player on the
   detail page is now the only listen-along path. Do not reintroduce
   `reader_mode.js`, `#reader-mode` DOM, or `.reader-*` CSS without an explicit
   product decision. `tts.py` + generated MP3 files remain the source for saved
   audio/podcast behavior.

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
   Default search mode is **hybrid** (BM25 + vector + LLM re-ranking) everywhere:
   CLI, web API, web UI, and skill workflows.
   When embeddings are missing, hybrid/vector automatically falls back to text (BM25) with a warning.
   Run `paper-assist index-rebuild --embed` to enable full hybrid search.

8. **Notion sync should remain manual and non-destructive by default.**
   `sync_notion(..., dry_run=True)` must not mutate local or Notion state.
   Archive state propagates via soft-archive fields, not hard delete.

8b. **Note round-trip fidelity in Notion depends on optional schema fields.**
   `source_type` preserves NOTE vs WEB when importing remote-only pages.
   `source_url` preserves bookmarked links for non-arXiv entries.

8c. **Notion block writes must respect the API's nested-child depth limit.**
   Create/update paths should append deeper descendants recursively after shallower parent blocks exist.

## Skill Workflow Gotchas

- `src/paper_assistant/prompts/paper_summary_instructions.md` is the shared summary instruction source for Claude Code, Codex, Kiro, and manual workflows. Agent summaries should use normal Markdown paragraphs, not hard-wrapped prose.
- Paper content retrieval prefers `hf papers info <id>` for metadata and `hf papers read <id>` for body content (redirected to a file to avoid shell output truncation); PDF download is the fallback path only.
- `skill-import` must always receive the canonical arXiv URL (`https://arxiv.org/abs/<id>`), not HuggingFace or other source URLs, so that `paper_id` resolves to the arXiv ID.
- Skill-based summary workflows sync Notion by default unless the user explicitly opts out with `--no-sync-notion`. The Kiro skill omits Notion sync entirely (designed for environments without Notion credentials).
- Skill-based summary workflows now generate `.artifacts/summarize-paper/<paper_id>/transcript.md` by default when audio is enabled, then pass `--script-file ... --no-script-fallback` to `skill-import`. If transcript generation fails, they warn first and fall back to `--skip-transcript` or `--skip-audio` instead of shipping an empty script file.
- Artifacts live under `.artifacts/summarize-paper/` during skill runs.

## Web UI / Frontend

- CSS theme uses Pico CSS 2 as base + custom `style.css` with design tokens (`:root` variables prefixed `--pa-*`).
- Fonts: Fraunces (display/headings, variable with SOFT/WONK axes) + Source Sans 3 (body/UI). Loaded via Google Fonts `@import` in `style.css`.
- JS-critical selectors: all element IDs, `data-paper-id`, `onclick`/`onchange` handlers, `.reading-status-select`, `.tag-chip` (used in `renderTags()` JS), `.error` (injected via `innerHTML`). Do not rename or remove these.
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
- `tests/test_storage.py` — index/path invariants (including transcript round-trip + cleanup)
- `tests/test_summarizer.py` — section parsing + `normalize_summary_body`
- `tests/test_web_*.py` — route contracts
- `tests/test_cli_*.py` — command behavior
- `tests/test_notion.py` — sync conflict/merge rules
- `tests/test_search.py` — SearchManager, search doc generation, degraded behavior
- `tests/test_tts.py` — backend factory, chunking, `prepare_*_for_tts` helpers
- `tests/test_tts_mlx.py` — MLX backend (respx-mocked `/v1/audio/speech`)
- `tests/test_audio_assets.py` — `render_audio_assets` force × skip matrix + fallback
- `tests/test_audio_script.py` — Claude narration script generation
- `tests/test_cli_transcript_regenerate.py` — `transcript regenerate` + `tts check`
- `tests/test_web_transcript_regenerate.py` — `POST /api/paper/{id}/transcript/regenerate`

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
