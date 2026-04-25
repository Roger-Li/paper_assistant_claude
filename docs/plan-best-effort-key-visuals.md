# Best-Effort Key Visuals in Paper Summaries

## Summary
- Add default-on, best-effort visual embedding for arXiv/Hugging Face paper summaries.
- Use `hf papers read` / arXiv HTML figure links first. Verified output includes links like `![Image 1](https://arxiv.org/html/<id>v1/x1.png)` followed by captions.
- Include only 1-3 crucial visuals. If a crucial figure/table cannot be confidently linked, skip the image and keep the prose description.

## Implementation Changes
- Add an internal visual helper, likely `src/paper_assistant/visuals.py`, to parse HF markdown for image-backed figure candidates: figure number, image URL, caption, and nearby anchor text.
- For built-in CLI/Web arXiv add flows, extract candidates from HF markdown, summarize as usual, then inject matching image Markdown near the relevant figure/table discussion when the summary references `Fig. N` / `Figure N`.
- Update `skills/codex/summarize-paper/SKILL.md` and the shared paper summary instructions so agent summaries include exact HF/arXiv image Markdown only for 1-3 crucial visuals, and skip when uncertain.
- Preserve storage shape: no `index.json` schema change. Images live as normal Markdown in saved summaries.
- Update Web UI CSS so summary images render cleanly and responsively.
- Update Notion markdown conversion to support external image blocks from Markdown image syntax, plus image block round-tripping where feasible.
- Update TTS markdown cleanup so image Markdown is removed or reduced to harmless caption text before narration.

## Public Interfaces
- No new required CLI flags.
- Default behavior: attempt visual embedding automatically for HF/arXiv HTML markdown paths.
- Degradation behavior: no warnings unless useful for debugging; summary import/add must still succeed when no visual candidates exist or no candidate matches a crucial figure/table.

## Test Plan
- Unit test HF visual extraction from real-style `hf papers read` markdown with image links and captions.
- Unit test visual injection chooses at most 3 referenced figures and skips unreferenced or missing candidates.
- Unit test Notion conversion: `![caption](https://...)` becomes an external Notion image block.
- Unit test TTS cleanup does not speak raw image URLs or Markdown syntax.
- Run targeted tests, then `pytest tests/`.

## Assumptions
- For v1, table “images” are included only if HF/arXiv HTML exposes them as image-backed visuals. Text-only Markdown tables remain textual.
- PDF cropping is not part of v1. If HF links are absent or ambiguous, the workflow skips images instead of doing fragile crop detection.
- Web UI and Notion are both supported destinations, with external image URLs rather than local downloaded copies.
