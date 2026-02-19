# Plan: Support Non-arXiv Web Articles in Paper Assistant

## Context

Currently, Paper Assistant only accepts arXiv URLs. The user wants to summarize long technical articles from arbitrary URLs (e.g., `https://thinkingmachines.ai/blog/on-policy-distillation/`). The `arxiv_id` is deeply embedded as the primary key across models, storage, CLI, web routes, templates, Notion sync, and RSS feed. This plan generalizes the identifier system and adds a web article ingestion pipeline.

**Key decisions (confirmed with user):**
- ID: URL-derived slug (e.g., `thinkingmachines-ai-blog-on-policy-distillation`)
- Metadata: Auto-extract from HTML (Open Graph / meta tags), CLI flag overrides
- Prompt: New article-specific prompt template (not assuming academic paper structure)
- CLI: Extend existing `add`/`import` commands with auto-detection

---

## Step 1: Add dependencies (`pyproject.toml`)

Add `trafilatura>=1.6.0` (HTML content extraction) and `beautifulsoup4>=4.12.0` (fallback + meta tag parsing).

**File:** `pyproject.toml`

---

## Step 2: Generalize data model (`models.py`)

- Add `SourceType` enum: `ARXIV = "arxiv"`, `WEB = "web"`
- Add fields to `PaperMetadata`:
  - `source_type: SourceType = SourceType.ARXIV` (backward-compatible default)
  - `source_url: str | None = None` (canonical URL for web articles)
  - `source_slug: str | None = None` (URL-derived slug for web articles)
- Make arXiv-specific fields optional: `arxiv_id: str | None = None`, `arxiv_url: str | None = None`, `pdf_url: str | None = None`
- Make `published: datetime | None = None` and `abstract: str = ""` (web articles may lack these)
- Add computed property `paper_id` -> returns `arxiv_id` for arXiv papers, `source_slug` for web articles
- Update `PaperIndex.papers` comment to note it's keyed by `paper_id`

**Backward compat:** Existing `index.json` entries have `arxiv_id` set and no `source_type`. The default `source_type=ARXIV` ensures they deserialize correctly. The `paper_id` property returns `arxiv_id` when present, so existing keys still work.

**File:** `src/paper_assistant/models.py`

---

## Step 3: New web article module (`web_article.py`)

Create `src/paper_assistant/web_article.py` with:

- `is_arxiv_url(url: str) -> bool` -- check if URL matches arXiv patterns (reuse regex from `arxiv.py`)
- `slugify_url(url: str, max_length: int = 80) -> str` -- extract domain+path, strip `www.`/scheme/query/fragment, replace non-alphanumeric with hyphens, collapse, truncate
- `async fetch_article(url: str) -> tuple[PaperMetadata, str]`:
  - Fetch HTML with `httpx` (already a dependency)
  - Extract metadata from `<title>`, `og:title`, `og:description`, `article:author`, `article:published_time` using `beautifulsoup4`
  - Extract article body text using `trafilatura.extract()`, fallback to BeautifulSoup text stripping
  - Build `PaperMetadata` with `source_type=WEB`, `source_url=url`, `source_slug=slugify_url(url)`, `arxiv_id=None`
  - Return `(metadata, body_text)`

**File:** `src/paper_assistant/web_article.py` (new)

---

## Step 4: Add article prompt template (`prompt.py`)

Add `ARTICLE_SYSTEM_PROMPT` and `ARTICLE_USER_PROMPT_TEMPLATE`:
- Framed for "technical articles/blog posts" instead of "ML research papers"
- Sections adapted: keep One-Pager Summary, Rapid Skim, Deep-Structure Map, Critical Q&A, Technical Details, Glossary, Reading List; drop "Key Figures and Tables" (or make it optional)
- User template: `{title}`, `{authors}`, `{source_url}`, `{article_content}` (no `arxiv_id`)

**File:** `src/paper_assistant/prompt.py`

---

## Step 5: Generalize summarizer (`summarizer.py`)

- Add `summarize_article_text(config, metadata, article_text)` using the new article prompts
- Update `format_summary_file(metadata, summary)`:
  - Branch on `metadata.source_type`:
    - **ARXIV**: current behavior (writes `arxiv_id:`, `**arXiv**:` link)
    - **WEB**: writes `source_url:`, `source_slug:` in YAML, `**Source**: [title](url)` in header
  - Handle `published` being `None` (omit from YAML if absent)

**File:** `src/paper_assistant/summarizer.py`

---

