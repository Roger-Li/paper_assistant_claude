# Paper Assistant

AI-powered ML research paper summarizer with podcast generation.

## What It Does

Takes an arXiv URL, fetches the paper, summarizes it via Claude, saves a structured markdown summary, generates a full-length audio narration, and maintains a private podcast RSS feed. Browse and manage everything through a local web UI.

## Quick Start

```bash
# Create a virtual environment and install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure your API key
cp .env.example .env   # Then edit .env and add your ANTHROPIC_API_KEY

# Add a paper (full pipeline: fetch -> summarize -> audio -> RSS)
paper-assist add https://arxiv.org/abs/2503.10291

# Or import a pre-generated summary from clipboard
paper-assist import https://arxiv.org/abs/2503.10291

# Browse papers in the web UI
paper-assist serve   # http://127.0.0.1:8877
```

## Running After Setup

Always activate the virtual environment first:

```bash
cd paper_assistant_claude
source .venv/bin/activate
paper-assist serve
```

## CLI Commands

| Command | Description |
|---|---|
| `paper-assist add <url>` | Full pipeline: fetch, summarize, TTS, RSS |
| `paper-assist import <url>` | Import a pre-generated summary from clipboard or file |
| `paper-assist list` | List papers (`--status`, `--tag` filters) |
| `paper-assist show <id>` | Print summary in terminal |
| `paper-assist remove <id>` | Delete paper and files |
| `paper-assist serve` | Start web UI at http://127.0.0.1:8877 |
| `paper-assist regenerate-feed` | Rebuild RSS feed |

### Import Command

The `import` command reads markdown from the macOS clipboard (`pbpaste`) by default. Useful when you generate summaries interactively with Claude Pro:

```bash
# Copy summary to clipboard, then:
paper-assist import https://arxiv.org/abs/2503.10291 -t multimodal -t rl

# Or read from a file:
paper-assist import https://arxiv.org/abs/2503.10291 --file summary.md

# Skip audio generation:
paper-assist import https://arxiv.org/abs/2503.10291 --skip-audio
```

## Web UI

Start with `paper-assist serve`, then open http://127.0.0.1:8877.

Features:
- Paper list with tag filtering
- Paper detail view with rendered markdown + LaTeX math
- Add paper form (triggers full pipeline)
- Import pre-generated summary (URL + markdown textarea)
- Tag management (add/remove tags on individual papers)
- Delete papers from the index
- Audio player for each paper
- RSS podcast feed at `/feed.xml`

## Testing

```bash
source .venv/bin/activate
pytest tests/
```

## License

MIT
