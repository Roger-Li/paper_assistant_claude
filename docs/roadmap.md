# Prioritized Roadmap

Roadmap for Paper Assistant. Item numbers are preserved from the original
list for traceability across commit messages and conversations.

For detailed design specs, see the corresponding `docs/design-*.md` and
`docs/plan-*.md` files.

## Active / Remaining

2. `regenerate-audio` command (`single` and `--all`) for imported/legacy entries. (`paper-assist transcript regenerate <paper_id>` covers the single-paper path via `docs/plan-audio-friendly-readout.md`; a `--all` batch path is still open.)
3. Notion sync upload retries. (Formatting, nested lists, write timeouts (120s), and non-empty error reporting done; retry-on-failure remains.)
4. Reachable podcast feed for phone clients (LAN/tunnel/hosted URL strategy). (`podcast_base_url` config exists but defaults to localhost; reachable-URL strategy remains.)
5. Batch import for multiple arXiv entries + summary files.
8. Refactor: Extract remaining shared pipeline logic from `cli.py` and `web/routes.py` into `pipeline.py`. (`import_paper_summary`, `create_local_entry`, and `regenerate_transcript_and_audio` are extracted; the add workflows (arxiv+web × cli+routes) are still duplicated.)
9. Refactor: Split `NotionClient` in `notion.py` (~2000 LOC) — extract property mapping, block fetching, and page building into focused helpers.
10. Refactor: Break `_ast_node_to_blocks` (~145 LOC) into per-block-type sub-functions for tables, lists, and code blocks.
11. Workflow optimization: `--sync-notion` flag on `add`/`import` commands + web API. (`skill-import` has the flag, and the `/synthesize` flow syncs via `notion-sync --paper`; plain `add`/`import` and the web API still lack it. See `docs/design-workflow-optimization.md` R1, R6)
12b. Additional synthesis templates (`comparison`, `study-guide`) as new template slots in `paper_synthesis_instructions.md`. (lit-review shipped in 12.)
14. Academic paper search MCP server integration — optional, for paper discovery in lit reviews. (See R3)
15. Evaluate community research skills for adoption/inspiration. (See R7)

## Completed

1. ~~Sorting entries by tag/date added/title; editing existing summaries.~~
2b. ~~Skill-driven transcript generation — host agent produces the narration script artifact, and import surfaces can opt out of Anthropic script fallback.~~ (See `docs/plan-skill-driven-transcript.md`)
2c. ~~[BUG] MLX/Qwen speaker drift in generated audio.~~ MLX now forwards the generic `voice` selector on every chunk and can also forward a model-specific `speaker` when the backend supports it. The docs now spell out that the current oMLX Qwen3-TTS CustomVoice server relies on supported `voice` IDs such as `ryan`.
2d. ~~[DROPPED] Browser Reader Mode.~~ (2026-04-17) The client-side Web Speech feature drifted out of sync with the transcript-backed MLX audio path, and re-aligning it (sentence timing without server metadata, matching voice, etc.) was not worth the maintenance cost. Removed `reader_mode.js`, `#reader-mode` DOM, `.reader-*` CSS, and `docs/design-browser-reader-mode.md`. Saved MP3 on the detail page is now the only listen-along path. Existing summaries, `index.json`, and audio files are unaffected (Reader Mode was client-side only).
3a. ~~Notion sync fidelity — formatting and nested lists.~~
6. ~~Search across titles/tags/summaries.~~ Shipped via the qmd integration: `paper-assist search` (hybrid default), web API/UI search, and skill workflows. (See `docs/plan-qmd-search.md`)
7. ~~Non-arXiv web articles.~~ (See `docs/design-web-article-support.md`)
12a. ~~`SourceType.NOTE` + `paper-assist create` command + local note web/API flow.~~
12. ~~Synthesis prompt templates.~~ (2026-06-12) `/synthesize` skill (`.claude/commands/synthesize.md`) + `src/paper_assistant/prompts/paper_synthesis_instructions.md` with the `lit-review` template; imports as a `SourceType.NOTE` via `paper-assist create` (now with `--script-file`/`--no-script-fallback`/`--json`/`--cleanup-file`) and `list --json` for enumeration. Comparison/study-guide templates remain open as 12b.
13. ~~Skill-based single-paper summary workflow for Claude Code/Codex.~~
16. ~~Notion image-sync hardening.~~ (2026-06-12) Pull paths (`_set_local_from_remote`, `_import_remote_only`) restore presigned Notion file URLs back to local `/images/<paper_id>/<basename>` refs when the file exists locally, applied before the remote-vs-local comparison so URL churn never dirties local state. Page/block writes use `_NOTION_WRITE_TIMEOUT` (120s), and sync errors surface via `describe_exception` (`str(exc) or repr(exc)`), so a `ReadTimeout` no longer reports an empty `notion_error`.
