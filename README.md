# Paper Assistant

AI-powered ML research paper summarizer with podcast generation and Notion sync.

## Overview

Paper Assistant takes an arXiv ID, an arXiv or Hugging Face paper URL, any web article URL, or a local markdown-backed note, generates or stores a structured markdown summary, optionally creates narrated audio, and maintains a local podcast feed.

It can also sync papers to a Notion database (manual two-way sync for summary/tags/reading status) so pages are easy to share and listen to across devices.

Use it from:
- CLI (`paper-assist`)
- Local web UI (`paper-assist serve`)

## Platform Support

- Primary: macOS
- Also supported: Linux (with a few caveats)

Platform notes:
- Clipboard import without `--file` uses `pbpaste` (macOS command).
- On Linux, use `paper-assist import ... --file summary.md`.
- iCloud audio sync is macOS-specific unless you override `PAPER_ASSIST_ICLOUD_DIR`.
- Browser Reader Mode on the paper detail page is a client-side Web Speech feature, optimized for desktop Brave/Chromium, and it prefers default/local natural voices exposed by the browser. In Reader Mode, technical blocks stay visible, but only prose is read aloud. Use `K` or `Space` to pause/resume and `Escape` to stop.

## Quick Start (pip + venv)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

paper-assist add https://arxiv.org/abs/2503.10291
paper-assist serve
```

Then open `http://127.0.0.1:8877`.

## Optional Setup (uv)

If you prefer `uv`, this is an equivalent path:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Configuration

Configuration resolution order is:
1. CLI flags
2. Environment variables
3. `.env`
4. Defaults

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | none | Required for summarization. |
| `PAPER_ASSIST_DATA_DIR` | No | `~/.paper-assistant` | Overrides the full data root. |
| `PAPER_ASSIST_MODEL` | No | `claude-sonnet-4-20250514` | Claude model used for summaries. |
| `PAPER_ASSIST_TTS_VOICE` | No | `en-US-AriaNeural` | Voice for generated narration. |
| `PAPER_ASSIST_ICLOUD_SYNC` | No | `true` | `true/1/yes` enables iCloud audio copy. |
| `PAPER_ASSIST_ICLOUD_DIR` | No | `~/Library/Mobile Documents/com~apple~CloudDocs/Paper Assistant` | iCloud destination folder. |
| `PAPER_ASSIST_ARXIV_USER_AGENT` | No | `paper-assistant/0.1 (...)` | Set app name + contact email for arXiv API requests. |
| `PAPER_ASSIST_ARXIV_MAX_RETRIES` | No | `6` | Retry attempts for arXiv `429`, `5xx`, and transient network errors. |
| `PAPER_ASSIST_ARXIV_BACKOFF_BASE_SECONDS` | No | `2.0` | Base delay for exponential backoff (with jitter). |
| `PAPER_ASSIST_ARXIV_BACKOFF_CAP_SECONDS` | No | `90.0` | Max delay cap for exponential backoff. |
| `PAPER_ASSIST_QMD_ENABLED` | No | `false` | Enable qmd-based search. |
| `PAPER_ASSIST_QMD_COMMAND` | No | `qmd` | Shell-style command to invoke qmd (e.g. `npx @tobilu/qmd`). |
| `PAPER_ASSIST_QMD_INDEX` | No | `paper-assistant` | Named qmd index for isolation. |
| `PAPER_ASSIST_QMD_COLLECTION` | No | `papers` | qmd collection name. |
| `PAPER_ASSIST_NOTION_SYNC_ENABLED` | No | `false` | Enable manual Notion sync features. |
| `PAPER_ASSIST_NOTION_TOKEN` | No* | none | Notion integration token (*required when sync is enabled). |
| `PAPER_ASSIST_NOTION_DATABASE_ID` | No* | none | Target Notion database ID (*required when sync is enabled). |
| `PAPER_ASSIST_NOTION_ARCHIVE_ON_DELETE` | No | `true` | Archive linked Notion pages when local side is archived. |

## Data Directory Layout

Default path: `~/.paper-assistant/`

