Usage: /synthesize <title-or-topic> [--tags t1 --tags t2] [--query "..."] [--papers id1 id2 ...] [--no-sync-notion] [--skip-audio] [--skip-transcript]

Generate a cross-paper synthesis (lit review) from stored library summaries and
import it as a local note entry.

## Workflow

1. Parse `$ARGUMENTS`: the positional words are the synthesis title/topic; flags
   as above. Tags must be passed as repeated flags, e.g. `--tags rl --tags distillation`.
   Derive `<slug>` from the title (lowercase, hyphens, alphanumerics only) for
   artifact paths. Default to syncing Notion unless the user explicitly passes
   `--no-sync-notion`.
2. Unless `--no-sync-notion` is present, run `.venv/bin/paper-assist notion-preflight`
   before the workflow. If preflight fails, stop immediately and report it.
   Do **not** add a `paper-assist index-rebuild --embed` preflight here (same
   reasoning as `/summarize`: the qmd index is kept current by mutation hooks).
3. Resolve the candidate paper set (machine-readable enumeration):
   a. Fetch the library map first: run `.venv/bin/paper-assist list --json` and
      index the entries by `paper_id`. Every candidate ID from the sources
      below is hydrated through this map to get its `title`, `tags`, and
      `summary_path` — `search --json` returns only
      `paper_id`/`title`/`score`/`snippet`, so its hits must be hydrated here
      too.
   b. Collect candidate IDs:
      - If `--papers` is given: take those IDs verbatim; warn about any ID
        missing from the library map.
      - For each `--tags t`: run `.venv/bin/paper-assist list --tag t --json`
        and union the results (`list` takes a single `--tag`, so run once per
        tag).
      - If `--query` is given (or neither `--papers` nor `--tags` was given —
        derive a query from the topic): run
        `.venv/bin/paper-assist search --json "<query>" --limit 20 --mode hybrid`
        and union the hit IDs with the tag results. Hybrid falling back to text
        with a warning is acceptable.
4. **Confirm the paper set with the user (mandatory).** Show the resolved set as
   a table (paper_id, title, tags) and ask the user to confirm, add, or remove
   papers before generating. Do not proceed without explicit confirmation. If
   the resolved set is empty, tell the user and stop.
5. Read each confirmed paper's normalized summary body:
   `.venv/bin/paper-assist show <paper_id> --body > .artifacts/synthesize/<slug>/papers/<paper_id>.md`
   (redirect to a file to avoid shell output truncation, then read the file).
   This prints the stored summary through `normalize_summary_body()` — the
   single source of truth for stripping YAML front matter and the generated
   title header — so do not re-implement any stripping yourself. Skip papers
   whose `summary_path` is null in the library map (no summary yet), with a
   warning. **After exporting, recheck the usable set: if no papers with
   readable summaries remain, report which papers were skipped and why, and
   stop — never generate a synthesis from an empty corpus.**
6. Read `src/paper_assistant/prompts/paper_synthesis_instructions.md` and
   generate the synthesis using the `lit-review` template. Write it to
   `.artifacts/synthesize/<slug>/synthesis.md` with no YAML front matter.
7. Unless `--skip-transcript` or `--skip-audio` is present, generate a narration
   transcript before import:
   a. Read `.artifacts/synthesize/<slug>/synthesis.md`.
   b. Read `src/paper_assistant/prompts/audio_script_instructions.md`, adapted
      for a multi-paper survey: narrate the arc theme-by-theme rather than one
      paper's method, and the length may stretch to ~2000 words for large sets.
      All other rules (no headings/bullets, speakable prose) apply unchanged.
   c. Write the transcript to `.artifacts/synthesize/<slug>/transcript.md`.
   d. Verify the file exists and has more than 32 non-whitespace characters.
   e. If transcript generation fails, emit a visible warning before import and
      fall back: plain run → add `--skip-transcript` (audio uses the raw
      synthesis); user already passed `--skip-transcript` or `--skip-audio` →
      unchanged. Never pass `--script-file` or `--no-script-fallback` after a
      transcript-generation failure.
8. Import in the foreground:
   `.venv/bin/paper-assist create --title "<title>" \
     --file .artifacts/synthesize/<slug>/synthesis.md \
     --tags lit-review [--tags <user tags>...] \
     [--script-file .artifacts/synthesize/<slug>/transcript.md --no-script-fallback] \
     [--skip-audio] [--skip-transcript] --json \
     --cleanup-file .artifacts/synthesize/<slug>/synthesis.md \
     [--cleanup-file .artifacts/synthesize/<slug>/transcript.md] \
     [--cleanup-file .artifacts/synthesize/<slug>/papers/<paper_id>.md ...]`
   Always include the `lit-review` tag plus any user-provided tags.
   Pass one `--cleanup-file` per exported paper-body file from step 5.
   Add `--script-file ... --no-script-fallback` only when transcript generation
   succeeded, and only then include the transcript `--cleanup-file`.
   Read `paper_id` from the JSON output — slug dedupe may have appended `-2`,
   `-3`, etc.; that value is authoritative for the next step.
9. Unless `--no-sync-notion` is present, run
   `.venv/bin/paper-assist notion-sync --paper <paper_id>` with the paper_id
   from step 8. Report a sync failure as a warning — the note itself was
   created.
10. Report to the user: paper_id, summary/transcript/audio paths from the JSON
    output, the list of papers included in the synthesis, and any warnings.

## Error Handling

- Empty resolved paper set: tell the user which tags/query matched nothing and stop.
- `create` failure: the command exits non-zero and prints the preserved artifact
  paths under `.artifacts/synthesize/<slug>/`; report them.
- Duplicate title: slug dedupe is automatic (`-2`, `-3`, ...); just report the
  final paper_id.
- Notion sync failure: report as a warning; the note itself was created.
