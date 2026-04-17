# Plan: Roadmap Item 2b — Skill-Driven Transcript Generation

## Context

Roadmap item 2b: let the host agent (Claude Code / Codex CLI / Kiro skill) produce the narration script instead of having `render_audio_assets()` call the Anthropic API with `config.audio_script_model` (default Haiku). Today every summary import pays for an extra API round-trip even when the user is already running a skill session with a stronger model (Opus, GPT-5, etc.).

The pipeline already supports injected scripts via `provided_script_markdown` and `--script-file`. The missing pieces are:

1. Skill steps that generate + drop the transcript alongside `summary.md`.
2. A pipeline guard so skill callers never silently fall back to the API when the provided script is empty/broken.
3. Aligned prompt-file location so skills and the Python runtime read the same canonical instructions.
4. Parity for the web `/api/import` route.

Design decisions confirmed with the user:

- Co-locate `paper_summary_instructions.md` and `audio_script_instructions.md` in `src/paper_assistant/prompts/` (single source of truth; ships with the wheel).
- Skill runs transcript generation automatically on every run unless `--skip-transcript` / `--skip-audio`.
- `render_audio_assets()` gains `skip_script_generation: bool` so skill callers suppress API fallback.
- Extend `ImportRequest` with `script_markdown` + `skip_script_generation` for CLI/API symmetry.

Revisions addressing the first review round:
§2 adds a failure-policy subsection; §1 swaps the packaging assumption for an explicit wheel-inspect step; §6 expands tests to cover the force-merge preservation matrix; §"Critical Files" uses repo-accurate names and the doc sweep now includes `docs/plan-skill-based-summary.md` and `docs/design-workflow-optimization.md`.

Revisions addressing the second review round:
`--no-script-fallback` is kept strictly orthogonal to preservation — it only controls whether the Anthropic API may generate a narration script, never whether existing transcript/audio is preserved. Callers wanting preservation on `--force` pair it with `--skip-audio` (or `--skip-transcript`) explicitly, consistent with invariant 1d. The `skip_script_generation` plumbing therefore stops at `import_paper_summary()`; `regenerate_transcript_and_audio()` and the transcript-regenerate CLI/web surfaces are left unchanged (their contract is "give me a script — via `--script-file` or I'll generate one"). `AGENTS.md` is auto-generated from `CLAUDE.md`; the plan updates `CLAUDE.md` and relies on the existing pre-commit hook to regenerate `AGENTS.md`.

## Scope

1. **Prompts** — relocate `paper_summary_instructions.md`, keep `audio_script_instructions.md`.
2. **Pipeline** — add `skip_script_generation` flag on `audio_assets.render_audio_assets` and thread it through `pipeline.import_paper_summary` only (see §2 for why `regenerate_transcript_and_audio` is intentionally left alone).
3. **CLI** — new `--no-script-fallback` flag on `skill-import`.
4. **Web API** — extend `ImportRequest` with `script_markdown` + `skip_script_generation`; plumb into `POST /api/import`.
5. **Skills** — three skill files gain a post-summary transcript step plus an explicit failure policy.
6. **Tests** — guard-flag happy path + the force-merge preservation cases already covered by the existing matrix.
7. **Docs** — CLAUDE.md, roadmap.md, plan-audio-friendly-readout.md §14b, plan-skill-based-summary.md, design-workflow-optimization.md, README.md, and this file. `AGENTS.md` is regenerated from `CLAUDE.md` by the pre-commit hook, not directly edited.

## Critical Invariants Touched

- **1d (force × skip preservation matrix)** — relied on, not changed. Skill failure policy uses `--skip-audio` / `--skip-transcript` to trigger the existing preservation rules in `pipeline._build_import_paper` (`src/paper_assistant/pipeline.py:521-546`).
- **5 (TTS input precedence)** — unchanged; skill-provided transcript still flows through `prepare_script_for_tts`.
- **5a (audio asset centralization)** — unchanged; all call sites still route through `render_audio_assets`. Update the invariant's note to mention `skip_script_generation`.
- **7 (graceful degradation)** — preserved; when `skip_script_generation=True` and no script is supplied, `render_audio_assets` appends a warning and continues with raw-summary TTS (or no audio when also `--skip-audio`). No exception propagates.
- **CLAUDE.md Skill Workflow Gotchas** — reference `src/paper_assistant/prompts/paper_summary_instructions.md` after the move. `AGENTS.md` is auto-generated from `CLAUDE.md` via pre-commit; do not edit it directly.