```text
~/.paper-assistant/
├── papers/     # [Paper][{paper_id}] {title}.md / [Note][{paper_id}] {title}.md
├── audio/      # {paper_id}.mp3
├── pdfs/       # {arxiv_id}.pdf (arXiv papers only)
├── search/     # {paper_id}.md — derived search docs (auto-managed by qmd integration)
├── index.json  # Source of truth for paper metadata/state
└── feed.xml    # RSS feed
```

For arXiv papers, `paper_id` is the arXiv ID (e.g., `2503.10291`). For web articles, it is a URL-derived slug (e.g., `thinkingmachines-ai-blog-on-policy-distillation`). For local notes, it is a title-derived slug (e.g., `my-reading-note`).

## CLI Commands

| Command | Description |
|---|---|
| `paper-assist add <url-or-id>` | Full pipeline: fetch -> summarize -> audio -> feed (arXiv ID, arXiv/HF paper URL, or web URL) |
| `paper-assist import <url-or-id>` | Import pre-written markdown summary (arXiv ID, arXiv/HF paper URL, or web URL, optional `--model`) |
| `paper-assist skill-import <url-or-id>` | Agent-oriented import with deterministic provenance, cleanup, and JSON output |
| `paper-assist extract-text <pdf-path>` | Extract PDF text to markdown for skill fallback workflows |
| `paper-assist create --title ...` | Create a local markdown-backed note or article bookmark |
| `paper-assist list` | List papers (`--status`, `--tag`) |
| `paper-assist show <paper_id>` | Print summary in terminal |
| `paper-assist remove <paper_id>` | Remove paper (`--keep-files` supported) |
| `paper-assist serve` | Start local web app |
| `paper-assist regenerate-feed` | Rebuild RSS feed from index |
| `paper-assist search "<query>"` | Search papers (hybrid by default; `--mode text\|vector\|hybrid`, `--limit`, `--json`) |
| `paper-assist index-setup` | Create qmd collection and rebuild all search docs |
| `paper-assist index-rebuild` | Regenerate all search docs (`--embed` to also generate embeddings) |
| `paper-assist notion-preflight` | Verify the configured Notion database is reachable/shared |
| `paper-assist notion-sync` | Manual two-way sync with Notion (`--paper`, `--dry-run`) |

## Common Workflows

### 1. Add a paper or article with tags

```bash
# arXiv paper
paper-assist add 2503.10291 -t multimodal -t rl
paper-assist add https://arxiv.org/abs/2503.10291 -t multimodal -t rl
paper-assist add https://huggingface.co/papers/2503.10291 -t multimodal -t rl

# Web article (blog post, technical article, etc.)
paper-assist add https://thinkingmachines.ai/blog/on-policy-distillation/ -t distillation
```

Useful flags:
- `--native-pdf`: when PDF fallback is needed, send raw PDF to Claude instead of extracted text (arXiv only)
- `--skip-audio`: skip TTS generation
- `--force`: re-process if already present

### 2. Import your own summary

```bash
# arXiv paper (macOS clipboard mode)
paper-assist import https://arxiv.org/abs/2503.10291 -t survey

# Web article with file
paper-assist import https://example.com/blog/post --file summary.md --model claude-code

# cross-platform mode
paper-assist import https://arxiv.org/abs/2503.10291 --file summary.md
```

### 3. Filter and inspect

```bash
paper-assist list --status complete --tag multimodal
paper-assist show 2503.10291
paper-assist show thinkingmachines-ai-blog-on-policy-distillation
```

### 4. Create a local markdown note

```bash
# Read markdown from clipboard on macOS
paper-assist create --title "Reading Note - Policy Distillation" -t reading-list

# Cross-platform mode with a file and optional bookmark URL
paper-assist create --title "Alignment Reading Note" \
  --source-url https://example.com/post \
  --file note.md \
  -t notes
```

### 5. Run web UI on another host/port

```bash
paper-assist serve --host 0.0.0.0 --port 8877
```

### 6. Notion sync (manual)

```bash
# preview sync actions
paper-assist notion-sync --dry-run

# run sync for all papers
paper-assist notion-sync

# run sync for one paper
paper-assist notion-sync --paper 2503.10291
```

## Skills

