# HF-First ArXiv Retrieval

## Summary
- Treat Hugging Face paper pages as the primary source for arXiv paper metadata and body content in arXiv import and summarize/add flows.
- Use direct HTTP with existing `httpx` for application/runtime code, not the external `hf` binary.
- Keep the current arXiv API and PDF pipeline as fallback, because HF coverage and HTML quality are not guaranteed.
- Keep `skill-import` receiving the canonical arXiv URL so `paper_id` remains the arXiv ID.

## Key Changes
- Add an internal HF paper client, ideally in `src/paper_assistant/hf_papers.py`, that calls `GET /api/papers/{id}` for metadata and `GET /papers/{id}.md` for content.
- Map HF metadata into `PaperMetadata` as: `id -> arxiv_id`, `authors[].name -> authors`, `published_at -> published`, `summary -> abstract`, `title -> title`, and set `categories=[]`, `arxiv_url`, and `pdf_url`.
- Make arXiv import flows prefer HF metadata first, then fall back to the current arXiv metadata fetch, then keep the existing markdown-derived metadata fallback only for pre-generated summary imports.
- Make arXiv add/summarize flows prefer HF metadata plus HF markdown first, then fall back to the current PDF/native/extract-text path when HF content is unavailable or low quality.
- Add a strict HF content gate: only trust HF markdown when `URL Source` is `https://arxiv.org/html/...`, the wrapper is stripped cleanly, the body contains an `Abstract` heading, and the remaining body text is at least 2500 characters. Otherwise, fall back to PDF.
- Keep `hf papers info/read` in the skill/manual workflow, but update `skills/codex/summarize-paper/SKILL.md` to treat `hf papers info` as the metadata companion to `hf papers read` instead of trying to infer metadata from the markdown wrapper.
- Do not add a runtime dependency on `huggingface_hub` or shell out to `hf` from FastAPI/CLI code paths; reserve CLI usage for agent skills and manual operations.

## Test Plan
- Add unit tests for HF metadata JSON to `PaperMetadata` mapping, including missing optional fields and `categories=[]`.
- Add unit tests for HF markdown parsing that cover accepted arXiv HTML output, rejected HF paper-page fallback output, and rejected short or wrapper-only output.
- Add import-path tests proving that HF metadata avoids arXiv metadata failures and that pre-generated summary import still falls back to summary-derived metadata when both HF and arXiv metadata are transiently unavailable.
- Add add/summarize-path tests proving that valid HF markdown is used as the paper body and that failed or rejected HF markdown falls back to the existing PDF flow.
- Add regression tests for CLI and WebUI arXiv add/import entrypoints so the new source priority is consistent across all arXiv flows.
- Use captured real-response fixtures from these paper IDs for happy-path coverage:
  - `2603.19835` — `FIPO: Eliciting Deep Reasoning with Future-KL Influenced Policy Optimization`; verified to return rich HF metadata and substantial arXiv-HTML markdown.
  - `2503.10291` — `VisualPRM: An Effective Process Reward Model for Multimodal Reasoning`; useful because it is already a repo-familiar paper ID and is indexed on HF with structured metadata.
  - `2601.15621` — `Qwen3-TTS Technical Report`; useful as a second metadata shape with many authors and a different paper style.
- Keep fallback-path tests deterministic by mocking HF `404`, HF short-body responses, and arXiv/PDF failures rather than relying on live network behavior in CI.

## Assumptions
- HF metadata is sufficient for this repo even without arXiv categories, because `categories` are not used elsewhere today.
- HF is primary only for arXiv papers; web article and local note flows remain unchanged.
- Direct HTTP is the chosen runtime integration style because it reuses `httpx`, avoids subprocess brittleness, and is easier to test than either `hf` subprocesses or a new `huggingface_hub` dependency.
- The implementation should store real-paper fixtures as local test data, not run live HF requests in the test suite.