## Failure policy for skill transcript generation

Skill transcript step can fail in three ways:

1. Host model returns empty / whitespace-only text.
2. Host model refuses or errors.
3. Writing `.artifacts/summarize-paper/<paper_id>/transcript.md` fails (disk, permissions).

**Chosen contract — "warn loudly, degrade predictably, never ship a bad `--script-file`":**

- The skill MUST always surface a visible warning to the user that transcript generation failed (exact text left to each skill, but the warning must precede the `skill-import` invocation).
- The skill MUST NOT pass `--script-file` pointing at an empty or missing file. It substitutes flags based on the current invocation — these substitutions lean on invariant 1d's existing preservation rules in `_build_import_paper()` and do NOT require `--no-script-fallback`:

  | User flag state on `/summarize`          | On transcript failure, skill adds to `skill-import` | Effective result                                                                 |
  | ---------------------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------- |
  | Plain run (no `--force`, no skip flags)  | `--skip-transcript`                                 | No previous transcript; pipeline synthesizes audio from the raw summary.         |
  | `--force` re-import                      | `--skip-audio`                                      | Invariant 1d preserves both existing `transcript_path` and `audio_path`.         |
  | User already passed `--skip-transcript`  | unchanged                                           | No transcript was expected; audio flows from raw summary as today.               |
  | User already passed `--skip-audio`       | unchanged                                           | No audio work at all; transcript/audio preserved per invariant 1d.               |

- The skill NEVER adds `--no-script-fallback` when transcript generation failed. That flag is paired ONLY with a valid `--script-file`.
- `--no-script-fallback` is intentionally orthogonal to preservation: it decides whether the Anthropic API may generate a script, nothing else. A hand-run `skill-import ... --force --no-script-fallback` WITHOUT `--script-file` will still clear the old transcript/audio and synthesize from the raw summary, because the user did not opt into preservation. Callers wanting preservation on `--force` must add `--skip-audio` (or `--skip-transcript`) themselves — exactly what the table above does on behalf of the skill.
- All three skill files implement the same contract verbatim (copy the table into each skill as a short checklist). Divergence between Claude Code / Codex / Kiro is a regression.

## Implementation Steps

### 1. Prompt Relocation

- `git mv prompts/paper_summary_instructions.md src/paper_assistant/prompts/paper_summary_instructions.md` (no content change).
- Delete the now-empty top-level `prompts/` directory.
- Update every reference in the repo to the new path:
  - `.claude/commands/summarize.md:9`
  - `.kiro/skills/summarize-paper.md:13`
  - `skills/codex/summarize-paper/SKILL.md:14`
  - `CLAUDE.md:118`
  - `README.md:241,264`
  - `docs/design-workflow-optimization.md:21,63,153,160`
  - `docs/plan-skill-based-summary.md:47,57,275,324,427,450` (completed plan doc; update in place rather than leaving dual paths).
- Do NOT edit `AGENTS.md` directly — it is auto-generated from `CLAUDE.md` (see `AGENTS.md:1`). Rely on the existing pre-commit hook to regenerate it from the updated `CLAUDE.md`.

**Packaging verification (replaces the earlier assumption):**

- `pyproject.toml` uses hatchling with `packages = ["src/paper_assistant"]` and no explicit data-file rules. Hatchling includes all package files by default, but this must be verified rather than assumed.
- `python -m build` requires the `build` package, which is NOT currently in `pyproject.toml` dev extras. Either install it ad-hoc (`pip install build`) for the verification step, or add `"build>=1.0"` to `[project.optional-dependencies].dev` as part of this change. Prefer the ad-hoc install for now to keep the dev-extras surface narrow.
- Run:

  ```bash
  pip install build         # if not already installed
  python -m build
  python -c "import zipfile; z=zipfile.ZipFile(next(iter(__import__('glob').glob('dist/*.whl'))));
             [print(n) for n in z.namelist() if 'prompts/' in n]"
  ```

  Expected output must list both:
  - `paper_assistant/prompts/audio_script_instructions.md`
  - `paper_assistant/prompts/paper_summary_instructions.md`

