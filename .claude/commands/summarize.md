Usage: /summarize <arxiv-url-or-id> [--tags t1 --tags t2] [--no-sync-notion] [--skip-audio] [--force]

## Workflow

1. Parse `$ARGUMENTS` for URL, tags, and flags.
   Tags must be passed as repeated flags, e.g. `--tags rl --tags agent`.
   Default to syncing Notion unless the user explicitly passes `--no-sync-notion`.
2. Read summary instructions from `prompts/paper_summary_instructions.md`.
3. Unless `--no-sync-notion` is present, run `.venv/bin/paper-assist notion-preflight`
   before the paper workflow. If preflight fails, stop immediately and report it.
4. Extract the arXiv ID from the URL and create a repo-local artifact directory:
   `.artifacts/summarize-paper/<id>/`
5. Fetch the paper content using `hf papers read <id>` (HuggingFace CLI).
   Redirect stdout to a file to avoid shell output truncation on long papers:
   `hf papers read <id> > .artifacts/summarize-paper/<id>/paper.md`
   Then read `.artifacts/summarize-paper/<id>/paper.md` as the paper content.
   Fallback only if `hf papers read` fails or produces an empty file:
   a. Download the PDF:
      `curl -sL -o .artifacts/summarize-paper/<id>/paper.pdf https://arxiv.org/pdf/<id>`
   b. Prefer native PDF reading first.
   c. If native PDF reading fails, extract text:
      `.venv/bin/paper-assist extract-text .artifacts/summarize-paper/<id>/paper.pdf --output .artifacts/summarize-paper/<id>/paper.md`
      then read the extracted markdown file.
6. Generate a summary following the instructions from step 2.
   Adaptations for the saved document:
   - Omit `# Follow-ups` (interactive-only)
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
7. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
8. Import in the foreground and complete:
   `.venv/bin/paper-assist skill-import https://arxiv.org/abs/<id> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model claude-code \
     [--tags ...] --sync-notion [--skip-audio] [--force] \
     --cleanup-file .artifacts/summarize-paper/<id>/summary.md \
     --cleanup-file .artifacts/summarize-paper/<id>/paper.md \
     [--cleanup-file .artifacts/summarize-paper/<id>/paper.pdf] \
     --json`
   Always pass the arXiv URL (`https://arxiv.org/abs/<id>`) to `skill-import`,
   not the original HuggingFace or other URL, so that the paper_id resolves to
   the arXiv ID.
   Omit `--sync-notion` only when the user explicitly passed `--no-sync-notion`.
   `paper.md` is always created (by `hf papers read` or `extract-text`).
   Only include `--cleanup-file` for `paper.pdf` if the PDF fallback was used.
9. Report results from the JSON output to the user.

## Error Handling

- `hf papers read` failure: fall back to PDF download + native read + extract-text
- `curl` failure (in fallback path): retry once, then report error and stop
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata immediately on metadata `429`s instead of burning the full API retry budget; if arXiv is still rate-limiting after fallback, stop and wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Notion sync failure: report it as a warning; the import itself succeeded
- Duplicate paper: report the error message, which suggests `--force` or sync-only
