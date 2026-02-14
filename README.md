# Paper Assistant

AI-powered ML research paper summarizer with podcast generation.

## Overview

Paper Assistant takes an arXiv URL, fetches metadata and PDF, generates a structured markdown summary with Claude, optionally creates narrated audio, and maintains a local podcast feed.

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

## Data Directory Layout

Default path: `~/.paper-assistant/`

```text
~/.paper-assistant/
├── papers/     # [Paper][{arxiv_id}] {title}.md
├── audio/      # {arxiv_id}.mp3
├── pdfs/       # {arxiv_id}.pdf
├── index.json  # Source of truth for paper metadata/state
└── feed.xml    # RSS feed
```

## CLI Commands

| Command | Description |
|---|---|
| `paper-assist add <url>` | Full pipeline: fetch -> summarize -> audio -> feed |
| `paper-assist import <url>` | Import pre-written markdown summary |
| `paper-assist list` | List papers (`--status`, `--tag`) |
| `paper-assist show <arxiv_id>` | Print summary in terminal |
| `paper-assist remove <arxiv_id>` | Remove paper (`--keep-files` supported) |
| `paper-assist serve` | Start local web app |
| `paper-assist regenerate-feed` | Rebuild RSS feed from index |

## Common Workflows

### 1. Add a paper with tags

```bash
paper-assist add https://arxiv.org/abs/2503.10291 -t multimodal -t rl
```

Useful flags:
- `--native-pdf`: send raw PDF to Claude instead of extracted text
- `--skip-audio`: skip TTS generation
- `--force`: re-process if already present

### 2. Import your own summary

```bash
# macOS clipboard mode (default)
paper-assist import https://arxiv.org/abs/2503.10291 -t survey

# cross-platform mode
paper-assist import https://arxiv.org/abs/2503.10291 --file summary.md
```

### 3. Filter and inspect

```bash
paper-assist list --status complete --tag multimodal
paper-assist show 2503.10291
```

### 4. Run web UI on another host/port

```bash
paper-assist serve --host 0.0.0.0 --port 8877
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

Features:
- **Sorting**: click "Sort by" links on the papers list to sort by date added, title, tag, or arXiv ID
- **Filtering**: filter papers by processing status, reading status, or tag
- **Reading status**: mark papers as unread/read/archived directly from the list page via inline dropdown
- **Edit summary**: on a paper detail page, click "Edit Summary" to modify the markdown and optionally regenerate audio

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
```

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

## Development

```bash
source .venv/bin/activate
pytest tests/
```

## License

MIT
