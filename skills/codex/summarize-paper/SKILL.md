---
name: "summarize-paper"
description: "Summarize arXiv ML papers through Paper Assistant: import a structured Markdown summary with optional audio and default-on Notion sync, or create a standalone local HTML learning brief when explicitly requested."
---

Use this skill when the user wants a paper summarized and stored through Paper Assistant.

## HTML learning brief — route before any preflight

When the user explicitly requests HTML instead of the usual Markdown summary,
use this branch and stop before the import workflow below:

1. Resolve the input to an arXiv ID as in step 1 below, preserving a requested
   version separately for retrieval and citations.
2. Read `src/paper_assistant/prompts/paper_summary_instructions.md` and the HTML
   supplement, `src/paper_assistant/prompts/paper_html_summary_instructions.md`.
3. Fetch version-pinned sources and figure files:
   `.venv/bin/python skills/shared/html-brief/build_brief.py fetch <id>[vN] --out .artifacts/summarize-paper/<id>`
   This replaces `hf papers read` for this branch: the brief needs the arXiv
   HTML for section anchors, math and figures, and `hf papers read` has no body
   for many new papers. Read the resulting `paper.md` and look at the figures in
   `sources/`. `hf papers info <id>` stays optional metadata context (prefer a
   working repo-local `.venv/bin/hf` if the global launcher is unavailable). If
   the paper has no arXiv HTML, follow the supplement's PDF path.
4. Run the best-effort related-paper lookup from step 7 below. Use hits only for
   the brief's "connections to papers in your library" part, read through
   `.venv/bin/paper-assist show <paper_id> --body`.
5. Follow the supplement: write `evidence.json`, copy
   `skills/shared/html-brief/skeleton.html` to `brief.src.html` and fill it in,
   then build and lint:
   `.venv/bin/python skills/shared/html-brief/build_brief.py build .artifacts/summarize-paper/<id>`
   The build must exit 0: it verifies every source anchor and every ledger value
   against the downloaded paper. Fix errors; resolve or report warnings.
6. Browser-check `summary.html` as the supplement describes
   (`node skills/shared/html-brief/check_brief.cjs …` when Playwright is
   available) and look at the screenshots.
7. Link the HTML and report the outcome. Do not run Notion preflight/sync,
   transcript/TTS generation, visual injection into Markdown, `skill-import`,
   or artifact cleanup. The pilot does not add an HTML record to `index.json`.

This is a skill-level format choice, not a new `paper-assist` CLI flag. The
ordinary Markdown workflow below retains its existing defaults.

## Workflow

1. Parse the user's request for a bare arXiv ID, a full arXiv URL, a Hugging Face paper URL, or an OpenReview URL, plus any tags and flags such as `--no-sync-notion`, `--skip-audio`, `--skip-transcript`, and `--force`.
   For OpenReview URLs (`openreview.net/forum?id=<ovid>` or `openreview.net/pdf?id=<ovid>`), resolve to an arXiv ID first:
   a. `curl -sL "https://api2.openreview.net/notes?id=<ovid>"` and read the title and authors from `content` (fields may be `{"value": ...}`-wrapped). Use **api2**; the legacy `api.openreview.net` 404s for newer Archive notes. OpenReview may answer with a 403 `ChallengeRequiredError` (browser verification, observed 2026-07); then recover the title another way — web-search the OpenReview ID, or ask the user for the paper title — instead of retrying curl.
   b. Search arXiv by exact title — `curl -sL "https://export.arxiv.org/api/query?search_query=ti:%22<url-encoded-title>%22&max_results=3"` — and accept a hit only when the title matches and the author list overlaps. (The endpoint can transiently reply `Rate exceeded.`; wait ~30s and retry once.)
   c. On a confirmed match, use that arXiv ID and continue normally. If no arXiv counterpart exists, stop and tell the user — the pipeline is arXiv-keyed, and a direct-upload OpenReview note may have no arXiv version.
   Normalize any accepted input form to the canonical arXiv ID `<id>` immediately and use that ID for the rest of the workflow.
   Tags must be repeated flags, e.g. `--tags rl --tags agent`.
   Default to syncing Notion unless the user explicitly opts out with `--no-sync-notion`.
