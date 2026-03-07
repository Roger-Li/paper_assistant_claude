# Design: Browser Reader Mode for Paper Detail Page

## Context

Paper Assistant already supports generated MP3 narration through `tts.py`, but that path is offline, file-based, and not interactive. The paper detail page needed a lightweight way to:

- start reading from an arbitrary sentence ("read from here")
- show reading progress inline with the text
- keep local runtime simple
- avoid introducing new backend services, timing metadata, or storage schema changes

The resulting feature is a browser-native Reader Mode built on the Web Speech API and rendered entirely on the client side.

---

## Goals

- Add interactive read-aloud to the paper detail page.
- Let users click any sentence to begin playback from that point.
- Highlight reading progress in the page while speech is active.
- Preserve technical article structure visually in Reader Mode.
- Keep `paper-assist serve` unchanged.
- Avoid changes to `index.json`, backend APIs, or audio storage.

## Non-Goals

- Replacing generated MP3 audio.
- Word-perfect alignment or karaoke-style word highlighting.
- Reading tables, equations, or code blocks verbatim.
- Cross-browser parity beyond desktop Brave/Chromium quality targets.

---

## Core Product Decision

Reader Mode is separate from generated audio.

- Generated MP3 audio remains the durable, shareable narration path.
- Reader Mode is an interactive browser feature for local reading sessions.
- Reader Mode uses browser voices, not the `edge-tts` voice configured by `PAPER_ASSIST_TTS_VOICE`.

This separation keeps the implementation local-first and avoids building a second backend TTS pipeline just for "read from here."

---

## Rendering Model

### Source of truth

The normal rendered markdown view remains the canonical page content.

### Hybrid Reader Mode

Reader Mode does not rebuild the article as plain text anymore. Instead it:

1. renders markdown and KaTeX into the normal summary article
2. clones that rendered HTML into a dedicated Reader Mode container
3. keeps technical blocks visible in the cloned view
4. wraps only speakable prose into sentence fragments for interaction/highlighting

This hybrid approach was chosen because the original prose-only Reader Mode dropped too much structure for dense technical summaries.

### Speakable content

Speech targets are built from:

- `p`
- `li`
- `blockquote`
- `h1`
- `h2`
- `h3`

Speech explicitly skips:

- `pre`
- `code`
- `table`
- `.katex`
- `.katex-display`

Technical blocks still render in Reader Mode, but they are passive visual content rather than speech targets.

---

## Sentence Preparation

Sentence segmentation is client-side:

- prefer `Intl.Segmenter` with sentence granularity
- fall back to a punctuation-based regex splitter

Each speakable block is scanned for eligible text nodes. Eligible text is mapped back into DOM fragments so the UI can:

- click a sentence to start reading there
- mark the active sentence
- mark already-read sentences
- keep the original rendered layout mostly intact

One important implementation detail: the Reader Mode content container starts hidden until enabled, so hidden-state checks must ignore the Reader Mode root itself. Otherwise every cloned child looks hidden and zero sentences are prepared.

---

## Speech Pipeline

### Voice selection

Reader Mode uses browser-provided voices from `speechSynthesis.getVoices()`.

Selection strategy:

- prefer a previously saved user choice
- otherwise prefer the browser default voice
- rank English/local/default voices higher
- filter or demote obvious novelty / low-quality voices

### Playback model

The initial one-utterance-per-sentence design was too choppy and made pacing unnatural. The current implementation instead:

1. groups prepared sentences into chunked utterances
2. sends each chunk to `SpeechSynthesisUtterance`
3. uses `boundary` events to advance sentence highlighting within the chunk when available

This improves prosody while preserving sentence-level click targets and progress indication.

### Playback controls

Reader Mode supports:

- click any sentence to start from there
- `Play from top`
- `Pause` / `Resume`
- `Stop`
- keyboard shortcuts:
  - `K` or `Space` to pause/resume
  - `Escape` to stop

Global keyboard shortcuts run in capture phase so they still work when a focusable sentence fragment has focus.

### Cleanup

Speech is cancelled on:

- Reader Mode disable
- page unload / page hide
- edit-mode entry

---

## State and Storage

Reader Mode is client-side only.

Persisted in `localStorage`:

- enabled/disabled state
- selected voice
- selected rate

Not persisted anywhere else:

- playback position
- sentence timing
- progress metadata

No backend API, model, or `index.json` changes are required.

---

## Why This Still Runs Easily Locally

Local runtime stays simple because Reader Mode adds:

- no new Python dependencies
- no background worker
- no speech timing files
- no database fields
- no server-side preprocessing

The only runtime requirement is a desktop browser with usable Web Speech support, with Brave/Chromium as the intended target.

---

## Testing Strategy

### Automated

Keep automated tests at the HTML contract level:

- Reader Mode toolbar renders when a summary exists
- Reader Mode assets/bootstrap hook are included
- helper text is present
- MP3 audio player still renders independently
- pages without summaries do not render Reader Mode controls

This fits the current pytest-based web route coverage without adding browser automation infrastructure.

### Manual

Manual desktop Brave/Chromium QA is still required for:

- speech voice quality
- sentence progression
- boundary-event highlight updates
- pause/resume behavior
- keyboard shortcuts
- interaction with long technical summaries

Recommended checks:

- click a middle sentence and confirm playback starts there
- verify active/read/paused highlighting changes correctly
- verify `K` / `Space` pause and resume
- verify `Escape` stops
- verify tables, equations, and code remain visible but are not spoken
- verify editing a summary rebuilds Reader Mode correctly

---

## Known Limitations

- Browser voice quality varies by machine and browser.
- Browser rate settings are voice-relative, not globally consistent.
- `speechSynthesis.pause()` / `resume()` may resume at the current sentence boundary rather than the exact phoneme/word position.
- Word-level sync is intentionally out of scope.
- Technical blocks are visible but skipped in speech.
- Reader Mode is optimized for desktop usage, not mobile-first behavior.

---

## Future Improvements

- Optional sticky mini-toolbar while actively reading.
- Better fallback when `boundary` events are missing or unreliable.
- Smarter spoken placeholders for skipped technical blocks, such as "equation omitted."
- Better cache-busting for static JS assets during local iteration.
- Optional persistence of last-read sentence for a paper.

---

## Files Touched by This Design

- `src/paper_assistant/web/templates/paper.html`
- `src/paper_assistant/web/static/style.css`
- `src/paper_assistant/web/static/reader_mode.js`
- `tests/test_web.py`
- `README.md`
- `CLAUDE.md`
