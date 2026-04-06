#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing Codex skill..."
mkdir -p "$HOME/.codex/skills"
ln -sfn "$REPO_ROOT/skills/codex/summarize-paper" \
  "$HOME/.codex/skills/summarize-paper"
echo "  → ~/.codex/skills/summarize-paper"

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