## Step 6: Rename storage parameters to `paper_id` (`storage.py`)

- Rename all `arxiv_id` parameters to `paper_id` across all methods: `get_paper`, `delete_paper`, `paper_exists`, `add_tags`, `remove_tag`, `set_reading_status`, `save_summary`, `save_audio`, `set_archived`, `set_notion_fields`
- In `add_paper`: change `index.papers[paper.metadata.arxiv_id]` -> `index.papers[paper.metadata.paper_id]`
- In `save_summary`: use `paper_id` for `make_summary_filename`
- In `save_audio`: use `paper_id` for `make_audio_filename`
- Update `make_summary_filename(arxiv_id, title)` -> `make_summary_filename(paper_id, title)` (cosmetic rename)
- Update `make_audio_filename(arxiv_id)` -> `make_audio_filename(paper_id)`, same for `make_pdf_filename`
- In `sort_key`: `arxiv_id` sort key -> use `p.metadata.paper_id`

**File:** `src/paper_assistant/storage.py`

---

## Step 7: Update CLI pipeline (`cli.py`)

- At the top of `_add_paper(url, ...)`:
  - Call `is_arxiv_url(url)` to dispatch
  - **arXiv path**: existing flow (unchanged)
  - **Web path**: call `fetch_article(url)` -> get `(metadata, body_text)` -> call `summarize_article_text(config, metadata, body_text)` -> `format_summary_file` -> `save_summary` -> TTS -> feed
- At the top of `_import_paper(url, ...)`:
  - Same dispatch: arXiv -> `fetch_metadata` from arXiv API; web -> `fetch_article` for metadata only (body_text unused since user supplies summary)
- Update all `storage.*(arxiv_id)` calls to use `paper_id` (from `metadata.paper_id`)
- Update step labels: "Parsing arXiv URL..." -> "Parsing URL..." for the generic case

**File:** `src/paper_assistant/cli.py`

---

## Step 8: Update web routes (`web/routes.py`)

- Rename all `{arxiv_id}` path parameters to `{paper_id}` in route decorators and function signatures
- Update `api_add_paper` and `api_import_paper`:
  - Dispatch on `is_arxiv_url(req.url)` -- same logic as CLI
- Update all `storage.*(arxiv_id)` calls to use `paper_id`

**File:** `src/paper_assistant/web/routes.py`

---

## Step 9: Update templates (`web/templates/`)

- Rename `data-arxiv-id` -> `data-paper-id`, JS var `arxivId` -> `paperId`
- Column header: "arXiv ID" -> "ID" (or "Source ID")
- Display logic: show `arxiv_id` for arXiv papers, slug for web articles
- Link: arXiv papers link to `arxiv_url`, web articles link to `source_url`
- Input placeholder: add example of both URL types
- Update all JS `fetch()` URLs from `/api/paper/${arxivId}/...` -> `/api/paper/${paperId}/...`

**Files:** `src/paper_assistant/web/templates/index.html`, `src/paper_assistant/web/templates/paper.html`

---

## Step 10: Update TTS intro (`tts.py`)

- `prepare_text_for_tts(markdown, title, authors, source_type="paper")`:
  - arXiv: "This is a summary of the paper: {title}..."
  - web: "This is a summary of the article: {title}..."

**File:** `src/paper_assistant/tts.py`

---

## Step 11: Update podcast feed (`podcast.py`)

- Episode GUID: use `paper.metadata.paper_id` instead of `paper.metadata.arxiv_id`
- Episode link: use `paper.metadata.source_url or paper.metadata.arxiv_url`

**File:** `src/paper_assistant/podcast.py`

---

## Step 12: Update Notion sync (`notion.py`)

### What the user must change in the Notion database (manual, one-time)

Add **one new column** to the existing Notion database:

| New column name | Type | Purpose |
|-----------------|------|---------|
| `source_slug` | Rich text | Stores the URL-derived slug for web articles (e.g., `thinkingmachines-ai-blog-on-policy-distillation`). Left **empty** for arXiv papers. |

The code infers source type from which field is populated: `arxiv_id` -> arXiv paper, `source_slug` -> web article. No `source_type` column needed in Notion.

**Why this is safe for existing arXiv entries:**
- Existing rows keep their `arxiv_id` populated -- sync continues to join on `arxiv_id` for those
- The new column is empty for existing rows -- code ignores it and matches on `arxiv_id` as before
- No existing data is modified or reinterpreted

### Code changes in `notion.py`

