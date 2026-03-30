Usage: /summarize <arxiv-url-or-id> [--tags t1 --tags t2] [--sync-notion] [--skip-audio] [--force]

## Workflow

1. Parse `$ARGUMENTS` for URL, tags, and flags.
   Tags must be passed as repeated flags, e.g. `--tags rl --tags agent`.
2. Read summary instructions from `prompts/paper_summary_instructions.md`.
3. If `--sync-notion` is requested, run `.venv/bin/paper-assist notion-preflight`
   before the paper workflow. If preflight fails, stop immediately and report it.
4. Extract the arXiv ID from the URL and create a repo-local artifact directory:
   `.artifacts/summarize-paper/<id>/`
5. Download the PDF:
   `curl -sL -o .artifacts/summarize-paper/<id>/paper.pdf https://arxiv.org/pdf/<id>`
6. Prefer native PDF reading first.
   Fallback only if native PDF reading is unavailable or fails:
   `.venv/bin/paper-assist extract-text .artifacts/summarize-paper/<id>/paper.pdf --output .artifacts/summarize-paper/<id>/paper.md`
   then read the extracted markdown file instead.
7. Generate a summary following the instructions from step 2.
   Adaptations for the saved document:
   - Omit `# Follow-ups` (interactive-only)
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
8. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
9. Import in the foreground and complete:
   `.venv/bin/paper-assist skill-import <url> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model claude-code \
     [--tags ...] [--sync-notion] [--skip-audio] [--force] \
     --cleanup-file .artifacts/summarize-paper/<id>/paper.pdf \
     [--cleanup-file .artifacts/summarize-paper/<id>/paper.md] \
     --cleanup-file .artifacts/summarize-paper/<id>/summary.md \
     --json`
10. Report results from the JSON output to the user.

## Error Handling

- `curl` failure: retry once, then report error and stop
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata; if arXiv is still rate-limiting, wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Notion sync failure: report it as a warning; the import itself succeeded
- Duplicate paper: report the error message, which suggests `--force` or sync-only
