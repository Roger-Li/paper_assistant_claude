# Audio-Friendly Readout — Implementation Plan (v3)

**Status:** Approved for implementation in a new session.
**Review trail:** `docs/plan-audio-friendly-readout-review.md` (v1 blockers,
then v2 clarifications). All comments addressed below.

## 1. Context

Two compounding issues make the saved MP3 unsuitable for standalone listening:

1. **Voice quality.** `tts.py::text_to_speech()` uses edge-tts with
   `en-US-AriaNeural` (`src/paper_assistant/tts.py:11-30`). Prosody, pacing,
   and handling of technical prose lag modern open local models. The user
   already runs an MLX server on `127.0.0.1:8000` (model directory
   `/Users/liyuanzhe/models`, `Voxtral-4B-TTS-2603-mlx-bf16` loaded) and wants
   primary TTS switched to that local server.
2. **Content shape.** `prepare_text_for_tts()` (`tts.py:33-89`) regex-cleans
   the full 2–4k word markdown summary and hands it to TTS. It does not
   strip tables, reads Critical Q&A / Reading List / Follow-ups verbatim,
   and cannot turn the bullet-dense Method section into narrative prose.

Two independently shippable tracks fix both problems:

- **Track A — Pluggable TTS backend, MLX primary.** edge-tts demoted to
  fallback.
- **Track B — Derived narration script.** LLM rewrites the summary into a
  5–8 min prose narration persisted at `transcripts/{paper_id}.md`; feed
  that (not the raw summary) to TTS.

## 2. Locked decisions

- Narration style = single narrator.
- Default ON for every import; `--skip-transcript` opts out.
- Local MLX is primary TTS; edge-tts is fallback only (`tts_edge_fallback`,
  default on).
- Confirm the MLX audio endpoint shape by probe **before** coding
  `MlxTTSBackend` (Step 0 below). Plan assumes OpenAI-compatible
  `/v1/audio/speech`; adjust only the backend body if probe diverges.

## 3. Architecture overview

```
CLI add / import / skill-import
Web /api/add (arXiv + article)
Web /api/paper/{id}/summary (edit + regen)
CLI transcript regenerate                     ──┐
Web /api/paper/{id}/transcript/regenerate     ──┴──► render_audio_assets()
                                                          │
                                                          ├── (optional) audio_script.generate_audio_script()
                                                          │      → transcripts/{paper_id}.md
                                                          │
                                                          └── get_tts_backend(config).synthesize()
                                                                 ├── MlxTTSBackend (primary)
                                                                 └── EdgeTTSBackend (fallback)
                                                                       → audio/{paper_id}.mp3
```

Single helper; all call sites consolidate through it.

## 4. Helper contracts

### 4.1 `render_audio_assets()` — shared audio-asset helper

New module `src/paper_assistant/audio_assets.py`.

```python
@dataclass
class AudioAssetsResult:
    transcript_path: Path | None
    audio_path: Path | None
    script_model: str | None
    backend_used: Literal["mlx", "edge", None]
    warnings: list[str]

async def render_audio_assets(
    *,
    config: Config,
    storage: StorageManager,
    paper: Paper,
    source_markdown: str,                 # NORMALIZED body (no YAML / title header)
    skip_transcript: bool,
    skip_audio: bool,
    provided_script_markdown: str | None = None,   # review v2 #1
    script_model_override: str | None = None,      # review v2 #1
) -> AudioAssetsResult: ...
```

Parameter notes:

- `source_markdown` must be the normalized body. Call
  `summarizer.normalize_summary_body()` first if reading from disk.
- `provided_script_markdown` short-circuits LLM script generation —
  used by `skill-import --script-file` and by the `transcript regenerate`
  CLI when the user supplies text.
- `script_model_override` lets `transcript regenerate --model …` and
  skill flows pick a specific Claude model without mutating `Config`.

Responsibilities (in order):

1. If `skip_audio` → return immediately with `paper.transcript_path` and
   `paper.audio_path` unchanged.