- If either file is missing from the wheel, add to `pyproject.toml`:

  ```toml
  [tool.hatch.build.targets.wheel.force-include]
  "src/paper_assistant/prompts" = "paper_assistant/prompts"
  ```

  and re-run the inspection until both files appear.

- The installed-wheel path also backs `audio_script.py:PROMPT_PATH` resolution (`src/paper_assistant/audio_script.py:5`), so an invisible regression here would break the existing transcript generation, not just the new skill flow.

### 2. Pipeline — `skip_script_generation` Guard

**`src/paper_assistant/audio_assets.py`**

- Add `skip_script_generation: bool = False` to `render_audio_assets()` signature.
- Update the script-decision block (current lines ~79–97):

  ```text
  if provided_script_markdown is not None:
      # existing path; strip; warn on empty
  elif skip_transcript:
      # existing path: no new transcript, TTS from raw summary
  elif skip_script_generation:
      result.warnings.append(
          "Skipped narration script generation (caller opted out); "
          "audio will use the raw summary."
      )
      # fall through to TTS-from-raw-summary
  else:
      # existing _try_generate_script path
  ```

- Treat a whitespace-only `provided_script_markdown` as "no script" **and** respect `skip_script_generation` in that branch too — i.e. do not re-enter `_try_generate_script` just because the caller stripped-to-empty after validation. Emit the same warning and fall through to raw-summary TTS.

**`src/paper_assistant/pipeline.py`**

- Add pass-through `skip_script_generation` to `import_paper_summary()` (~line 225) only.
- `regenerate_transcript_and_audio()` (~line 579) is NOT extended. Its contract is "produce a narration for this paper": either the caller provides one via `--script-file` / `script_markdown`, or the API generates one. A "refuse API fallback" option there would need a matching CLI flag (`paper-assist transcript regenerate --no-script-fallback`) and a matching web field on `TranscriptRegenerateRequest`, and neither surface is in scope for item 2b. The skill flow does not use the regenerate path — it uses `skill-import` with `--script-file`.
- `create_local_entry()` (line 70) does not take `provided_script_markdown`; it stays untouched. No `create_summary_entry()` exists in this codebase.

Default `False` preserves every existing caller's behavior.

### 3. CLI — `skill-import --no-script-fallback`

**`src/paper_assistant/cli.py` (skill-import, line 766)**

- Add `@click.option("--no-script-fallback", is_flag=True, default=False, help="Never call the Anthropic API for narration; require --script-file or warn.")`.
- Forward to `_run_import_pipeline(..., skip_script_generation=no_script_fallback)`.
- No changes to `add` / `import` commands — those remain user-driven and may legitimately want API generation.

### 4. Web — `/api/import` parity

**`src/paper_assistant/web/routes.py` (ImportRequest, line ~17)**

```python
class ImportRequest(BaseModel):
    url: str
    markdown: str
    tags: list[str] = Field(default_factory=list)
    skip_audio: bool = False
    skip_transcript: bool = False
    script_markdown: str | None = None
    skip_script_generation: bool = False
```

- `POST /api/import` handler passes BOTH `script_markdown` AND `skip_script_generation` through to `import_paper_summary`. A test asserts the second kwarg explicitly (see §6).
- Invariant 6 respected: model stays module-level.

### 5. Skill Workflow Updates

Each skill adds a new step between "summary saved" and `skill-import`. The step embeds the failure-policy table verbatim.

**Critical files:**

- `.claude/commands/summarize.md`
- `.kiro/skills/summarize-paper.md`
- `skills/codex/summarize-paper/SKILL.md`

**Skill step (insert before the `skill-import` invocation):**

```text
If not --skip-transcript and not --skip-audio:
  1. Read .artifacts/summarize-paper/<paper_id>/summary.md.
  2. Read src/paper_assistant/prompts/audio_script_instructions.md.
  3. Using the host model, produce the narration script per those instructions.
  4. Write the transcript to .artifacts/summarize-paper/<paper_id>/transcript.md.
  5. Verify the file exists AND is non-empty (> 32 non-whitespace chars). If not,
     apply the Failure policy table (substitute --skip-transcript or --skip-audio
     based on whether --force is set, emit a user-visible warning, and DO NOT
     pass --script-file / --no-script-fallback).
  6. On success, append:
       --script-file .artifacts/summarize-paper/<paper_id>/transcript.md
       --no-script-fallback
     to the skill-import command, and add transcript.md to --cleanup-file.
```