2. Read `src/paper_assistant/prompts/paper_summary_instructions.md`.
3. Unless `--no-sync-notion` is present, run `.venv/bin/paper-assist notion-preflight`
   before the rest of the workflow. If that fails, stop immediately.
   Do **not** add a `paper-assist index-rebuild --embed` preflight here: the qmd
   index is already kept current by `sync_paper()`/`batch_sync()` hooks on every
   mutation path (invariant 7b), so an automatic full rebuild would re-embed the
   whole library (O(library size)) for an optional lookup. If you suspect the
   index has drifted (e.g., after bulk imports from another host or after editing
   summaries out-of-band), run `.venv/bin/paper-assist index-rebuild --embed`
   manually before invoking this skill.
4. Create a repo-local artifact directory:
   `.artifacts/summarize-paper/<id>/`
5. Use the Hugging Face paper route as the default retrieval path.
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
7. **Related-paper lookup** (optional, best-effort):
   Query for related papers in the library:
   `.venv/bin/paper-assist search --json "<paper title>" --limit 5 --mode hybrid`
   Prefer hybrid mode; if embeddings are unavailable, the command falls back to
   text search automatically and prints a warning — that is acceptable.
   If this returns results, use them as context when generating the summary —
   note connections, contrasts, or builds-on relationships with existing library papers.
   If the command fails or returns no results, proceed without related context.
8. Generate the summary from the tracked instructions.
   Adaptations for the saved document:
   - Omit `# Follow-ups` because it is interactive-only
   - `# My-Level Adaptation` profile: ML engineer + researcher
     (implementation details, architecture decisions, code snippets,
     theoretical contributions, comparison with prior work, open questions)
   - If related papers were found in step 7, weave brief connections into the summary where natural
   - Apply the prompt's redundancy pass before writing the file: do not repeat
     method definitions across sections, keep headline metrics in at most two
     places, and make later sections add mechanism, evidence, caveat,
     implementation detail, or contrast.
   - **Best-effort key visuals (per Global Policy §5).** Scan the fetched
     `paper.md` for image-backed figures of the form
     `![...](https://arxiv.org/html/<id>vK/xN.png)` followed by a
     `Figure N: ...` / `Table N: ...` caption. For up to 3 of the most
     crucial visuals (architecture diagrams or headline result figures),
     embed the exact image Markdown line — `![Figure N: short caption](url)` —
     near the relevant discussion in the summary. Copy URLs verbatim;
     skip rather than guess. The prose description still belongs in the
     summary even when an image is embedded.
9. Write the summary to `.artifacts/summarize-paper/<id>/summary.md` with no YAML front matter.
10. Run the deterministic visual injector before transcript generation:
   `.venv/bin/paper-assist visuals inject \
     --summary-file .artifacts/summarize-paper/<id>/summary.md \
     --source-file .artifacts/summarize-paper/<id>/paper.md \
     --arxiv-id <id>`
   This preserves the existing summary when no verified image URL is available.
   It first uses exact image Markdown already present in `paper.md`; if the file
   has figure/table captions but no image Markdown, it fetches arXiv HTML and
   embeds only verified `https://arxiv.org/html/...` image URLs. Never hand-write
   guessed image paths. If the command fails, warn and continue without injected
   visuals — they are best-effort.
11. Unless `--skip-transcript` or `--skip-audio` is present, generate a narration transcript before import:
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
12. Import it in the foreground:
   `.venv/bin/paper-assist skill-import https://arxiv.org/abs/<id> \
     --file .artifacts/summarize-paper/<id>/summary.md \
     --model codex \
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
13. Parse the JSON output and report the result to the user.

## Error Handling

- `hf papers info` failure: continue if `hf papers read` still works, but do not infer metadata from the read-wrapper; runtime import resolves metadata separately
- `hf papers read` failure: fall back to PDF download + native read + extract-text
- `curl` failure (in fallback path): retry once, then stop and report the failure
- PDF read failure: fall back to `extract-text --output`
- arXiv metadata/API failure during import: `skill-import` now falls back to abs-page metadata immediately on metadata `429`s instead of burning the full API retry budget; if arXiv still rate-limits after fallback, stop and wait 2+ minutes before retrying
- Import failure: report the error and the exact artifact paths kept under `.artifacts/summarize-paper/<id>/`
- Notion sync failure: report it as a warning; the import itself succeeded
- Duplicate paper: report the command error, which suggests `--force` or sync-only
