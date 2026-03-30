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

  "Bash(curl -sL -o /tmp/paper_*)"
  "Bash(.venv/bin/paper-assist skill-import *)"
  "Bash(.venv/bin/paper-assist extract-text *)"

GUIDE
