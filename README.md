# Paper Assistant

AI-powered ML research paper summarizer with podcast generation and Notion sync.

## Overview

Paper Assistant takes an arXiv URL or any web article URL, fetches metadata and content, generates a structured markdown summary with Claude, optionally creates narrated audio, and maintains a local podcast feed.

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
| `PAPER_ASSIST_NOTION_SYNC_ENABLED` | No | `false` | Enable manual Notion sync features. |
| `PAPER_ASSIST_NOTION_TOKEN` | No* | none | Notion integration token (*required when sync is enabled). |
| `PAPER_ASSIST_NOTION_DATABASE_ID` | No* | none | Target Notion database ID (*required when sync is enabled). |
| `PAPER_ASSIST_NOTION_ARCHIVE_ON_DELETE` | No | `true` | Archive linked Notion pages when local side is archived. |

## Data Directory Layout

Default path: `~/.paper-assistant/`

```text
~/.paper-assistant/
├── papers/     # [Paper][{paper_id}] {title}.md
├── audio/      # {paper_id}.mp3
├── pdfs/       # {arxiv_id}.pdf (arXiv papers only)
├── index.json  # Source of truth for paper metadata/state
└── feed.xml    # RSS feed
```

For arXiv papers, `paper_id` is the arXiv ID (e.g., `2503.10291`). For web articles, it is a URL-derived slug (e.g., `thinkingmachines-ai-blog-on-policy-distillation`).

## CLI Commands

| Command | Description |
|---|---|
| `paper-assist add <url>` | Full pipeline: fetch -> summarize -> audio -> feed (arXiv or web URL) |
| `paper-assist import <url>` | Import pre-written markdown summary (arXiv or web URL) |
| `paper-assist list` | List papers (`--status`, `--tag`) |
| `paper-assist show <paper_id>` | Print summary in terminal |
| `paper-assist remove <paper_id>` | Remove paper (`--keep-files` supported) |
| `paper-assist serve` | Start local web app |
| `paper-assist regenerate-feed` | Rebuild RSS feed from index |
| `paper-assist notion-sync` | Manual two-way sync with Notion (`--paper`, `--dry-run`) |

## Common Workflows

### 1. Add a paper or article with tags

```bash
# arXiv paper
paper-assist add https://arxiv.org/abs/2503.10291 -t multimodal -t rl

# Web article (blog post, technical article, etc.)
paper-assist add https://thinkingmachines.ai/blog/on-policy-distillation/ -t distillation
```

Useful flags:
- `--native-pdf`: send raw PDF to Claude instead of extracted text (arXiv only)
- `--skip-audio`: skip TTS generation
- `--force`: re-process if already present

### 2. Import your own summary

```bash
# arXiv paper (macOS clipboard mode)
paper-assist import https://arxiv.org/abs/2503.10291 -t survey

# Web article with file
paper-assist import https://example.com/blog/post --file summary.md

# cross-platform mode
paper-assist import https://arxiv.org/abs/2503.10291 --file summary.md
```

### 3. Filter and inspect

```bash
paper-assist list --status complete --tag multimodal
paper-assist show 2503.10291
paper-assist show thinkingmachines-ai-blog-on-policy-distillation
```

### 4. Run web UI on another host/port

```bash
paper-assist serve --host 0.0.0.0 --port 8877
```

### 5. Notion sync (manual)

```bash
# preview sync actions
paper-assist notion-sync --dry-run

# run sync for all papers
paper-assist notion-sync

# run sync for one paper
paper-assist notion-sync --paper 2503.10291
```

## Web UI and API

Start server:

```bash
paper-assist serve
```

Key URLs:
- UI list page: `GET /`
- Paper details: `GET /paper/{arxiv_id}`
- RSS feed: `GET /feed.xml`
- Paper list JSON: `GET /api/papers`
- Notion sync preview: `GET /api/notion/sync/preview`
- Notion sync run: `POST /api/notion/sync`

Features:
- **Sorting**: click "Sort by" links on the papers list to sort by date added, title, tag, or arXiv ID
- **Filtering**: filter papers by processing status, reading status, or tag
- **Reading status**: mark papers as unread/read/archived directly from the list page via inline dropdown
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

# List papers filtered by tag
curl "http://127.0.0.1:8877/api/papers?tag=manual"

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
- `source_slug` (`rich_text`) — **optional**, needed only if you sync web articles to Notion

The `source_slug` column stores the URL-derived slug for web articles. Existing arXiv papers are unaffected — sync continues to join on `arxiv_id` for those. If you don't add the column, arXiv sync works normally and web articles sync without the slug populated.

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
when arXiv provides it. To reduce throttling risk, set a descriptive User-Agent with contact info:

```bash
export PAPER_ASSIST_ARXIV_USER_AGENT="paper-assistant/0.1 (you@example.com)"
```

If retries are exhausted, wait for the suggested delay in the error and retry the import.

### Paper already exists

Use `--force` to overwrite processing for `add`/`import`.

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
- `paper-assist notion-sync --dry-run` only validates mapping/plan and does not upload files.
- Audio upload failures are reported as warnings and do not abort summary/tag/status sync.

## Development

```bash
source .venv/bin/activate
pytest tests/
```

## License

MIT