2. Decide the script:
   - If `provided_script_markdown` → use it; persist.
   - Else if `not skip_transcript` and `ANTHROPIC_API_KEY` available →
     `audio_script.generate_audio_script(source_markdown, paper.metadata,
     config, model=script_model_override)`; persist.
   - Else → no script; audio will fall back to `prepare_text_for_tts()`
     on `source_markdown`.
3. If a script is available, persist via `storage.save_transcript()`,
   set `paper.transcript_path`, use `prepare_script_for_tts(script)` as
   TTS input. Otherwise use `prepare_text_for_tts(source_markdown, …)`.
4. Synthesize MP3 via `get_tts_backend(config)`. On MLX failure honor
   fallback policy (§4.3). Persist via `storage.save_audio()`.
5. Re-fetch `paper` after any `storage.save_summary()` /
   `storage.save_audio()` / `storage.save_transcript()` interaction
   (invariant 1).
6. Return paths + warnings. The helper **never raises upward**; every
   failure becomes a warning so the summary import keeps progressing
   (invariant 7).

### 4.2 `TTSBackend` protocol

Refactor `src/paper_assistant/tts.py`:

```python
class TTSBackend(Protocol):
    async def synthesize(self, text: str, output_path: Path) -> Path: ...

class MlxTTSBackend:
    def __init__(self, url, model, voice, response_format="mp3", speed=1.0,
                 timeout_s=120, chunk_chars=2000): ...
    async def synthesize(self, text, output_path): ...
    # Raises MlxConfigError (4xx) or MlxTransientError (5xx / connect / timeout).

class EdgeTTSBackend:
    def __init__(self, voice, rate): ...
    async def synthesize(self, text, output_path): ...

def get_tts_backend(config: Config) -> TTSBackend: ...
```

- `prepare_text_for_tts()` stays for the fallback/legacy path.
- `prepare_script_for_tts()` is new — minimal regex pass (strip stray
  bolds/headings) and **no intro prepend** (the script already opens
  naturally).
- MLX path chunks text at sentence boundaries ≤ `chunk_chars`. Concat
  with `pydub` (+ ffmpeg). ffmpeg missing policy per §4.4.

### 4.3 Error contract (review v2 #2)

One clear rule, applied everywhere:

- **Backends raise typed errors** (`MlxConfigError`, `MlxTransientError`,
  `EdgeTTSError`, etc.).
- **`render_audio_assets()` catches them and converts to warnings** so
  invariant 7 holds for every import/add/edit flow. The caller sees an
  `AudioAssetsResult` with `warnings=[...]`, never a propagated exception.
- **Diagnostic surfaces (`paper-assist tts check`) bypass this
  conversion** — they report the typed error directly and exit non-zero,
  so misconfiguration is visible during setup.

Concretely:

| Failure | Backend raises | `render_audio_assets` does |
| ------- | -------------- | -------------------------- |
| MLX connect refused / 5xx / timeout | `MlxTransientError` | Warn; if `tts_edge_fallback`, retry via edge; else skip audio. |
| MLX 4xx (bad model, bad voice, oversize) | `MlxConfigError` | Warn clearly; do **not** fall back (would hide a config bug); skip audio. |
| edge-tts fails | `EdgeTTSError` | Warn; skip audio. |
| Script LLM fails / no API key | `AudioScriptError` | Warn; continue to raw-summary audio path; leave existing `transcript_path` intact. |
| ffmpeg missing + chunking needed | `FfmpegMissingError` | See §4.4. |

Summary import, CLI `add`, web edit/regen, and `transcript regenerate`
must all still return successfully with warnings in these cases.

### 4.4 ffmpeg / pydub availability policy (review v1 #5)

- At `MlxTTSBackend` construction, probe ffmpeg via
  `pydub.utils.which("ffmpeg")` and cache the capability.
- If chunking is required AND ffmpeg is unavailable:
  - Attempt a single unchunked request if `len(text) ≤
    mlx_tts_max_input_chars` (new config, default 6000).
  - Otherwise raise `FfmpegMissingError`; `render_audio_assets`
    converts to a warning and falls back to edge-tts (edge handles
    arbitrary length). If `tts_edge_fallback=False`, skip audio.
- If ffmpeg unavailable AND text fits in one chunk → MLX works fine,
  no ffmpeg needed.
