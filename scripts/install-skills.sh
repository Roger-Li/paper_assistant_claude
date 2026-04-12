#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing Codex skill..."
mkdir -p "$HOME/.codex/skills"
ln -sfn "$REPO_ROOT/skills/codex/summarize-paper" \
  "$HOME/.codex/skills/summarize-paper"
echo "  → ~/.codex/skills/summarize-paper"

echo ""
echo "Kiro setup — skill is in-repo at .kiro/skills/summarize-paper.md"
echo "  No symlink needed. Ensure Kiro has terminal access for:"
echo "    hf papers info / hf papers read"
echo "    curl (PDF fallback)"
echo "    .venv/bin/paper-assist skill-import / extract-text"
echo "  Copy .env.work to .env on the work laptop: cp .env.work .env"

cat <<'GUIDE'

Claude Code setup — add these to .claude/settings.local.json
under permissions.allow:

  "Bash(hf papers info *)"
  "Bash(hf papers read *)"
  "Bash(curl -sL -o .artifacts/summarize-paper/*)"
  "Bash(.venv/bin/paper-assist skill-import *)"
  "Bash(.venv/bin/paper-assist extract-text *)"
  "Bash(.venv/bin/paper-assist notion-preflight)"

GUIDE