Kiro skill keeps its "no Notion sync" posture; only the transcript step is added.

### 6. Tests

**Unit (must update or add):**

- `tests/test_audio_assets.py` — new cases:
  1. `render_audio_assets(..., skip_script_generation=True, provided_script_markdown=None, skip_transcript=False)` emits the expected warning, does NOT invoke `_try_generate_script`, proceeds with raw-summary TTS. Mock `get_tts_backend`.
  2. Whitespace-only `provided_script_markdown` combined with `skip_script_generation=True` also skips `_try_generate_script` (regression guard against re-entering the API on empty input).
- `tests/test_skill_import.py` (existing force-matrix tests live here, lines ~388–470) — new cases:
  1. `--no-script-fallback` plumbs into `import_paper_summary(skip_script_generation=True)`.
  2. `--force` + `--script-file` + `--no-script-fallback`: the provided transcript replaces the old one and no API script-gen happens.
  3. `--force` + `--skip-audio` + `--no-script-fallback` (skill failure-policy path): existing `transcript_path` and `audio_path` are preserved per invariant 1d, and `_try_generate_script` is not invoked.
  4. `--force` + `--no-script-fallback` WITHOUT `--script-file` or `--skip-audio`: the old transcript IS cleared (per invariant 1d), audio is synthesized from the raw summary via the "skip_script_generation warn" branch, and `_try_generate_script` is not invoked. This is the orthogonality contract — it locks in that `--no-script-fallback` does not imply preservation.
- `tests/test_web.py` (existing `/api/import` tests live around lines ~365–461) — new cases:
  1. `script_markdown` round-trips into `provided_script_markdown`.
  2. `skip_script_generation` is forwarded, not dropped. Assert the kwarg explicitly; do NOT rely only on the script_markdown assertion.
- No new tests on `regenerate_transcript_and_audio()`; its signature is unchanged by this plan.

**Prompt-move regression check (CI-friendly grep):**

```bash
! rg -n 'prompts/paper_summary_instructions\.md' \
    --glob '!docs/plan-skill-driven-transcript*.md'
```

This command must return nothing once the doc sweep is complete. Keep it in the Definition-of-Done checklist for this change.

**Manual QA:**

- Run `/summarize` in Claude Code on one real arXiv id. Confirm:
  - `.artifacts/summarize-paper/<id>/transcript.md` is created by the skill.
  - `paper-assist skill-import` receives `--script-file` + `--no-script-fallback`.
  - Final `transcripts/<id>.md` matches the skill's transcript byte-for-byte (Claude-generated header and all).
  - No `anthropic` HTTP request for script generation in the import log (debug via `ANTHROPIC_LOG=debug` or by running the skill-import leg without `ANTHROPIC_API_KEY`).
  - `audio/<id>.mp3` plays and matches the skill transcript content.
- Force re-import of the same paper with an intentionally empty skill response (temporarily short-circuit step 3 to emit `""`): confirm the skill warns, falls through to `--skip-audio`, and existing transcript/audio are preserved.
- Repeat a successful run via Codex skill; repeat via Kiro skill (confirm Notion sync remains off in the Kiro flow).
- Run `paper-assist tts check` — behavior unchanged.
- `POST /api/import` with a `script_markdown` body in `curl` — verify transcript is saved; re-run with `skip_script_generation: true` and an absent/empty `script_markdown` — verify warning flows back in the response and no API call is made.

### 7. Docs

- `CLAUDE.md` — update "Skill Workflow Gotchas" path, add note that skill drives transcript generation by default, mention `skip_script_generation` under invariant 5a, and reference the Failure policy table in this plan.
- `AGENTS.md` — do NOT edit directly; it is auto-generated from `CLAUDE.md` by the pre-commit hook. Run the commit and verify the hook regenerates it; include the regenerated `AGENTS.md` in the same commit.
- `docs/roadmap.md` — move item 2b to Completed; keep item 2 `--all` batch path open.
- `docs/plan-audio-friendly-readout.md` §14b — mark implemented, link to this doc.
- `docs/plan-skill-based-summary.md` — path updates (completed plan doc; update references rather than stranding readers on a dead path).
- `docs/design-workflow-optimization.md` — path updates (4 references).
- `README.md:241,264` — path updates and short note under the summary workflow section that skill sessions generate transcripts locally.

