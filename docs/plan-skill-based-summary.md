# Plan V4: Skill-Based Paper Summary Workflow

## Context

The user manually: (1) drops an arxiv link into ChatGPT/Claude with custom instructions, (2) copies the summary, (3) imports into Paper Assistant, (4) syncs to Notion. This plan automates all steps as a skill for both Claude Code and Codex. The agent's own LLM generates the summary, then a shared pipeline helper handles import, audio, sync, and cleanup.

## Architecture

```
Claude Code                          Codex
.claude/commands/summarize.md        skills/codex/summarize-paper/SKILL.md
        │  (thin adapter)                     │  (thin adapter)
        │                                     │
        ├── Read shared instructions          ├── Read shared instructions
        ├── Download PDF (curl)               ├── Download PDF (curl)
        ├── Read PDF (capability-based)       ├── Read PDF (capability-based)
        ├── Generate summary (LLM)            ├── Generate summary (LLM)
        ├── Write /tmp/summary_<id>.md        ├── Write /tmp/summary_<id>.md
        └─────────────┬──────────────────────-┘
                      ▼
     paper-assist skill-import <url>
       --file /tmp/summary_<id>.md
       --model claude-code
       [--model-version claude-opus-4-6]
       [--tags rl] [--sync-notion] [--force]
       --cleanup-file /tmp/paper_<id>.pdf
       --cleanup-file /tmp/summary_<id>.md
                      │
                      ▼
     pipeline.import_paper_summary()    ◄── ALSO called by `paper-assist import`
       ├── fetch metadata (arxiv or web)
       ├── duplicate check / force-merge
       ├── parse sections + save summary
       ├── re-fetch paper (storage invariant)
       ├── generate audio (unless --skip-audio)
       ├── update RSS
       ├── notion sync (if --sync-notion)
       └── return ImportResult
```

**Key design**: One shared import function in `pipeline.py` backs both `import` and `skill-import`. No parallel import pipelines to drift.

---

## Step 1: Create tracked prompt asset

**Create:** `src/paper_assistant/prompts/paper_summary_instructions.md`

Seed it with the current instruction content that currently lives in the local,
gitignored `.artifacts/` files.

**Rationale**: `.artifacts/` is scratch space and is ignored by git in this repo.
The skill instructions need to live in a tracked, reviewable location that both
Claude Code and Codex can read reliably.

**Migration**: Update manual ChatGPT/Claude/Codex project references to point to
`src/paper_assistant/prompts/paper_summary_instructions.md`. If the user wants to keep personal
copies or symlinks under `.artifacts/` for convenience, that can remain a local
workflow choice, but it is not repo-managed.

---

## Step 2: Unified import helper in `pipeline.py`

**File:** `src/paper_assistant/pipeline.py`

New function `import_paper_summary()` — the single import path for both CLI commands.

```python
class DuplicatePaperError(Exception):
    """Raised when importing a paper that already exists without --force."""
    def __init__(self, paper_id: str):
        self.paper_id = paper_id
        super().__init__(
            f"Paper {paper_id} already exists. Use --force to re-import, "
            f"or 'paper-assist notion-sync --paper {paper_id}' to sync only."
        )

@dataclass
class ImportResult:
    paper_id: str
    title: str
    summary_path: Path
    audio_path: Path | None
    model_used: str
    notion_synced: bool
    notion_error: str | None
    warnings: list[str]

async def import_paper_summary(
    *,
    config: Config,
    storage: StorageManager,
    url: str,
    markdown: str,
    model: str = "manual",
    tags: list[str] | None = None,
    skip_audio: bool = False,
    force: bool = False,
    sync_notion: bool = False,
) -> ImportResult:
```

### Pseudo-flow with storage invariants and force-merge semantics

1. **Detect source type**: `is_arxiv_url(url)` → branch to arxiv or web metadata fetch.
2. **Fetch metadata**: `fetch_metadata(arxiv_id)` or `fetch_article(url)` → `PaperMetadata`.
3. **Duplicate check / force-merge**:
   - If `storage.paper_exists(paper_id)` and not `force` → raise `DuplicatePaperError`.
   - If `storage.paper_exists(paper_id)` and `force` → load existing paper for merge (see **Force-Merge Policy** below).
   - If not exists → create fresh `Paper`.