The skill-based workflow automates the manual loop of reading a paper, generating a structured summary, importing it into Paper Assistant, optionally creating audio, and optionally syncing the final record to Notion. Both the Claude Code command and the Codex skill read the same tracked instructions from `prompts/paper_summary_instructions.md`, then hand the finished markdown to `paper-assist skill-import`.

### Setup

```bash
./scripts/install-skills.sh
```

The installer symlinks the in-repo Codex skill into `~/.codex/skills/` and prints the Claude Code permission entries needed for:
- `hf papers info` + `hf papers read` (metadata + primary paper fetch)
- `curl` PDF download (fallback)
- `paper-assist skill-import`
- `paper-assist extract-text`
- `paper-assist notion-preflight`

### Claude Code

Use:

```text
/summarize <arxiv-url-or-id> [--tags ...] [--no-sync-notion] [--skip-audio] [--force]
```

The command fetches metadata via `hf papers info`, fetches the paper body via `hf papers read` (falling back to PDF download), reads `prompts/paper_summary_instructions.md`, writes `.artifacts/summarize-paper/<id>/summary.md`, and finishes through `paper-assist skill-import`. Notion sync is now on by default for this workflow; pass `--no-sync-notion` only when you intentionally want a local-only run.
Bare arXiv IDs like `2503.10291`, canonical arXiv URLs like `https://arxiv.org/abs/2503.10291`, and HF paper URLs like `https://huggingface.co/papers/2503.10291` are all accepted; the workflow normalizes any of them to the arXiv ID and uses the Hugging Face paper route by default for retrieval.

### Codex

Ask:

```text
Summarize this paper through Paper Assistant: https://arxiv.org/abs/2503.10291
```

You can also pass just `2503.10291` or an HF paper URL like `https://huggingface.co/papers/2503.10291`. The in-repo `skills/codex/summarize-paper/SKILL.md` normalizes any accepted form to the arXiv ID, uses the Hugging Face paper route by default for retrieval, and then imports via the canonical arXiv abs URL while stamping provenance as `codex`. It also syncs Notion by default; say `--no-sync-notion` only when you want to opt out.

Both skills now use repo-local artifacts under `.artifacts/summarize-paper/<arxiv_id>/` instead of hardcoded `/tmp/...` paths. That keeps the intermediate PDF/markdown/summary files visible while the workflow is running, and `skill-import` can clean them up safely afterward because `.artifacts/` is an allowed cleanup root.

### `skill-import`

`paper-assist skill-import <url>` is the shared agent-facing import command. Key flags:
- `--file SUMMARY.md`: required markdown input
- `--model LABEL` and optional `--model-version VERSION`: stored as `model_used`, e.g. `codex/gpt-5.4`
- `--sync-notion`: runs a targeted Notion sync after import
- `--cleanup-file /path`: accepts files under Python's temp dir or the repo-local `.artifacts/` tree
- agent hard-wrap cleanup: ordinary prose paragraphs from Claude Code/Codex summaries are normalized to soft-wrapped Markdown before saving
- `--skip-audio`: preserves an existing `audio_path` on forced re-imports instead of regenerating
- `--force`: merges over an existing paper instead of replacing it
- `--json`: emits machine-readable output for agent wrappers

Force re-imports preserve `date_added`, `reading_status`, Notion linkage/timestamps, and `archived_at`; tags are unioned; existing audio is kept only when `--skip-audio` is set.

### `extract-text`

Use `paper-assist extract-text <pdf-path> [--max-pages 100] [--output FILE]` when a skill can download a PDF but cannot read it natively. This is a thin wrapper around the existing PDF-to-markdown extraction path, and the intended fallback is `--output .artifacts/summarize-paper/<id>/paper.md`.

### `notion-preflight`

Skill-based summary runs now sync Notion by default, so `paper-assist notion-preflight` is the check those workflows run before import. Use `--no-sync-notion` only when you intentionally want to skip that sync.

## Search (qmd)