- `paper-assist tts check` reports ffmpeg presence + version.
- README flags `brew install ffmpeg` as **recommended** for long papers,
  not required.

### 4.5 `normalize_summary_body()` (review v1 #4)

Extract the existing stripping logic in `web/routes.py:594-602` into a
module-level helper in `summarizer.py`:

```python
def normalize_summary_body(raw: str) -> str:
    """Strip YAML front matter and the duplicated title/metadata header."""
    body = raw
    if body.startswith("---"):
        end_idx = body.find("---", 3)
        if end_idx != -1:
            body = body[end_idx + 3 :].lstrip()
    hr_idx = body.find("\n---\n")
    if hr_idx != -1 and hr_idx < 400:
        body = body[hr_idx + 5 :].lstrip()
    return body
```

Callers:

- `transcript regenerate` CLI + web route.
- `api_summary_markdown()` at `web/routes.py:594-602` (replaces inline
  version).
- Any future code that loads a summary file and wants the editable body.

Fresh-import paths pass `result.full_markdown` directly (already the body
form — no normalization needed).

### 4.6 `audio_script.generate_audio_script()`

New module `src/paper_assistant/audio_script.py`, parallel to
`summarizer.py`:

```python
@dataclass
class AudioScriptResult:
    script_markdown: str
    model_used: str
    input_tokens: int
    output_tokens: int

async def generate_audio_script(
    markdown: str,
    metadata: PaperMetadata,
    config: Config,
    model: str | None = None,
) -> AudioScriptResult:
    ...
```

- Uses `anthropic.AsyncAnthropic` with `model or
  config.audio_script_model` (default `claude-haiku-4-5-20251001`).
- Lazy `ANTHROPIC_API_KEY` validation — does not break read-only commands.
- Prompt lives at `src/paper_assistant/prompts/audio_script_instructions.md` (inside the package so installed wheels ship it). The shared skill summary instructions now live alongside it at `src/paper_assistant/prompts/paper_summary_instructions.md`.

Prompt requirements:

- Opens with a 1–2 sentence title/authors intro.
- Walks One-Pager → Problem → Method → Experiments → Conclusions in
  prose, no bullets.
- Paraphrases equations in words.
- Summarizes tables as 1–2 sentences.
- Collapses Critical Q&A into 2–3 sentences.
- Drops Reading List and Follow-ups entirely.
- Uses transitional phrases between sections.
- Target length ~900–1400 words (≈5–8 min at default TTS speed).

### 4.7 Storage helpers (review v2 #4)

`src/paper_assistant/storage.py`:

- `make_transcript_filename(paper_id: str) -> str` — returns
  `"{paper_id}.md"`. Mirrors `make_audio_filename` (line 34).
- `StorageManager.save_transcript(paper_id, content: str) -> Path` —
  writes to `config.transcripts_dir / "{paper_id}.md"`, returns the
  relative-to-data-dir path that `render_audio_assets` stores on
  `paper.transcript_path`. Mirrors `save_audio()` at line 342 and
  `save_summary()` at line 273.
- Extend `delete_paper` (line 121-139) to include
  `paper.transcript_path` in the files-to-unlink list.
- Honor `--force` preservation rules (§5) when re-importing.

## 5. Force × skip state matrix (review v1 #2)

Combined rule: **transcript and audio move together by default**; opting
out of one keeps the other from silently drifting.

| `--skip-audio` | `--skip-transcript` | Transcript | Audio |
| -------------- | ------------------- | ---------- | ----- |
| unset          | unset               | (re)generate | synthesize from new transcript |
| unset          | set                 | preserve existing | synthesize from raw summary (legacy path) |
| set            | unset               | preserve existing | preserve existing |
| set            | set                 | preserve existing | preserve existing |

Equivalently: `--skip-audio` is the master switch and implies
`--skip-transcript`. `--skip-transcript` alone means "still regenerate
the MP3, but from raw summary, not from a new script."

**Invariant 1d extension:** on `--force` re-import, preserve
`transcript_path` whenever `--skip-audio` OR `--skip-transcript` is set.
Never delete or blank the other artifact on a one-sided skip — keep the
previous file on disk.

