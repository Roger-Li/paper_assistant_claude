Usage: /summarize <arxiv-url-or-id> [--tags t1 --tags t2] [--no-sync-notion] [--skip-audio] [--skip-transcript] [--force]

## Workflow

1. Parse `$ARGUMENTS` for a bare arXiv ID, a full arXiv URL, or a Hugging Face paper URL, plus tags and flags.
   Normalize any accepted input form to the canonical arXiv ID `<id>` immediately and use that ID for the rest of the workflow.
   Tags must be passed as repeated flags, e.g. `--tags rl --tags agent`.
   Default to syncing Notion unless the user explicitly passes `--no-sync-notion`.
2. Read summary instructions from `src/paper_assistant/prompts/paper_summary_instructions.md`.
3. Unless `--no-sync-notion` is present, run `.venv/bin/paper-assist notion-preflight`
   before the paper workflow. If preflight fails, stop immediately and report it.
   Do **not** add a `paper-assist index-rebuild --embed` preflight here: the qmd
   index is already kept current by `sync_paper()`/`batch_sync()` hooks on every
   mutation path (invariant 7b), so an automatic full rebuild would re-embed the
   whole library (O(library size)) for an optional lookup. If you suspect the
   index has drifted (e.g., after bulk imports from another host or after editing
   summaries out-of-band), run `.venv/bin/paper-assist index-rebuild --embed`
   manually before invoking `/summarize`.
4. Create a repo-local artifact directory for the normalized arXiv ID:
   `.artifacts/summarize-paper/<id>/`
5. Use the Hugging Face paper route as the default retrieval path.
   First fetch metadata using `hf papers info <id>`:
   `hf papers info <id>`
   Use that output for title/authors/abstract context when needed.
   Do not infer metadata from the markdown wrapper returned by `hf papers read`.
   Then fetch the paper content using `hf papers read <id>` (Hugging Face CLI).
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
6. **Related-paper lookup** (optional, best-effort):
   Query for related papers in the library:
   `.venv/bin/paper-assist search --json "<paper title>" --limit 5 --mode hybrid`
   Prefer hybrid mode; if embeddings are unavailable, the command falls back to
   text search automatically and prints a warning — that is acceptable.
   If this returns results, use them as context when generating the summary —
   note connections, contrasts, or builds-on relationships with existing library papers.
   If the command fails or returns no results, proceed without related context.
7. Generate a summary following the instructions from step 2.
   Adaptations for the saved document:
   - Omit `# Follow-ups` (interactive-only)
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
   - If related papers were found in step 6, weave brief connections into the summary where natural
8. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
9. Unless `--skip-transcript` or `--skip-audio` is present, generate a narration transcript before import:
   a. Read `.artifacts/summarize-paper/<id>/summary.md`.
   b. Read `src/paper_assistant/prompts/audio_script_instructions.md`.
   c. Using the host model, write the narration transcript to `.artifacts/summarize-paper/<id>/transcript.md`.
   d. Verify the transcript file exists and has more than 32 non-whitespace characters.
   e. If transcript generation fails, emit a visible warning to the user before import and apply this exact fallback policy:

      | User flags on `/summarize` | Add to `skill-import` | Result |
      | --- | --- | --- |
      | plain run (no `--force`, no skip flags) | `--skip-transcript` | audio still uses the raw summary |
      | `--force` re-import | `--skip-audio` | existing transcript/audio are preserved |
      | user already passed `--skip-transcript` | unchanged | raw-summary audio path remains intentional |
      | user already passed `--skip-audio` | unchanged | transcript/audio remain preserved |

      Never pass `--script-file` or `--no-script-fallback` after a transcript-generation failure.
10. Import in the foreground and complete:
   `.venv/bin/paper-assist skill-import https://arxiv.org/abs/<id> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model claude-code \
     [--tags ...] --sync-notion [--skip-audio] [--skip-transcript] [--force] \
     [--script-file .artifacts/summarize-paper/<id>/transcript.md --no-script-fallback] \
     --cleanup-file .artifacts/summarize-paper/<id>/summary.md \
     [--cleanup-file .artifacts/summarize-paper/<id>/transcript.md] \
     --cleanup-file .artifacts/summarize-paper/<id>/paper.md \
     [--cleanup-file .artifacts/summarize-paper/<id>/paper.pdf] \
     --json`
   Always pass the arXiv URL (`https://arxiv.org/abs/<id>`) to `skill-import`,
   not the original Hugging Face or other URL, so that the paper_id resolves to
   the arXiv ID.
   Omit `--sync-notion` only when the user explicitly passed `--no-sync-notion`.
   `paper.md` is always created (by `hf papers read` or `extract-text`).
   Add `--script-file ... --no-script-fallback` only when transcript generation succeeded.
   Only include transcript cleanup when `transcript.md` was created and passed to `skill-import`.
   Only include `--cleanup-file` for `paper.pdf` if the PDF fallback was used.
11. Report results from the JSON output to the user.

## Error Handling

- `hf papers info` failure: continue if `hf papers read` still works, but do not infer metadata from the read-wrapper; runtime import resolves metadata separately
- `hf papers read` failure: fall back to PDF download + native read + extract-text
- `curl` failure (in fallback path): retry once, then report error and stop
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata immediately on metadata `429`s instead of burning the full API retry budget; if arXiv is still rate-limiting after fallback, stop and wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Notion sync failure: report it as a warning; the import itself succeeded
- Duplicate paper: report the error message, which suggests `--force` or sync-only