1. **`_ensure_property_keys()`** -- Add `source_slug` to the `expected` dict, but make it **optional** (don't raise `ValueError` if missing). This way sync still works even if the user hasn't added the column yet -- web articles simply won't have the slug populated in Notion.

2. **`NotionPaper` dataclass** -- Add `source_slug: str | None` field. `_parse_page` populates it from the Notion page properties.

3. **`_build_properties()`** -- Accept optional `source_slug` param. Write it to Notion only if the property key exists in the database.

4. **Join logic in `sync_notion()`** -- Build a second lookup dict `remote_by_slug: dict[str, NotionPaper]` alongside the existing `remote_by_arxiv`. For each local paper:
   - If it has `arxiv_id`: match via `remote_by_arxiv` (unchanged)
   - If it has `source_slug` (web article): match via `remote_by_slug`
   - This keeps arXiv sync exactly as-is

5. **`_import_remote_only()`** -- Currently skips pages without `arxiv_id`. Update to also accept pages with `source_slug` but no `arxiv_id`. For web-sourced Notion pages, create a local `Paper` with `source_type=WEB` and `source_slug` as the key (no arXiv metadata fetch needed).

6. **`_push_local_to_notion()` and `_set_local_from_remote()`** -- Replace `paper.metadata.arxiv_id` references with `paper.metadata.paper_id` so these work for both source types.

**File:** `src/paper_assistant/notion.py`

---

## Step 13: Add/update tests

- **New:** `tests/test_web_article.py` -- test `slugify_url`, `is_arxiv_url`, `fetch_article` (mocked HTTP)
- **Update:** `tests/test_storage.py` -- add web article CRUD tests, backward-compat deserialization test
- **Update:** `tests/test_summarizer.py` -- test `format_summary_file` for web articles
- **Update:** `tests/test_web_*.py` -- test web routes with `paper_id` path params
- **Update:** `tests/test_notion.py` -- test sync with web articles (both directions)

---

## Step 14: Update docs

- **`README.md`**: Add web article usage examples to "Add a paper" section, note new dependencies
- **`CLAUDE.md`**: Update Code Map (add `web_article.py`), add invariant for `paper_id` resolution, update API surface path params, update roadmap

---

## Step 15: Save design doc to `docs/` folder

Save this planning document as `docs/design-web-article-support.md` in the repository for future reference.

---

## Implementation Order

| Order | Files | Risk | Can test after |
|-------|-------|------|----------------|
| 1 | `pyproject.toml` | Low | `pip install -e .` |
| 2 | `models.py` | **High** | Unit tests for backward compat |
| 3 | `web_article.py` (new) | Medium | `test_web_article.py` |
| 4 | `prompt.py` | Low | -- |
| 5 | `summarizer.py` | Medium | `test_summarizer.py` |
| 6 | `storage.py` | **High** | `test_storage.py` |
| 7 | `cli.py` | Medium | Manual CLI test |
| 8 | `web/routes.py` | Medium | `test_web_*.py` |
| 9 | `web/templates/*.html` | Low | Browser test |
| 10 | `tts.py` | Low | -- |
| 11 | `podcast.py` | Low | -- |
| 12 | `notion.py` | Medium | `test_notion.py` |
| 13 | Tests | -- | `pytest tests/` |
| 14 | `CLAUDE.md`, `README.md` | Low | -- |
| 15 | `docs/design-web-article-support.md` | Low | -- |

---

## Verification

1. **Existing arXiv workflow**: `paper-assist add https://arxiv.org/abs/2503.10291` still works identically
2. **Web article add**: `paper-assist add https://thinkingmachines.ai/blog/on-policy-distillation/` -> fetches content, generates summary, audio, appears in index
3. **Web article import**: `paper-assist import https://thinkingmachines.ai/blog/on-policy-distillation/ --file summary.md` -> imports user-provided summary
4. **Web UI**: Both arXiv papers and web articles appear in the list, detail pages work, add/import via web works
5. **Backward compat**: Existing `index.json` with arXiv-only entries loads and works without migration
6. **Notion sync (arXiv)**: Existing arXiv papers sync exactly as before -- no changes needed to the Notion DB for these
7. **Notion sync (web article)**: After adding `source_slug` column to Notion DB, web articles sync bidirectionally using slug as the join key
8. **Notion sync (graceful degradation)**: If user hasn't added the new Notion column yet, sync still works for arXiv papers; web articles sync without the slug field populated
9. **Full test suite**: `pytest tests/` passes