Script-generation failure is distinct from "skip": it warns, falls
through to raw-summary audio, and leaves any prior `transcript_path`
intact. The user can retry with `transcript regenerate`.

## 6. Call sites that must route through `render_audio_assets()`

All six current inline-TTS paths plus the two new regenerate entry points:

1. `src/paper_assistant/pipeline.py:121-140` — local-note path in
   `create_local_entry()`.
2. `src/paper_assistant/pipeline.py:541-565` —
   `_generate_audio_for_import()` used by `import_paper()`.
3. `src/paper_assistant/cli.py:334-358` — `_generate_audio_step()`.
4. `src/paper_assistant/web/routes.py:186-195` — `/api/add`
   web-article inline TTS.
5. `src/paper_assistant/web/routes.py:272-280` —
   `_api_add_arxiv()` inline TTS.
6. `src/paper_assistant/web/routes.py:643-659` —
   `/api/paper/{id}/summary` edit + regen inline TTS.
7. New: `paper-assist transcript regenerate <paper_id>` (CLI).
8. New: `POST /api/paper/{paper_id}/transcript/regenerate` (web; see §8.2).

## 7. Storage + config plumbing (review v1 #3)

### 7.1 Config (`src/paper_assistant/config.py`)

Add `transcripts_dir` property alongside `papers_dir` / `audio_dir` /
`pdfs_dir` (current code at `:48-70`), resolving to
`self.data_dir / "transcripts"`. Include it in `ensure_dirs()` at
`:72-75`.

New fields:

| Field | Env | Default |
| ----- | --- | ------- |
| `tts_backend` | `PAPER_ASSIST_TTS_BACKEND` | `"mlx"` |
| `mlx_tts_url` | `PAPER_ASSIST_MLX_TTS_URL` | `"http://127.0.0.1:8000"` |
| `mlx_tts_model` | `PAPER_ASSIST_MLX_TTS_MODEL` | `"Voxtral-4B-TTS-2603-mlx-bf16"` |
| `mlx_tts_voice` | `PAPER_ASSIST_MLX_TTS_VOICE` | `None` (server default) |
| `mlx_tts_timeout_s` | `PAPER_ASSIST_MLX_TTS_TIMEOUT` | `120` per chunk |
| `mlx_tts_chunk_chars` | `PAPER_ASSIST_MLX_TTS_CHUNK_CHARS` | `2000` |
| `mlx_tts_max_input_chars` | `PAPER_ASSIST_MLX_TTS_MAX_INPUT_CHARS` | `6000` |
| `tts_edge_fallback` | `PAPER_ASSIST_TTS_EDGE_FALLBACK` | `True` |
| `audio_script_model` | `PAPER_ASSIST_AUDIO_SCRIPT_MODEL` | `"claude-haiku-4-5-20251001"` |

Existing `tts_voice` / `tts_rate` stay wired to the edge backend.

### 7.2 Models

`Paper` gains `transcript_path: str | None = None` (after `audio_path`
at `models.py:93`).

### 7.3 Test fixtures

`tests/conftest.py:6-10` — add `"transcripts"` to the pre-created
subdirs in `tmp_data_dir`.

### 7.4 Result surfaces (review v1 #6)

- `ImportResult` (`pipeline.py:56-65`) gains
  `transcript_path: Path | None`.
- `cli._import_result_to_dict` (`cli.py:535-545`) surfaces it.
- `cli._print_import_result` (`cli.py:548-563`) prints
  `"Transcript: …"` when present.
- Web JSON responses on `/api/add`, `/api/paper/{id}/summary`, and the
  new regenerate route include `transcript_path`, `audio_path`,
  `backend_used`, `warnings`.

## 8. Surface additions

### 8.1 CLI

- `--skip-transcript` flag on `add`, `import`, `skill-import`
  (parallels `--skip-audio`).
- `paper-assist transcript regenerate <paper_id> [--model MODEL]
  [--script-file PATH]` — loads stored summary, runs
  `normalize_summary_body()`, calls `render_audio_assets()` with
  `provided_script_markdown` (if `--script-file`) or
  `script_model_override` (if `--model`).
