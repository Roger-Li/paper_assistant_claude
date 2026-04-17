---
name: "summarize-paper"
description: "Use when a user explicitly asks to summarize, import, or store an arXiv ML paper through the Paper Assistant workflow. Downloads the paper, generates a structured summary following project instructions, and imports it into the local paper-assistant library with optional TTS audio. Designed for environments without Notion sync or qmd search."
---

Use this skill when the user wants a paper summarized and stored through Paper Assistant.

## Workflow

1. Parse the user's request for a bare arXiv ID, a full arXiv URL, or a Hugging Face paper URL, plus any tags and flags such as `--skip-audio`, `--skip-transcript`, and `--force`.
   Normalize any accepted input form to the canonical arXiv ID `<id>` immediately and use that ID for the rest of the workflow.
   Tags must be repeated flags, e.g. `--tags rl --tags agent`.
2. Read `src/paper_assistant/prompts/paper_summary_instructions.md`.
3. Create a repo-local artifact directory for the normalized arXiv ID:
   `.artifacts/summarize-paper/<id>/`
4. Use the Hugging Face paper route as the default retrieval path.
   Fetch the paper metadata and content using the Hugging Face CLI keyed by the normalized arXiv ID `<id>`.
   First, use `hf papers info <id>` as the metadata companion:
   `hf papers info <id>`
   Use that output for title/authors/abstract context when needed.
   Do not try to infer metadata from the markdown wrapper returned by `hf papers read`.
   Then fetch the paper content using `hf papers read <id>`:
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
5. Generate the summary from the tracked instructions.
   Adaptations for the saved document:
   - Omit `# Follow-ups` because it is interactive-only
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
6. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
7. Unless `--skip-transcript` or `--skip-audio` is present, generate a narration transcript before import:
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
8. Import it in the foreground:
   `.venv/bin/paper-assist skill-import https://arxiv.org/abs/<id> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model kiro \
     [--tags ...] [--skip-audio] [--skip-transcript] [--force] \
     [--script-file .artifacts/summarize-paper/<id>/transcript.md --no-script-fallback] \
     --cleanup-file .artifacts/summarize-paper/<id>/summary.md \
     [--cleanup-file .artifacts/summarize-paper/<id>/transcript.md] \
     --cleanup-file .artifacts/summarize-paper/<id>/paper.md \
     [--cleanup-file .artifacts/summarize-paper/<id>/paper.pdf] \
     --json`
   Always pass the arXiv URL (`https://arxiv.org/abs/<id>`) to `skill-import`,
   not the original Hugging Face or other URL, so that the paper_id resolves to
   the arXiv ID.
   `paper.md` is always created (by `hf papers read` or `extract-text`).
   Add `--script-file ... --no-script-fallback` only when transcript generation succeeded.
   Only include transcript cleanup when `transcript.md` was created and passed to `skill-import`.
   Only include `--cleanup-file` for `paper.pdf` if the PDF fallback was used.
9. Parse the JSON output and report the result to the user.

## Error Handling

- `hf papers info` failure: continue if `hf papers read` still works, but do not infer metadata from the read-wrapper; runtime import resolves metadata separately
- `hf papers read` failure: fall back to PDF download + native read + extract-text
- `curl` failure (in fallback path): retry once, then stop and report the failure
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata immediately on metadata `429`s instead of burning the full API retry budget; if arXiv still rate-limits after fallback, stop and wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Duplicate paper: report the command error, which suggests `--force` or sync-only
