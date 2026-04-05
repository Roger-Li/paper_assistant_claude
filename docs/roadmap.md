# Prioritized Roadmap

Roadmap for Paper Assistant. Item numbers are preserved from the original
list for traceability across commit messages and conversations.

For detailed design specs, see the corresponding `docs/design-*.md` and
`docs/plan-*.md` files.

## Active / Remaining

2. `regenerate-audio` command (`single` and `--all`) for imported/legacy entries.
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

## Completed

1. ~~Sorting entries by tag/date added/title; editing existing summaries.~~
3a. ~~Notion sync fidelity — formatting and nested lists.~~
7. ~~Non-arXiv web articles.~~ (See `docs/design-web-article-support.md`)
12a. ~~`SourceType.NOTE` + `paper-assist create` command + local note web/API flow.~~
13. ~~Skill-based single-paper summary workflow for Claude Code/Codex.~~
