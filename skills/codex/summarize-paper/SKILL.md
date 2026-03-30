---
name: "summarize-paper"
description: "Use when a user explicitly asks to summarize, import, or store an arXiv ML paper through the Paper Assistant workflow. Downloads the paper PDF, generates a structured summary following project instructions, and imports it into the local paper-assistant library with optional TTS audio and Notion sync."
---

Use this skill when the user wants a paper summarized and stored through Paper Assistant.

## Workflow

1. Parse the user's request for the arXiv URL or ID, plus any tags and flags such as `--sync-notion`, `--skip-audio`, and `--force`.
   Tags must be repeated flags, e.g. `--tags rl --tags agent`.
2. Read `prompts/paper_summary_instructions.md`.
3. If `--sync-notion` is requested, run `.venv/bin/paper-assist notion-preflight`
   before the rest of the workflow. If that fails, stop immediately.
4. Create a repo-local artifact directory:
   `.artifacts/summarize-paper/<id>/`
5. Download the PDF:
   `curl -sL -o .artifacts/summarize-paper/<id>/paper.pdf https://arxiv.org/pdf/<id>`
6. Prefer native PDF reading first.
   Fallback only if native PDF reading is unavailable or fails:
   `.venv/bin/paper-assist extract-text .artifacts/summarize-paper/<id>/paper.pdf --output .artifacts/summarize-paper/<id>/paper.md`
   then read the extracted markdown file instead.
7. Generate the summary from the tracked instructions.
   Adaptations for the saved document:
   - Omit `# Follow-ups` because it is interactive-only
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
8. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
9. Import it in the foreground:
   `.venv/bin/paper-assist skill-import <url> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model codex \
     [--tags ...] [--sync-notion] [--skip-audio] [--force] \
     --cleanup-file .artifacts/summarize-paper/<id>/paper.pdf \
     [--cleanup-file .artifacts/summarize-paper/<id>/paper.md] \
     --cleanup-file .artifacts/summarize-paper/<id>/summary.md \
     --json`
10. Parse the JSON output and report the result to the user.

## Error Handling

- `curl` failure: retry once, then stop and report the failure
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata; if arXiv still rate-limits the request, wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Notion sync failure: report it as a warning; the import itself succeeded
- Duplicate paper: report the command error, which suggests `--force` or sync-only