4. **Parse sections**: `parse_summary_sections(markdown)`, `find_one_pager(sections)`.
5. **Create `SummarizationResult`**: `model_used=model` (deterministic label, not "manual").
6. **Build Paper object**: Either fresh or merged (see policy). Call `storage.add_paper(paper)`.
7. **Save summary**: `storage.save_summary(paper_id, formatted_content)`.
8. **⚠️ Re-fetch paper** (`paper = storage.get_paper(paper_id)`) — **required by storage invariant** (`save_summary` updates a different instance internally; CLAUDE.md invariant 1).
9. **Generate audio**: Per **Audio Policy** below. On failure, append warning (don't raise).
10. **Update RSS feed**: `generate_feed()`. On failure, append warning.
11. **Notion sync**: If `sync_notion`, call `sync_notion()` from `notion.py`. On failure, capture error in `ImportResult.notion_error` but don't raise.
12. **Return `ImportResult`**.

### Force-Merge Policy (`force=True` on existing paper)

When re-importing over an existing paper, the helper must **merge** rather than replace to avoid destroying sync linkage, reader state, and history.

**Fields preserved from existing paper:**
| Field | Rationale |
|---|---|
| `date_added` | Original add date is historical fact |
| `reading_status` | User's reading progress is manual state |
| `notion_page_id` | Destroying this creates orphaned Notion pages |
| `notion_modified_at` | Tied to the linked Notion page |
| `last_synced_at` | Tied to the linked Notion page |
| `archived_at` | User's archive decision is manual state |

**Fields replaced from new import:**
| Field | Rationale |
|---|---|
| `summary_path` | New summary replaces old |
| `model_used` | New model attribution |
| `status` | Reset to reflect new processing state |
| `error_message` | Clear old errors |
| `token_count` | From new summarization |
| `metadata` | Re-fetched (may have updated abstract, etc.) |

**Tag policy: union.**
New tags from `--tags` are merged into existing tags. Existing tags are never removed by re-import. Rationale: tags are a user-curated accumulation; removing them on re-import would be surprising. Users who want to remove tags use the dedicated tag API.

**Audio policy:**
| `--skip-audio` | Existing `audio_path` | Behavior |
|---|---|---|
| Yes | Exists | **Keep** existing audio_path unchanged |
| Yes | None | No audio (skip) |
| No | Exists | **Replace** — generate new audio, overwrite path |
| No | None | Generate new audio |

**`local_modified_at`**: Always updated to `now()` on re-import (content changed).

**Implementation sketch:**
```python
if force and storage.paper_exists(paper_id):
    existing = storage.get_paper(paper_id)
    paper = Paper(
        metadata=new_metadata,
        date_added=existing.date_added,           # preserve
        reading_status=existing.reading_status,     # preserve
        notion_page_id=existing.notion_page_id,     # preserve
        notion_modified_at=existing.notion_modified_at,  # preserve
        last_synced_at=existing.last_synced_at,     # preserve
        archived_at=existing.archived_at,           # preserve
        tags=list(set(existing.tags) | set(tags or [])),  # union
        audio_path=existing.audio_path if skip_audio else None,  # conditional
        status=ProcessingStatus.PENDING,            # replace
        model_used=model,                           # replace
    )
else:
    paper = Paper(metadata=new_metadata, tags=list(tags or []), ...)
```

### Migration

Refactor `_import_arxiv_paper()` and `_import_web_article()` in `cli.py` to call `import_paper_summary()`. This eliminates ~120 lines of duplicated inline import logic and prevents the two paths from drifting.

**Follow-up (out of scope):** `POST /api/import` in `web/routes.py` (line ~357) still has its own inline import logic. Explicitly tracked as follow-up cleanup so the repo converges to one import path eventually.

---

## Step 3: `--model` flag on existing `import` command

**File:** `src/paper_assistant/cli.py`

Add to the `import` command:
```python
@click.option("--model", default=None,
    help="Model that generated this summary (e.g., 'claude-code'). Default: 'manual'.")
```

The `import` command now calls `pipeline.import_paper_summary()` with `model=model or "manual"`. This replaces the inline import logic in `_import_arxiv_paper` / `_import_web_article`.

---

## Step 4: `skill-import` CLI command

**File:** `src/paper_assistant/cli.py`

```
paper-assist skill-import <url>
  --file SUMMARY_PATH      (required)
  --model MODEL_LABEL      (required, e.g., "claude-code" or "codex")
  [--model-version VER]    (optional, e.g., "claude-opus-4-6")
  [--tags TAG ...]
  [--sync-notion]
  [--skip-audio]
  [--force]
  [--cleanup-file PATH]    (repeatable, temp files to delete on success)
  [--json]                 (output JSON instead of human-readable)
```

**Provenance label**: `model_used` is set to `"{model}"` when no version is given, or `"{model}/{model_version}"` when both are provided (e.g., `"claude-code/claude-opus-4-6"`). Both are stable CLI flags — no agent self-identification.

**Duplicate policy**: Without `--force`, if the paper exists, the command exits with a clear error:
```
Paper 2503.10291 already exists. Use --force to re-import, or
'paper-assist notion-sync --paper 2503.10291' to sync only.
```
With `--force`, the merge policy from Step 2 applies.

**Cleanup safety**: `--cleanup-file` accepts repeated paths. Validation:
- Each path must be under `tempfile.gettempdir()` (typically `/tmp/` or platform equivalent)
- Each path must be a regular file (not directory, not symlink resolving outside temp)
- Files are only deleted after successful import
- On failure, files are preserved and paths printed for manual recovery

**JSON output** (when `--json`):
```json
{
  "paper_id": "2503.10291",
  "title": "Paper Title",
  "summary_path": "/Users/.../papers/[Paper][2503.10291] Title.md",
  "audio_path": "/Users/.../audio/2503.10291.mp3",
  "model_used": "claude-code/claude-opus-4-6",
  "notion_synced": true,
  "notion_error": null,
  "warnings": []
}
```

---

## Step 5: `extract-text` CLI command (PDF fallback)

**File:** `src/paper_assistant/cli.py`

```
paper-assist extract-text <pdf-path> [--max-pages 100] [--output FILE]
```

Thin wrapper around existing `pdf.py:extract_text_from_pdf()`. Writes extracted markdown to `--output` file (default: stdout). In skill workflows, always use `--output /tmp/paper_<id>.md` so the agent reads a file rather than parsing a long stdout blob.

---

## Step 6: Claude Code slash command

**File:** `.claude/commands/summarize.md`

Thin adapter:

```markdown
Usage: /summarize <arxiv-url-or-id> [--tags t1 t2] [--sync-notion] [--skip-audio] [--force]

## Workflow

1. Parse $ARGUMENTS for URL, tags, flags.
2. Read summary instructions from src/paper_assistant/prompts/paper_summary_instructions.md
3. Extract arxiv ID from URL. Download PDF:
   curl -sL -o /tmp/paper_<id>.pdf https://arxiv.org/pdf/<id>
4. Read the PDF using available capabilities.
   Fallback: if native PDF reading is unavailable or fails, run:
   .venv/bin/paper-assist extract-text /tmp/paper_<id>.pdf --output /tmp/paper_<id>.md
   then read the extracted markdown file instead.
5. Generate summary following the instructions read in step 2.
   Adaptations for non-interactive saved document:
   - Omit # Follow-ups section (interactive-only)
   - # My-Level Adaptation profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
      theoretical contributions, comparison with prior work, open questions)
6. Write summary to /tmp/summary_<id>.md (no YAML front matter).
7. Import and complete:
   .venv/bin/paper-assist skill-import <url> \
     --file /tmp/summary_<id>.md \
     --model claude-code \
     [--tags ...] [--sync-notion] [--skip-audio] [--force] \
     --cleanup-file /tmp/paper_<id>.pdf \
     --cleanup-file /tmp/summary_<id>.md \
     --json
8. Report results from JSON output to the user.

## Error Handling
- curl failure: retry once, then report error and stop
- PDF read failure: fall back to extract-text --output
- Import failure: report error, note temp files preserved for manual recovery
- Notion sync failure: report as warning, import itself succeeded
- Duplicate paper: report the error message, which suggests --force or sync-only
```

---

## Step 7: Codex skill (in-repo source)

**File:** `skills/codex/summarize-paper/SKILL.md`

```yaml
---
name: "summarize-paper"
description: "Use when a user explicitly asks to summarize, import, or store an arXiv
ML paper through the Paper Assistant workflow. Downloads the paper PDF, generates a
structured summary following project instructions, and imports it into the local
paper-assistant library with optional TTS audio and Notion sync."
---
```

Body: same workflow as Claude Code command, adapted for Codex conventions:
- Same reference to `src/paper_assistant/prompts/paper_summary_instructions.md`
- Same PDF strategy (capability-based first, `extract-text --output` fallback)
- Same adaptations (omit Follow-ups, ML engineer + researcher profile)
- `--model codex` instead of `--model claude-code`
- Same `paper-assist skill-import` call, same JSON output handling
- Same error handling and duplicate guidance

---

## Step 8: Install/setup script

**File:** `scripts/install-skills.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Install Codex skill (symlink so edits in-repo propagate)
echo "Installing Codex skill..."
mkdir -p "$HOME/.codex/skills"
ln -sfn "$REPO_ROOT/skills/codex/summarize-paper" \
  "$HOME/.codex/skills/summarize-paper"
echo "  → ~/.codex/skills/summarize-paper"

# Print Claude Code permission guidance
cat <<'GUIDE'

Claude Code setup — add these to .claude/settings.local.json
under permissions.allow:

  "Bash(curl -sL -o /tmp/paper_*)"
  "Bash(.venv/bin/paper-assist skill-import *)"
  "Bash(.venv/bin/paper-assist extract-text *)"

GUIDE
```

---

## Step 9: Tests

### `tests/test_skill_import.py` (new)

Tests for `import_paper_summary()` and the `skill-import` CLI:

| Test | What it verifies |
|---|---|
| `test_import_happy_path` | Full flow: mock metadata → Paper entry, summary file, audio |
| `test_import_model_provenance` | `model="claude-code"` → `Paper.model_used == "claude-code"` |
| `test_import_model_with_version` | `model="codex/gpt-5.4"` → stored correctly |
| `test_import_refetch_after_save` | Audio/sync operate on re-fetched paper (invariant 1) |
| `test_import_duplicate_no_force` | Raises `DuplicatePaperError` |
| `test_import_duplicate_with_force` | Succeeds; preserves `date_added`, `reading_status`, `notion_page_id`, `last_synced_at`, `archived_at` |
| `test_force_merge_tags_union` | Existing `[ml]` + new `[rl]` → `[ml, rl]` (no duplicates) |
| `test_force_merge_audio_keep_on_skip` | `--skip-audio` + existing audio → audio_path preserved |
| `test_force_merge_audio_replace` | No `--skip-audio` → new audio generated, old path replaced |
| `test_import_sync_notion_called` | Notion sync called when flag set |
| `test_import_sync_notion_skipped` | Not called without flag |
| `test_import_notion_failure_nonfatal` | Import succeeds; `notion_error` populated |
| `test_skill_import_cli_json` | CLI `--json` outputs valid JSON matching ImportResult |
| `test_skill_import_cli_cleanup_success` | Temp files deleted after success |
| `test_skill_import_cli_cleanup_failure` | Temp files preserved after failure |
| `test_skill_import_cli_cleanup_rejects_nontmp` | Non-tmp path → error, not deleted |

### `tests/test_summarizer.py` (extend)

Fold section compatibility checks into the existing file:

| Test | What it verifies |
|---|---|
| `test_parse_custom_instruction_sections` | `# One-Pager`, `# Deep-Structure Map`, `# Critical Q&A`, `# My-Level Adaptation`, `# Reading List` → 5 sections |
| `test_find_one_pager_matches_custom_header` | `find_one_pager()` matches `# One-Pager` |

---

## Step 10: Documentation

**`README.md`** — Add "Skills" section:
- What the skill does and the end-to-end flow
- Setup: `scripts/install-skills.sh` + Claude Code permission additions
- Usage from Claude Code: `/summarize <url> [--tags ...] [--sync-notion]`
- Usage from Codex: "Summarize this paper through Paper Assistant: <url>"
- `skill-import` CLI reference (flags, JSON output, provenance, force-merge behavior)
- `extract-text` CLI reference

**`CLAUDE.md`** + **`AGENTS.md`** (kept in sync):
- Code map: add `pipeline.import_paper_summary()`, `ImportResult`, `DuplicatePaperError`
- CLI surface / command list: document `skill-import` and `extract-text`
- Critical invariant: note `--force` merge semantics under invariant 1b area
- Roadmap: mark item 13 as done

**`docs/design-workflow-optimization.md`**:
- Mark R4 (`/summarize`) as implemented
- Note deviations: custom instructions (not `prompt.py`), unified import helper, deterministic provenance

---

## Files Summary

| File | Action | Purpose |
|---|---|---|
| `src/paper_assistant/prompts/paper_summary_instructions.md` | **Create** | Tracked shared core instructions for Claude Code, Codex, and manual workflows |
| `src/paper_assistant/pipeline.py` | **Modify** | Add `ImportResult`, `DuplicatePaperError`, `import_paper_summary()` with force-merge |
| `src/paper_assistant/cli.py` | **Modify** | Add `--model` to import, refactor to call helper, add `skill-import` + `extract-text` |
| `.claude/commands/summarize.md` | **Create** | Claude Code slash command (thin adapter) |
| `skills/codex/summarize-paper/SKILL.md` | **Create** | Codex skill source (in-repo, symlinked to `~/.codex/`) |
| `scripts/install-skills.sh` | **Create** | Install Codex skill + print Claude Code permission guidance |
| `tests/test_skill_import.py` | **Create** | Tests for shared helper, force-merge, and CLI |
| `tests/test_summarizer.py` | **Extend** | Section compatibility tests |
| `README.md` | **Modify** | Document skill setup and usage |
| `CLAUDE.md` | **Modify** | Update code map, command surface, invariants, roadmap |
| `AGENTS.md` | **Modify** | Keep in sync with CLAUDE.md |
| `docs/design-workflow-optimization.md` | **Modify** | Mark R4 as implemented |

## Follow-up (explicitly out of scope)

- **`POST /api/import` in `web/routes.py`** (~line 357): Still has inline import logic. Should be migrated to call `import_paper_summary()` in a future cleanup PR to complete the single-import-path convergence.
- **Manual workflow cleanup**: After updating personal ChatGPT/Claude project configs, delete or ignore any old local `.artifacts/` copies if they are no longer needed.
- **`--model-version` auto-detection**: If a reliable way to inject exact model versions emerges, can layer it in as a default for `--model-version`.

## Critical Dependencies (read-only)

| File | Role |
|---|---|
| `src/paper_assistant/prompts/paper_summary_instructions.md` | Shared core instructions read at runtime by both skills |
| `src/paper_assistant/summarizer.py` | `parse_summary_sections()`, `find_one_pager()`, `format_summary_file()` |
| `src/paper_assistant/notion.py` | `sync_notion()` for optional sync |
| `src/paper_assistant/pdf.py` | `extract_text_from_pdf()` for fallback |
| `src/paper_assistant/arxiv.py` | `parse_arxiv_url()`, `fetch_metadata()` |
| `src/paper_assistant/web_article.py` | `is_arxiv_url()`, `fetch_article()` |
| `src/paper_assistant/storage.py` | `add_paper()` does full replacement — merge logic is caller's job |

## Verification

1. **Unit tests**: `pytest tests/test_skill_import.py tests/test_summarizer.py -v`
2. **Full suite**: `pytest tests/` — verify no regressions from import refactor
3. **E2E Claude Code**: `/summarize https://arxiv.org/abs/2503.10291 --tags rl --sync-notion` → paper in web UI, all sections, audio, Notion page, `model_used = "claude-code"`
4. **E2E Codex**: "Summarize this paper through Paper Assistant: <url>" → same, `model_used = "codex"`
5. **Fallback**: `paper-assist extract-text /tmp/test.pdf --output /tmp/test.md` → readable markdown
6. **Provenance**: `index.json` shows `"model_used": "claude-code"` (not "manual")
7. **Force re-import**: Run twice, second with `--force` → verify `date_added`, `reading_status`, `notion_page_id` preserved; tags unioned; new summary/audio/model replaced
8. **Force audio preservation**: `--force --skip-audio` on paper with existing audio → audio_path unchanged
9. **Duplicate without force**: Clear error with actionable guidance
10. **Cleanup safety**: `--cleanup-file /tmp/paper_X.pdf` deleted on success; non-tmp path rejected
