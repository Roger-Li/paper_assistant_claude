# Paper Assistant — Developer Guide

This file is for AI assistants and developers working on the codebase. For user-facing setup and usage, see [README.md](README.md).

## Architecture

```
src/paper_assistant/
├── cli.py          # Click CLI: add, import, list, show, remove, serve, regenerate-feed
├── config.py       # Config from env vars / .env / CLI flags (Pydantic BaseModel)
├── models.py       # Paper, PaperMetadata, PaperIndex, ProcessingStatus
├── arxiv.py        # parse_arxiv_url(), fetch_metadata(), download_pdf() — arXiv Atom API
├── pdf.py          # extract_text_from_pdf() — pymupdf4llm
├── prompt.py       # System prompt template for Claude summarization
├── summarizer.py   # summarize_paper_text/pdf(), parse_summary_sections(), find_one_pager(), format_summary_file()
├── storage.py      # StorageManager (JSON index CRUD), make_*_filename() helpers
├── tts.py          # prepare_text_for_tts(), text_to_speech() — edge-tts
├── podcast.py      # generate_feed() — feedgen RSS generation
└── web/
    ├── app.py      # FastAPI app factory, mounts /static and /audio
    ├── routes.py   # create_router() — page + API routes
    ├── templates/  # Jinja2: index.html, paper.html
    └── static/     # CSS (pico.css), JS (marked.js, KaTeX)
```

## Data Directory

Default: `~/.paper-assistant/` (override: `PAPER_ASSIST_DATA_DIR`).

```
~/.paper-assistant/
├── papers/     # [Paper][{arxiv_id}] {title}.md
├── audio/      # {arxiv_id}.mp3
├── pdfs/       # {arxiv_id}.pdf
├── index.json  # Source of truth for all paper metadata
└── feed.xml    # iTunes-compatible RSS podcast feed
```

## Conventions & Gotchas

### Async everywhere
All I/O modules are async (httpx, edge-tts, anthropic SDK). CLI bridges with `asyncio.run()`.

### JSON index — no database
`index.json` is the single source of truth. `StorageManager` re-reads from disk on each access to support concurrent CLI/web usage. No SQLite.

### Re-fetch after save_summary (critical)
`storage.save_summary()` updates `summary_path` on the paper object it fetches internally. The caller's local `paper` variable is a different instance. Always do:
```python
storage.save_summary(arxiv_id, content)
paper = storage.get_paper(arxiv_id)  # re-fetch before further mutations
```

### Audio = full summary verbatim
TTS converts the **full** markdown summary to speech — not the one-pager section. `prepare_text_for_tts()` strips markdown formatting for natural speech but does not truncate content.

### `from __future__ import annotations`
Used in most modules. Pydantic models used as FastAPI request bodies **must** be at module level (not inside functions), otherwise `typing.get_type_hints()` fails and FastAPI returns 422.

### iCloud sync
Audio files auto-copy to `~/Library/Mobile Documents/com~apple~CloudDocs/Paper Assistant/`. Controlled by `PAPER_ASSIST_ICLOUD_SYNC` env var.

### Config resolution order
CLI flags > env vars > .env file > defaults.

## Web API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Paper list dashboard (HTML) |
| `GET` | `/paper/{arxiv_id}` | Paper detail page (HTML) |
| `POST` | `/api/add` | Add paper via full pipeline (JSON) |
| `POST` | `/api/import` | Import pre-generated summary (JSON) |
| `POST` | `/api/paper/{arxiv_id}/tags` | Add tags (JSON body: `{"tags": [...]}`) |
| `DELETE` | `/api/paper/{arxiv_id}/tags/{tag}` | Remove a tag |
| `DELETE` | `/api/paper/{arxiv_id}` | Delete a paper |
| `GET` | `/api/papers` | List papers (JSON, `?tag=` filter) |
| `GET` | `/feed.xml` | RSS podcast feed |
| `GET` | `/audio/{filename}` | Static audio files |

## Testing

```bash
pytest tests/
```

Tests cover: models, arxiv URL parsing, summarizer section parsing, TTS text preparation, storage CRUD, and web API endpoints. Web tests use `FastAPI.TestClient` with mocked async dependencies.

## TODO

### 1. Data migration / portability
Add a CLI command to copy the entire data directory (index.json + papers/ + audio/ + pdfs/) to a new location. Use case: move data to a different machine, back up to external drive, or reorganize folder structure.
```
paper-assist export --to /path/to/new/location
```
Should copy all files referenced in `index.json`, update paths if needed, and validate the result.

### 2. RSS feed / private podcast (not working end-to-end)
The podcast feed (`feed.xml`) is generated but **not usable from iPhone podcast apps** because:
- `podcast_base_url` defaults to `http://127.0.0.1:8877` — audio URLs in the feed point to localhost
- Podcast apps on iPhone can't reach `127.0.0.1` on the Mac
- Audio files are already synced to iCloud via the `icloud_sync` feature, but podcast apps need an HTTP URL

**Options to investigate:**
- Serve over local network (use Mac's LAN IP instead of localhost, e.g. `http://192.168.x.x:8877`) — works on same WiFi but breaks when IP changes
- Tailscale / ngrok tunnel for a stable URL
- Host audio on S3/Cloudflare R2 and generate feed with public URLs
- Use Apple Shortcuts or a local podcast app that reads from iCloud files directly

### 3. Batch import
Support importing multiple papers at once. Either:
- A text file with one arXiv URL per line + corresponding markdown files
- A directory scan that finds markdown files with arXiv IDs in the name

### 4. Regenerate audio for existing papers
Add `paper-assist regenerate-audio <arxiv_id>` (or `--all`) to re-generate audio for papers that were imported with `--skip-audio` or when TTS voice/rate settings change.

### 5. Search
Full-text search across paper summaries (title, abstract, content). Could be a CLI command (`paper-assist search "attention mechanism"`) and a search bar in the web UI. Since the index is JSON-based, a simple substring/regex search over summary files would work for the current scale.

### 6. Paper notes / annotations
Allow adding personal notes to papers beyond tags. Could be a separate markdown file or a `notes` field in the index.
