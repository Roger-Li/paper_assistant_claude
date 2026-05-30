# Prioritized Roadmap

Roadmap for Paper Assistant. Item numbers are preserved from the original
list for traceability across commit messages and conversations.

For detailed design specs, see the corresponding `docs/design-*.md` and
`docs/plan-*.md` files.

## Active / Remaining

2. `regenerate-audio` command (`single` and `--all`) for imported/legacy entries. (`paper-assist transcript regenerate <paper_id>` covers the single-paper path via `docs/plan-audio-friendly-readout.md`; a `--all` batch path is still open.)
3. Notion sync upload retries. (Formatting and nested lists done; upload retries remain.)
4. Reachable podcast feed for phone clients (LAN/tunnel/hosted URL strategy).
5. Batch import for multiple arXiv entries + summary files.
6. Search across titles/tags/summaries.
8. Refactor: Extract shared pipeline logic from `cli.py` and `web/routes.py` into `pipeline.py`. Both files duplicate add/import workflows 4x (add/import x arxiv/web). (~400 lines of duplication.)
9. Refactor: Split `NotionClient` in `notion.py` (470 LOC) — extract property mapping, block fetching, and page building into focused helpers.
10. Refactor: Break `_ast_node_to_blocks` (143 LOC) into per-block-type sub-functions for tables, lists, and code blocks.
11. Workflow optimization: `--sync-notion` flag on `add`/`import` commands + web API. (See `docs/design-workflow-optimization.md` R1, R6)
12. Synthesis prompt templates (lit review, comparison, study guide) remain TODO for user finalization. (See R2)
14. Academic paper search MCP server integration — optional, for paper discovery in lit reviews. (See R3)
15. Evaluate community research skills for adoption/inspiration. (See R7)
16. Notion image-sync hardening (follow-ups from the local figure-upload feature):
    - Read-back round-trip rewrites local `![..](/images/<id>/figN.png)` refs
      into Notion presigned S3 URLs that expire (~1h, `X-Amz-Expires=3600`) when
      a sync pulls remote→local. The canonical local summary then holds
      short-lived URLs; a later local edit + push would send dead external
      image links. Options: preserve locally-originated `/images/` refs on
      pull, or skip rewriting image URLs whose block was a local upload.
    - `_request` uses a fixed 60s timeout; page write + multi-image upload can
      exceed it, raising `httpx.ReadTimeout` *after* the write+uploads land.
      `str(ReadTimeout())` is empty, so `skill-import` reports
      `notion_synced: false` with `notion_error: ""` despite success. Raise the
      timeout on the sync/upload path and surface `repr(exc)`/type when
      `str(exc)` is empty.

## Completed

1. ~~Sorting entries by tag/date added/title; editing existing summaries.~~
2b. ~~Skill-driven transcript generation — host agent produces the narration script artifact, and import surfaces can opt out of Anthropic script fallback.~~ (See `docs/plan-skill-driven-transcript.md`)
2c. ~~[BUG] MLX/Qwen speaker drift in generated audio.~~ MLX now forwards the generic `voice` selector on every chunk and can also forward a model-specific `speaker` when the backend supports it. The docs now spell out that the current oMLX Qwen3-TTS CustomVoice server relies on supported `voice` IDs such as `ryan`.
2d. ~~[DROPPED] Browser Reader Mode.~~ (2026-04-17) The client-side Web Speech feature drifted out of sync with the transcript-backed MLX audio path, and re-aligning it (sentence timing without server metadata, matching voice, etc.) was not worth the maintenance cost. Removed `reader_mode.js`, `#reader-mode` DOM, `.reader-*` CSS, and `docs/design-browser-reader-mode.md`. Saved MP3 on the detail page is now the only listen-along path. Existing summaries, `index.json`, and audio files are unaffected (Reader Mode was client-side only).
3a. ~~Notion sync fidelity — formatting and nested lists.~~
7. ~~Non-arXiv web articles.~~ (See `docs/design-web-article-support.md`)
12a. ~~`SourceType.NOTE` + `paper-assist create` command + local note web/API flow.~~
13. ~~Skill-based single-paper summary workflow for Claude Code/Codex.~~