Paper Assistant integrates with [qmd](https://github.com/tobi/qmd) for full-text and semantic search across your paper library.

### Setup

```bash
# Install qmd (bun)
bun install -g github:tobi/qmd

# Enable in .env
echo "PAPER_ASSIST_QMD_ENABLED=true" >> .env

# Initialize the search index and generate embeddings (required for hybrid search)
paper-assist index-setup
paper-assist index-rebuild --embed
```

### Usage

```bash
# Default: hybrid search (BM25 + vector + LLM re-ranking, requires embeddings)
paper-assist search "attention mechanisms"

# Generate embeddings (required for hybrid/vector; run once after index-setup, then after index-rebuild)
paper-assist index-rebuild --embed

# Text-only search (BM25, works without embeddings)
paper-assist search "reward models" --mode text

# Vector-only semantic search
paper-assist search "papers about reward shaping" --mode vector

# JSON output for programmatic use
paper-assist search "test query" --json
```

The default search mode is **hybrid** (BM25 + vector embeddings + LLM re-ranking) for best relevance. When embeddings are not yet generated, search automatically falls back to text (BM25) with a warning. Run `paper-assist index-rebuild --embed` to enable full hybrid search.

The web UI shows a search bar when qmd is enabled. The API endpoint is `GET /api/search?q=<query>&limit=10&mode=hybrid`.

Search docs are kept in sync automatically — adding, editing, or deleting papers updates the search index.

### MCP Server

qmd includes a built-in MCP server for Claude Code integration:

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

## Web UI and API

Start server:

```bash
paper-assist serve
```

Key URLs:
- UI list page: `GET /`
- Paper details: `GET /paper/{paper_id}`
- RSS feed: `GET /feed.xml`
- Search API: `GET /api/search?q=&limit=&mode=`
- Paper list JSON: `GET /api/papers`
- Bulk tag rename: `PUT /api/tags/rename`
- Notion sync preview: `GET /api/notion/sync/preview`
- Notion sync run: `POST /api/notion/sync`
- Local note create: `POST /api/create`

Features:
- **Search**: when qmd is enabled, a search bar appears on the papers list for instant full-text search; hidden when qmd is unavailable
- **Sorting**: click "Sort by" links on the papers list to sort by date added, title, tag, or arXiv ID
- **Filtering**: filter papers by processing status, reading status, or tag
- **Bulk tag edits**: from the list page, apply one or more `old => new` tag renames across all local papers; if the target tag already exists on a paper, the tags merge automatically
- **Reading status**: mark papers as unread/read/archived directly from the list page via inline dropdown
- **Reader Mode**: on a paper detail page, use browser-native "read from here" playback with sentence highlighting while keeping technical blocks visible; use `K` or `Space` to pause/resume and `Escape` to stop (separate from generated MP3 audio)
- **Edit summary**: on a paper detail page, click "Edit Summary" to modify the markdown and optionally regenerate audio
- **Notion sync**: run manual sync (preview/apply) from the list page

Minimal API examples:

```bash
# Add paper via API (query params)
curl -X POST "http://127.0.0.1:8877/api/add?url=https://arxiv.org/abs/2503.10291&skip_audio=true"

# Import markdown via API
curl -X POST "http://127.0.0.1:8877/api/import" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://arxiv.org/abs/2503.10291",
    "markdown": "# One Pager\\n...",
    "tags": ["manual"],
    "skip_audio": false
  }'

# Create a local note via API
curl -X POST "http://127.0.0.1:8877/api/create" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Local Reading Note",
    "source_url": "https://example.com/reference",
    "markdown": "# Notes\n...",
    "tags": ["reading-list"],
    "skip_audio": false
  }'

# List papers filtered by tag
curl "http://127.0.0.1:8877/api/papers?tag=manual"

# Rename tags across all local papers
curl -X PUT "http://127.0.0.1:8877/api/tags/rename" \
  -H "Content-Type: application/json" \
  -d '{
    "renames": [
      {"from_tag": "post-training", "to_tag": "Post-training"},
      {"from_tag": "Reranking", "to_tag": "Re-ranker"}
    ]
  }'

# Preview notion sync for one paper
curl "http://127.0.0.1:8877/api/notion/sync/preview?paper=2503.10291"

# Run notion sync
curl -X POST "http://127.0.0.1:8877/api/notion/sync" \
  -H "Content-Type: application/json" \
  -d '{"paper_id":"2503.10291","dry_run":false}'
```

## Notion Database Setup

Create a Notion database with these properties:
- `arxiv_id` (`rich_text`)
- `title` (`title`)
- `authors` (`rich_text`)
- `tags` (`multi_select`)
- `reading_status` (`select` with values `unread`, `read`, `archived`)
- `summary_last_modified` (`date`)
- `local_last_modified` (`date`)
- `archived` (`checkbox`)
- `source_slug` (`rich_text`) — **optional**, needed if you sync web articles or local notes to Notion
- `source_type` (`select`) — **optional**, values `arxiv`, `web`, `note`; preserves note vs web-article round-trip fidelity
- `source_url` (`rich_text`) — **optional**, preserves canonical/bookmark URLs for web articles and local notes

The `source_slug` column stores the URL-derived slug for web articles and the title-derived slug for local notes. Existing arXiv papers are unaffected — sync continues to join on `arxiv_id` for those.

If you skip `source_type`, Notion sync still works, but remote-only note pages will import back as web-style entries because `source_slug` is the only non-arXiv identifier. If you skip `source_url`, sync still works, but bookmark URLs will not round-trip back from Notion.

Set environment variables:

```bash
export PAPER_ASSIST_NOTION_SYNC_ENABLED=true
export PAPER_ASSIST_NOTION_TOKEN=secret_xxx
export PAPER_ASSIST_NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Sync rules (current implementation):
- Manual trigger only (CLI/web button).
- Two-way for summary/tags/reading status using last-write-wins timestamps.
- Notion page is linked per paper via `notion_page_id` in local index.
- Local audio is canonical; sync pushes local MP3 to Notion page when upload API is available.
- Archive propagates both ways via archive/reading-status state.

## Troubleshooting

### `ANTHROPIC_API_KEY is required`

Set it in `.env` or your shell environment:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### `pbpaste` not found

You are likely on Linux. Use file import:

```bash
paper-assist import <arxiv-url> --file summary.md
```

### arXiv `429` rate limit errors

arXiv can throttle API clients when request cadence is too high or clients are not clearly identified.

Paper Assistant now retries `429` and transient failures with exponential backoff and honors `Retry-After`
when arXiv provides it. For metadata lookups specifically, the import path now falls back to the arXiv abs page
immediately on a metadata `429` instead of exhausting the full API retry budget first. To reduce throttling risk,
set a descriptive User-Agent with contact info:

```bash
export PAPER_ASSIST_ARXIV_USER_AGENT="paper-assistant/0.1 (you@example.com)"
```

If retries are exhausted, wait for the suggested delay in the error and retry the import.

### Paper already exists

Use `--force` to merge a new import over the existing record. Re-import keeps the original `date_added`, reading state, Notion linkage, and archive state; tags are unioned; audio is only preserved when you also pass `--skip-audio`.

### Audio missing

Possible causes:
- You used `--skip-audio`
- TTS failed during processing

Feed can still generate without audio files. Re-run with audio enabled when needed.

### iPhone cannot play feed from `127.0.0.1`

`127.0.0.1` is only local to your Mac. If you need phone access, run server on reachable network host (or tunnel) and regenerate feed.

### iCloud sync warnings

If iCloud path is unavailable, either:
- disable with `PAPER_ASSIST_ICLOUD_SYNC=false`
- or set a valid `PAPER_ASSIST_ICLOUD_DIR`

### Notion sync `400 Bad Request`

Most common causes:
- Notion database property mismatch (wrong names/types).
- Integration not connected to the target database.
- Notion file upload constraints for audio attachment.

Notes:
- `paper-assist notion-preflight` is the fastest way to confirm the database is reachable/shared before a skill run.
- `paper-assist notion-sync --dry-run` only validates mapping/plan and does not upload files.
- Audio upload failures are reported as warnings and do not abort summary/tag/status sync.

## Development

```bash
source .venv/bin/activate
pytest tests/
```

### Documentation

- **[README.md](README.md)** — user-facing setup, configuration, workflows, troubleshooting
- **[CLAUDE.md](CLAUDE.md)** — agent/contributor playbook: critical invariants, workflow checklists, testing expectations
- **[docs/](docs/)** — design docs for implemented features and [roadmap](docs/roadmap.md)

## License

MIT