## Critical Files

- `src/paper_assistant/audio_assets.py` — `render_audio_assets` signature + guard branch.
- `src/paper_assistant/pipeline.py` — pass-through on `import_paper_summary` (~225) ONLY. `regenerate_transcript_and_audio` (~579) and `create_local_entry` (70) unchanged.
- `src/paper_assistant/cli.py:766` — `skill-import` adds `--no-script-fallback`.
- `src/paper_assistant/web/routes.py:17` — `ImportRequest` + `/api/import` handler.
- `src/paper_assistant/prompts/paper_summary_instructions.md` — new canonical location (moved from top-level).
- `.claude/commands/summarize.md`, `.kiro/skills/summarize-paper.md`, `skills/codex/summarize-paper/SKILL.md` — transcript step + Failure policy table + updated prompt path.
- `CLAUDE.md`, `docs/roadmap.md`, `docs/plan-audio-friendly-readout.md`, `docs/plan-skill-based-summary.md`, `docs/design-workflow-optimization.md`, `README.md`. (`AGENTS.md` is regenerated from `CLAUDE.md` by the pre-commit hook; do not edit directly.)
- Tests (actual existing filenames in this repo):
  - `tests/test_audio_assets.py`
  - `tests/test_skill_import.py`
  - `tests/test_web.py`

## Reuse

- `audio_assets.render_audio_assets` — extend, don't replace.
- `audio_assets._try_generate_script` — unchanged; only the dispatch around it changes.
- `pipeline.import_paper_summary` — add one kwarg.
- `pipeline.regenerate_transcript_and_audio` — unchanged by this plan.
- `pipeline._build_import_paper` (lines 499–546) — unchanged; the failure policy leans on its existing force-merge rules.
- `tts.prepare_script_for_tts` / `prepare_text_for_tts` — unchanged.
- `storage.save_transcript` — unchanged.
- `web.routes.TranscriptRegenerateRequest` — reference model for the new `ImportRequest` fields.

## Verification

```bash
# 1. Unit tests
pytest tests/

# 2. Wheel packaging check (verifies both prompts ship)
#    `build` is not in dev extras; install it ad-hoc for this step.
pip install build
python -m build
python -c "import zipfile,glob; z=zipfile.ZipFile(glob.glob('dist/*.whl')[0]); \
  print([n for n in z.namelist() if 'prompts/' in n])"

# 3. Prompt-path regression grep (must return nothing)
rg -n 'prompts/paper_summary_instructions\.md' \
    --glob '!docs/plan-skill-driven-transcript*.md'

# 4. Skill end-to-end (Claude Code)
/summarize https://arxiv.org/abs/2503.10291 --tags demo
# Expect: transcript artifact present, transcripts/<id>.md matches it,
# audio/<id>.mp3 generated, no Anthropic script-gen call in debug log.

# 5. Failure-path QA (manual)
# Temporarily stub the skill's transcript step to emit "".
# Expected: user-visible warning, skill-import invoked with --skip-transcript
# (or --skip-audio on --force), existing transcript/audio preserved on force.

# 6. CLI direct
paper-assist skill-import https://arxiv.org/abs/2503.10291 \
  --file /tmp/summary.md \
  --script-file /tmp/transcript.md \
  --no-script-fallback \
  --model claude-code

# 7. Web parity
curl -X POST http://localhost:8000/api/import \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://arxiv.org/abs/2503.10291","markdown":"...","script_markdown":"...","skip_script_generation":true}'

# 8. TTS diagnostics unchanged
paper-assist tts check
```

## Out of Scope

- Roadmap item 2 (`--all` batch `regenerate-audio`). Separate ticket.
- Changing the summary-generation prompt or audio-script prompt content.
- Migrating `audio_script_instructions.md` location (stays in package dir — only `paper_summary_instructions.md` moves).
- Adding a dedicated `paper-assist skill-regenerate-transcript` CLI (existing `transcript regenerate --script-file` already covers this; the skill composes it).