- `paper-assist tts check` — probes MLX `/v1/models`, reports ffmpeg
  presence + version, synthesizes a 1-sentence probe, reports latency
  and output size, prints whether fallback would engage. Exits non-zero
  on config errors (§4.3).

### 8.2 Web (review v2 #3)

- Canonical route: **`POST /api/paper/{paper_id}/transcript/regenerate`**
  (matches `/api/paper/{paper_id}/summary` at
  `web/routes.py:606`).
- Add a "Regenerate transcript + audio" button on the paper detail page,
  mirroring the per-paper Notion sync button from commit `9206e2d`.
  Button calls the new endpoint and surfaces `transcript_path`,
  `audio_path`, `backend_used`, and any `warnings` in the UI.

### 8.3 Skill workflow

- `skill-import` accepts `--script-file <path>`. Precedence inside
  `render_audio_assets()` is
  `provided_script_markdown` > API generation > raw-summary fallback.

## 9. Pre-implementation probe (Step 0)

```
curl -s http://127.0.0.1:8000/v1/models | jq
curl -s -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"Voxtral-4B-TTS-2603-mlx-bf16","input":"hello","response_format":"mp3"}' \
  --output /tmp/probe.mp3
file /tmp/probe.mp3
```

If endpoint path or body schema diverges from OpenAI-compat, only
`MlxTTSBackend` body changes; all other modules stay the same.

## 10. Implementation order

0. **Probe the MLX server** (§9). Record the confirmed endpoint shape.
1. **Track A.0 — shared helper + call-site routing.** Introduce
   `audio_assets.render_audio_assets()`, `normalize_summary_body()`, and
   `storage.save_transcript()`; route all six call sites through the
   helper. Behavior still uses edge + raw summary. Pure refactor; existing
   tests must keep passing.
2. **Track A.1 — MLX backend + `tts check`.** Implement `TTSBackend`,
   `MlxTTSBackend`, `EdgeTTSBackend`, `get_tts_backend()`, ffmpeg policy.
   Audio quality upgrade lands with no content-shape change.
3. **Track B — narration script.** Implement `audio_script.py`,
   prompt, helper wiring, `--skip-transcript`, CLI + web regenerate
   surfaces, `--script-file` skill flow. Transcript + audio default ON.
4. Update `CLAUDE.md`, `README.md`, prompts, and tests. Manual listening
   QA.

Each numbered step is independently shippable.

## 11. Dependencies

- `pydub` — MP3 chunk concat.
- `httpx` — confirm already present; add if not.
- `respx` (dev) — HTTP mocking for `tests/test_tts_mlx.py`.
- System: `ffmpeg` (recommended, not required). README gets
  `brew install ffmpeg` note.

## 12. Docs + invariants to update

- **`CLAUDE.md`**:
  - Rewrite invariant 5: "TTS input is the derived narration script at
    `transcript_path` when available; otherwise the filtered full
    markdown via `prepare_text_for_tts()`."
  - Extend invariant 1d with the force × skip matrix from §5.
  - Add: primary TTS backend is local MLX; edge-tts is fallback.
  - Add: audio-asset generation is centralized in
    `audio_assets.render_audio_assets()`; all inline TTS call sites
    route through it.
  - Add: `normalize_summary_body()` is the single source of truth for
    stripping YAML + duplicated title headers from stored summaries.
  - Add: ffmpeg is recommended for long-paper audio on the MLX path.
- **`README.md`**:
  - Document `PAPER_ASSIST_TTS_BACKEND` and the MLX env vars.
  - Document `--skip-transcript` and `paper-assist tts check`.
  - Document `paper-assist transcript regenerate` + the web button.
  - Recommend `brew install ffmpeg` for long papers.
- **`docs/roadmap.md`** — note skill-driven transcript generation as follow-up (§14).

## 13. Verification

### Automated

