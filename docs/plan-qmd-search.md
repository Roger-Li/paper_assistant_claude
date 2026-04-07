# qmd Search Integration Plan

Roadmap items addressed: #6 (search across titles/tags/summaries), enables #12 (synthesis prompts) and #14 (paper discovery MCP).

## Context

Paper Assistant stores structured markdown summaries but has **no search capability** beyond tag/status filtering. [qmd](https://github.com/tobi/qmd) is an on-device markdown search engine (Node.js/SQLite) with BM25 lexical search, vector semantic search (GGUF embeddings), hybrid search with LLM re-ranking, and a built-in MCP server.

## Why qmd fits

| Paper Assistant gap | qmd capability |
|---|---|
| No full-text search (roadmap #6) | BM25 keyword search across markdown |
| No semantic search ("papers about reward models") | Vector embeddings + hybrid search with re-ranking |
| No cross-paper discovery | MCP server lets Claude Code query library mid-conversation |
| No related-paper lookup for synthesis (roadmap #12) | Hybrid search returns ranked results with snippets |

**Why not Python-native?** (chromadb, sqlite-vss, tantivy-py)
- qmd is markdown-native with AST-aware chunking
- MCP server is built-in (zero code for Claude Code integration)
- Hybrid search + re-ranking would take significant effort to build in Python
- Cost: Node.js runtime dependency; mitigated by `npx`/`bunx` (no global install required)
- Integration is subprocess-only — no Node.js code in the Python project

## Core design: derived search documents

Summary files (`papers/[Paper][<id>] <title>.md`) have YAML front matter with title/authors/source but **no tags, reading status, or explicit paper_id**. Indexing them directly would not satisfy tag search.

**Solution**: Maintain `search/{paper_id}.md` — derived docs containing all searchable metadata plus the summary body. One file per paper, named by `paper_id` for trivial result→paper mapping.

```markdown
---
paper_id: "2503.10291"
title: "VisualPRM - An Effective Process Reward Model"
source_type: arxiv
tags: [RL, Reasoning, Multimodal]
reading_status: unread
authors: "Wang et al."
published: "2025-03-13"
url: "https://arxiv.org/abs/2503.10291"
---

[Summary body — YAML front matter and duplicate title block from format_summary_file()
stripped; all markdown section headers (One-Pager, Rapid Skim, etc.) and content preserved]
```

**`sync_paper(paper_id)`**: reads from `index.json` (via StorageManager) + summary file → writes/overwrites `search/{paper_id}.md`. No-op when `paper.summary_path is None` (paper has no summary yet). Ensures `config.data_dir / "search"` exists before writing — does not rely on `setup()` having been run.

**`delete_paper(paper_id)`**: removes `search/{paper_id}.md`.

## qmd index scoping via named index

The upstream README documents `--index <name>` as a global flag, with the default index at `~/.cache/qmd/index.sqlite`. To isolate Paper Assistant's state:

- **Every qmd invocation passes `--index <qmd_index_name>`** (e.g., `--index paper-assistant`).
- This applies to `collection add`, `update`, `embed`, `search`, `vsearch`, `query`, and `mcp`.
- `cwd=config.data_dir` is set for convenience (relative paths in collection add), but `--index` is the actual isolation mechanism.

## qmd CLI syntax reference

```bash
# All commands include --index for scoping
qmd --index paper-assistant collection add ./search --name papers
qmd --index paper-assistant search "query" -c papers -n 10 --json
qmd --index paper-assistant vsearch "query" -c papers -n 10 --json
qmd --index paper-assistant query "query" -c papers -n 10 --json
qmd --index paper-assistant update
qmd --index paper-assistant embed
qmd --index paper-assistant mcp
```

## Configuration

Add to `Config` (in `config.py`):

```python
qmd_enabled: bool = False                 # PAPER_ASSIST_QMD_ENABLED
qmd_command: list[str] = ["qmd"]          # PAPER_ASSIST_QMD_COMMAND (shell-style, shlex.split)
qmd_index_name: str = "paper-assistant"   # PAPER_ASSIST_QMD_INDEX
qmd_collection_name: str = "papers"       # PAPER_ASSIST_QMD_COLLECTION
```

**Env var parsing**: `PAPER_ASSIST_QMD_COMMAND` is a shell-style string (e.g., `"qmd"`, `"npx @tobilu/qmd"`, `"bunx @tobilu/qmd"`, `"/opt/homebrew/bin/qmd"`). Parsed via `shlex.split()` into `list[str]`. Internally always stored as `list[str]`.

**Install story**: The upstream README documents `bun install -g github:tobi/qmd` as the primary install. `npm install -g @tobilu/qmd` and `npx @tobilu/qmd` also appear but should be verified before documenting. Mark npm/npx paths as "verify first" in README.

**Credential scoping**: `load_config()` currently hard-fails if `ANTHROPIC_API_KEY` is missing (`config.py:85-89`). Change to: store `None` when missing. Validate lazily at point of use in `summarizer.py` only (`tts.py` uses `edge-tts`, no API key). This enables `paper-assist search`, `paper-assist list`, `/api/search`, and web UI browsing without Anthropic credentials.

## User-facing failure behavior

### CLI commands (`search`, `index-setup`, `index-rebuild`)

When qmd is disabled (`qmd_enabled=False`) or unavailable (binary not found):
- Print a clear setup message: "Search requires qmd. Install it with `bun install -g github:tobi/qmd` and set `PAPER_ASSIST_QMD_ENABLED=true`."
- Exit with non-zero exit code.

When `--mode vector` or `--mode hybrid` is used but embeddings are missing:
- Print setup hint: "Run `paper-assist index-rebuild --embed` to enable semantic search."
- Exit with code 1. No silent fallback to text mode.

### Web API (`/api/search`)

When qmd is unavailable:
- Return `{"error": "Search is not configured. Install qmd and set PAPER_ASSIST_QMD_ENABLED=true."}` with HTTP 503.
- Consistent with existing `{"error": ...}` pattern in `routes.py`.

When embeddings are missing for vector/hybrid mode:
- Return `{"error": "Semantic search requires embeddings. Run `paper-assist index-rebuild --embed`."}` with HTTP 400.

### Mutation hooks (background index updates)

Two distinct cases:
1. **`get_search_manager(config)` returned `None`** (qmd disabled or unavailable at startup): hooks are not installed; no code runs, no log output.
2. **SearchManager exists but a `sync_paper()`/`batch_sync()` call fails** (qmd process error, disk issue, etc.): log a warning with `paper_id` and exception. Never fail the primary operation.

## Mutation path audit + hook ownership

**Hook placement rule**: Hooks live at the **lowest shared layer** to avoid double-updates.
- `import_paper_summary()` and `create_local_entry()` in `pipeline.py` are called by both CLI and web routes → hooks in `pipeline.py` only.
- Direct CLI `add` and web `add` (bypass pipeline) → hooks in `cli.py` and `routes.py` respectively.
- Tag/status/delete mutations (web-only endpoints) → hooks in `routes.py`.
- Notion sync → hooks in calling code after `sync_notion()` returns.

| Mutation | File:Function | Hook location | Method |
|---|---|---|---|
| Paper added (arXiv/web) | `cli.py:_add_arxiv_paper()`, `_add_web_article()` | `cli.py` | `sync_paper(id)` |
| Paper added (arXiv/web) | `routes.py:_api_add_arxiv()`, web article path | `routes.py` | `sync_paper(id)` |
| Summary imported | `pipeline.py:import_paper_summary()` | `pipeline.py` | `sync_paper(id)` |
| Note created | `pipeline.py:create_local_entry()` | `pipeline.py` | `sync_paper(id)` |
| Summary edited | `routes.py:api_update_summary()` | `routes.py` | `sync_paper(id)` |
| Tags added | `routes.py:api_add_tags()` | `routes.py` | `sync_paper(id)` |
| Tags removed | `routes.py:api_remove_tag()` | `routes.py` | `sync_paper(id)` |
| Tags renamed | `routes.py:api_rename_tags()` | `routes.py` | `batch_sync(changed_ids)` |
| Paper deleted | `cli.py:remove()` | `cli.py` | `delete_paper(id)` |
| Paper deleted | `routes.py:api_delete_paper()` | `routes.py` | `delete_paper(id)` |
| Reading status changed | `routes.py:api_set_reading_status()` | `routes.py` | `sync_paper(id)` |
| Notion sync (all) | `cli.py:_notion_sync()`, `routes.py:api_notion_sync()` | CLI/routes | `batch_sync(touched_ids)` |

**Return contract changes**:

1. **`storage.rename_tags()`** (`storage.py:193`): Already tracks `changed_paper_ids: set[str]` internally (line 211). Add `"changed_paper_ids": list(changed_paper_ids)` to return dict.

2. **`SyncReport`** (`notion.py:720`): Add `touched_paper_ids: set[str] = field(default_factory=set)`. Populated in `_set_local_from_remote()`, `_import_remote_only()`, and archive propagation alongside existing `report.actions.append()` calls.

## SearchManager factory

```python
# search.py
def get_search_manager(config: Config) -> SearchManager | None:
    """Return SearchManager if qmd is enabled and available, else None."""
    if not config.qmd_enabled:
        return None
    mgr = SearchManager(config)
    if not mgr.is_available():
        return None
    return mgr
```

All callers use `search_mgr = get_search_manager(config)` then guard with `if search_mgr:`. This is the single construction point — no caller invents its own setup logic.

## SearchManager API

```python
class SearchManager:
    def __init__(self, config: Config):
        self._config = config
        self._available: bool | None = None  # cached on first check

    def is_available(self) -> bool:
        """Check qmd binary exists. Cached after first call — no repeated shell-outs."""

    def setup(self) -> None:
        """Idempotent: create collection if not already present.
        Exact mechanism (check existing collections or tolerate duplicate-add as success)
        determined during Step 0a verification."""

    def sync_paper(self, paper_id: str, storage: StorageManager) -> None:
        """Regenerate search doc for one paper, then `qmd --index <name> update`.
        Ensures search/ dir exists before writing.
        No-op if paper has no summary_path."""

    def delete_paper(self, paper_id: str) -> None:
        """Remove search/{paper_id}.md, then `qmd --index <name> update`."""

    def batch_sync(self, paper_ids: Iterable[str], storage: StorageManager) -> None:
        """Regenerate search docs for multiple papers, single `qmd update` at the end.
        Ensures search/ dir exists before writing.
        Used by tag rename and Notion sync to avoid repeated reindexing."""

    def rebuild_all(self, storage: StorageManager) -> None:
        """Regenerate ALL search docs from index.json + summary files, single `qmd update`.
        Ensures search/ dir exists before writing."""

    def generate_embeddings(self) -> None:
        """Run `qmd --index <name> embed`. Expensive, user-triggered only."""

    def search(self, query: str, limit: int = 10, mode: str = "text") -> list[SearchResult]:
        """
        mode="text"   → `qmd search` (BM25, always works)
        mode="vector"  → `qmd vsearch` (requires embeddings)
        mode="hybrid"  → `qmd query` (requires embeddings)
        If vector/hybrid fails due to missing embeddings, raise
        EmbeddingsNotAvailableError with setup hint. No silent fallback.
        """

    def _run_qmd(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run config.qmd_command + ["--index", index_name] + args
        with cwd=config.data_dir."""
```

`SearchResult` is provisional until Step 0a confirms the real `qmd --json` output schema. Expected fields: `paper_id` (extracted from result file path), `title`, `score`, `snippet`. Actual field mapping locked after verification.

## Phased plan

### Phase 0: Prerequisites — verify qmd, config changes, return contracts

**Step 0a**: Install qmd and verify before writing any code:
1. Exact JSON schema of `qmd search --json` / `qmd query --json` output → lock `SearchResult` fields.
2. Behavior of `qmd vsearch`/`qmd query` when no embeddings exist (error code? stderr?).
3. Confirm `--index <name>` creates a named SQLite file and isolates state.
4. Confirm `qmd collection add` behavior when collection already exists (for `setup()` idempotency strategy).
5. Confirm `qmd update` is incremental (only changed files).
6. Verify npm/npx install paths if planning to document them.

**Step 0b**: Modify existing files:
- `src/paper_assistant/config.py` — add qmd fields; make `anthropic_api_key` optional (store `None`, no ValueError)
- `src/paper_assistant/summarizer.py` — validate API key at point of use (raise clear error message)
- `src/paper_assistant/storage.py` — add `"changed_paper_ids": list(changed_paper_ids)` to `rename_tags()` return
- `src/paper_assistant/notion.py` — add `touched_paper_ids: set[str]` to `SyncReport`; populate in mutation paths

**Step 0c**: Create new files:
- `src/paper_assistant/search.py` — `SearchManager`, `SearchResult`, `get_search_manager()`, exceptions
- `tests/test_search.py` — unit tests for SearchManager (all subprocess mocked)

### Phase 1: CLI search + index management commands

**Files:**
- `src/paper_assistant/cli.py` — add `search`, `index-setup`, `index-rebuild`

```
paper-assist search "attention mechanisms" [--limit 10] [--mode text|vector|hybrid] [--json]
paper-assist index-setup      # idempotent: create collection + rebuild all search docs + qmd update
paper-assist index-rebuild [--embed]  # regenerate all search docs + qmd update [+ qmd embed]
```

- Default mode: `text` (BM25, always works).
- `--json` outputs JSON array for programmatic use.
- When qmd disabled/unavailable: print setup message, exit non-zero.
- `--mode vector`/`--mode hybrid` without embeddings → setup hint, exit code 1.

### Phase 2: Search doc maintenance across all mutation paths

**Files:**
- `src/paper_assistant/pipeline.py` — `sync_paper()` after `import_paper_summary()` and `create_local_entry()`
- `src/paper_assistant/cli.py` — `sync_paper()` after direct CLI `add`; `delete_paper()` after `remove()`
- `src/paper_assistant/web/routes.py` — hooks after web add, summary edit, tag add/remove, delete, reading status change
- Notion sync callers (`cli.py:_notion_sync()`, `routes.py:api_notion_sync()`) — `batch_sync(report.touched_paper_ids)`

**Guard pattern** (consistent everywhere):
```python
if search_mgr:
    try:
        search_mgr.sync_paper(paper_id, storage)
    except Exception:
        logger.warning("Search index update failed for %s", paper_id)
```

**Batch operations**:
- `api_rename_tags()` → `batch_sync(result["changed_paper_ids"], storage)`
- Notion sync → `batch_sync(report.touched_paper_ids, storage)`

### Phase 3: Web UI search bar

**Files:**
- `src/paper_assistant/web/routes.py` — `GET /api/search?q=&limit=&mode=`
- `src/paper_assistant/web/templates/index.html` — search input + results overlay
- `src/paper_assistant/web/static/style.css` — search bar styling

`search_available` computed in the index route handler, passed via `TemplateResponse` context. When unavailable, search bar hidden. Error responses use existing `{"error": ...}` JSON pattern. No API key required.

### Phase 4: Skill workflow + MCP

**Files:**
- `skills/codex/summarize-paper/SKILL.md` — related-paper lookup step
- `CLAUDE.md` — qmd invariants
- `README.md` — setup documentation

**Skill workflow v1**: `paper-assist search --json "<paper title>" --limit 5` before summary generation. CLI call, no MCP needed.

**MCP setup** (documented, not wrapped):
```json
{
  "mcpServers": {
    "paper-library": {
      "command": "qmd",
      "args": ["--index", "paper-assistant", "mcp"],
      "cwd": "~/.paper-assistant"
    }
  }
}
```

## Key invariants

1. **qmd is optional.** `get_search_manager(config)` returns `None` when disabled or unavailable. All hooks guard with `if search_mgr:`.
2. **No qmd in pyproject.toml.** Installed separately (bun verified; npm/npx verify-first).
3. **Minimal existing code changes**: `rename_tags()` adds `changed_paper_ids` to return; `SyncReport` adds `touched_paper_ids`. `load_config()` makes API key optional. Hooks live in callers.
4. **Derived search docs** in `search/{paper_id}.md`. Never index raw summary files. Markdown section headers preserved; only YAML wrapper and duplicate title block stripped.
5. **Named index isolation**: every qmd invocation passes `--index <qmd_index_name>`.
6. **`qmd_command` is `list[str]`** internally. Env var is shell-style string → `shlex.split()`.
7. **`is_available()` cached** on SearchManager instance.
8. **Embeddings are explicit.** `--mode text` default always works. `--mode vector`/`--mode hybrid` → setup hint error, no silent fallback.
9. **Search paths don't require `ANTHROPIC_API_KEY`.** Lazy validation in `summarizer.py` only.
10. **Papers without summaries skipped.** `sync_paper()` is a no-op when `paper.summary_path is None`.
11. **Search failures never break primary operations.** When `get_search_manager()` returned `None`: no code runs. When SearchManager exists but operation fails: log warning, continue.
12. **`setup()` is idempotent.** Exact mechanism finalized after Step 0a verification. Safe to run repeatedly.
13. **Single-paper mutations use `sync_paper()`. Multi-paper mutations use `batch_sync()`.** One `qmd update` per batch, not per paper.
14. **`sync_paper()`, `batch_sync()`, and `rebuild_all()` ensure `search/` dir exists** before writing. Do not rely on `setup()` having run.
15. **`SearchResult` fields are provisional** until Step 0a confirms the real `qmd --json` schema.

## Test plan

**New file**: `tests/test_search.py`

| Category | Tests |
|---|---|
| `get_search_manager()` | Returns `None` when disabled; `None` when binary missing; `SearchManager` when available |
| `is_available()` caching | Shells out once, returns cached result on subsequent calls |
| Command construction | `_run_qmd()` builds `qmd_command + ["--index", name] + args`; CWD set correctly |
| JSON output parsing | Mock subprocess → verify SearchResult fields |
| Search doc generation | `sync_paper()` → correct front matter (tags, status, metadata) + preserved markdown body |
| Search doc dir creation | `sync_paper()` creates `search/` dir if missing |
| Paper without summary | `sync_paper()` → no-op, no file written |
| Search doc deletion | `delete_paper()` → file removed; missing file handled gracefully |
| `batch_sync()` | Writes N docs, runs `qmd update` exactly once |
| `rebuild_all()` | One doc per paper with summary; papers without summaries skipped |
| `setup()` idempotency | Second call succeeds (collection already exists) |
| Degraded: `get_search_manager()` None | Mutation code path does not call any search methods; no log output |
| Degraded: operation fails | Log warning emitted; primary operation succeeds |
| Degraded: no embeddings | `search(mode="vector")` → `EmbeddingsNotAvailableError` with hint |
| CLI: qmd unavailable | `paper-assist search` → setup message, non-zero exit |
| CLI: no embeddings | `paper-assist search --mode vector` → setup hint, exit 1 |
| API: qmd unavailable | `/api/search` → `{"error": ...}`, HTTP 503 |
| API: no embeddings | `/api/search?mode=vector` → `{"error": ...}`, HTTP 400 |
| Tag rename | `rename_tags()` returns `changed_paper_ids`; `batch_sync()` called with them |
| Summary edit | `api_update_summary()` → search doc updated |
| Delete | `api_delete_paper()` → search doc removed |
| Notion sync | `SyncReport.touched_paper_ids` populated; `batch_sync()` called |

**Existing test file changes**:
- `tests/test_storage.py` — `rename_tags()` returns `changed_paper_ids`
- `tests/test_notion.py` — `SyncReport.touched_paper_ids` populated correctly
- `tests/test_cli_*.py` — `search`, `index-setup`, `index-rebuild` commands
- `tests/test_web_*.py` — `/api/search` endpoint; search bar hidden when unavailable

**All subprocess calls mocked.** No real qmd index in unit tests.

## Verification (end-to-end)

1. **Phase 0**: Install qmd, verify JSON schema, confirm `--index` isolation, lock `SearchResult` fields.
2. **Phase 1**: `paper-assist index-setup && paper-assist search "test query"` returns results. `paper-assist search` with qmd disabled → setup message + non-zero exit.
3. **Phase 2**: Add paper → searchable. Edit tags → search by tag. Delete → gone. Notion sync → search docs updated. Primary operations succeed even if qmd subprocess fails.
4. **Phase 3**: Web search bar works. Disabled qmd → bar hidden, API returns 503. No API key → search works.
5. **Phase 4**: `/summarize` skill includes related-paper lookup.
6. **All phases**: Existing commands work with `qmd_enabled=False` and without `ANTHROPIC_API_KEY` for read-only operations.