- `pytest tests/` — full suite per `CLAUDE.md` "Testing".
- New tests:
  - `tests/test_audio_assets.py` — every force × skip matrix cell;
    script failure falls back to legacy path; MLX failure falls back to
    edge; `provided_script_markdown` short-circuits generation;
    `script_model_override` routes to the override model;
    `transcript_path` preserved across `--force` per §5.
  - `tests/test_tts_mlx.py` — `respx`-mocked `/v1/audio/speech`: success
    (single + multi-chunk), connect refused → edge fallback, 5xx →
    edge fallback, 4xx → `MlxConfigError` (no fallback, caller warns),
    timeout → edge fallback, ffmpeg missing + oversize → edge fallback,
    ffmpeg missing + fits single chunk → MLX succeeds.
  - `tests/test_audio_script.py` — mocks `anthropic.AsyncAnthropic`;
    asserts script persisted via `save_transcript`, `transcript_path`
    set, graceful fallback when API key missing / call raises.
  - `tests/test_summarizer.py` — `normalize_summary_body` round-trip:
    strips YAML + duplicated title, leaves pre-stripped bodies
    unchanged.
- Extended tests:
  - `tests/test_storage.py` — `transcript_path` round-trip;
    `save_transcript` path shape; `delete_paper` cleans up transcripts.
  - `tests/test_skill_import.py:407-458` — update the force matrix to
    cover `transcript_path`.
  - `tests/test_tts.py` — backend factory routing;
    `prepare_script_for_tts` leaves clean prose untouched and does not
    prepend the legacy intro; edge-backend path behavior unchanged.
  - `tests/test_pipeline.py`, `tests/test_cli_*`, `tests/test_web_*`
    — every call site in §6 routes through `render_audio_assets` with
    transcript on/off.
  - New `tests/test_cli_transcript_regenerate.py` and
    `tests/test_web_transcript_regenerate.py` for the new surfaces.

### Live / manual

- Live integration test gated on `PAPER_ASSIST_TEST_LIVE_TTS=1` (skipped
  in CI): hits real MLX server, synthesizes one paragraph, decodes with
  pydub.
- Manual listening QA on one real paper: regenerate with the new path
  and compare the first 90 s against the current edge-tts MP3. Listen
  for: Voxtral prosody, equations paraphrased (not "equation omitted"),
  no table pipe noise, smooth section transitions, Reading List /
  Follow-ups not read.
- Run `paper-assist tts check` — expect MLX reachable, ffmpeg detected,
  probe MP3 playable, no warnings.

## 14. Follow-ups / Future work

Tracked in `docs/roadmap.md` (2b). Not in scope for this plan.

### 14b. Skill-driven transcript generation (roadmap 2b)

Today `render_audio_assets()` always calls the Anthropic API with
`config.audio_script_model` (default Haiku) to produce the narration
script. Even when the caller is a Claude Code / Codex / Kiro skill
session whose own model is stronger, the transcript step still
round-trips through an extra billable API call.

**Goal.** Let the host agent produce the narration script as a skill
artifact, skipping the dedicated Anthropic call entirely. The pipeline
already supports this via `provided_script_markdown` — the missing
piece is a first-class skill workflow that:

1. Reads the saved summary + narration prompt.
2. Writes the script to
   `.artifacts/summarize-paper/<paper_id>.transcript.md`.
3. Pipes it into `paper-assist transcript regenerate <paper_id>
   --script-file <path>` (already supported) — or a new
   `skill-regenerate-transcript` CLI that handles the handoff.

**Benefits.**
- Zero extra API cost per transcript when the user is already running a
  skill session.
- Users get transcripts from whichever model is driving their session
  (Claude Opus, Sonnet, GPT-5, etc.) without changing
  `PAPER_ASSIST_AUDIO_SCRIPT_MODEL`.
- Keeps the self-contained skill flow: summary + transcript both come
  from the same session.

**Open questions.**
- Should the skill regenerate the transcript automatically after the
  summary is saved, or remain a separate user-initiated step?
- Should `render_audio_assets()` gain a `skip_script_generation=True`
  flag for skill callers, so they can ship only `provided_script_markdown`
  without the pipeline ever attempting an API fallback?
- Prompt reuse: read the canonical prompt from
  `src/paper_assistant/prompts/audio_script_instructions.md` (packaged
  with the wheel) so skill and API paths stay aligned.
