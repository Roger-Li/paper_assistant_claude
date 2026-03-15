# Paper Assistant Workflow Optimization — Full Roadmap

This document is a comprehensive plan for optimizing the paper-assistant workflow.
Roadmap items are tracked in the `Prioritized Roadmap` section of `CLAUDE.md`
(items 11–15) and are designed to be tackled one-by-one in individual Claude Code
sessions.

---

## Context

**Current workflow** (5 manual steps):
1. Generate summary in Claude Pro / ChatGPT Plus (avoids per-token API costs)
2. Copy summary to clipboard
3. `paper-assist import <url>` (reads clipboard)
4. `paper-assist notion-sync --paper <id>`
5. Read in Notion or web UI

**New needs**:
- **Single-paper summaries via Claude Code**: User should also be able to generate
  summaries within a Claude Code session using the existing prompt templates from
  `prompt.py`, with the ability to adapt/improve prompts based on a paper's content.
- **Multi-paper synthesis**: Lit reviews, comparisons, study guides that synthesize
  across the existing paper library **and** additional papers discovered during the
  task (e.g., via MCP search or user-provided URLs).
- **Dedicated synthesis prompts**: New prompt templates for lit review, comparison,
  and study guide tasks — these don't exist yet and need to be drafted with a TODO
  for user finalization.

**Cost constraint**: User prefers subscription-based chat for routine single-paper
summaries. Claude Code sessions are acceptable for higher-value tasks (synthesis,
deep analysis) and optionally for single-paper summaries when convenience matters.

---

## Existing External Tools to Evaluate / Integrate

### MCP Servers for Paper Discovery

These MCP servers can be added to `.claude/settings.json` to give Claude Code
direct access to academic paper databases — useful for discovering new papers
during lit reviews without leaving the session.

| Tool | Sources | Key Features | Link |
|------|---------|-------------|------|
| **arxiv-mcp-server** (blazickjp) | arXiv | Search, download, read full paper content as markdown, built-in "deep-paper-analysis" prompt | [GitHub](https://github.com/blazickjp/arxiv-mcp-server) |
| **paper-search-mcp** (openags) | arXiv, PubMed, bioRxiv, medRxiv, Google Scholar, Semantic Scholar, IACR | Multi-source search + download, consistent Paper class output | [GitHub](https://github.com/openags/paper-search-mcp) |
| **academic-search-mcp-server** (afrise) | Semantic Scholar, Crossref | Citation network exploration, Claude Desktop integration | [GitHub](https://github.com/afrise/academic-search-mcp-server) |
| **Semantic Scholar MCP** (FujishigeTemma) | Semantic Scholar | Literature search, citation analysis, paper recommendations | [mcpservers.org](https://mcpservers.org/servers/FujishigeTemma/semantic-scholar-mcp) |
| **Scite MCP** | 250M+ articles | Smart Citations (support/contrast/mention), evidence-backed answers. Requires Scite subscription. | [scite.ai/mcp](https://scite.ai/mcp) |

**MCP vs Skills for paper search — design decision**: MCP servers add tools to
every request's context (constant token overhead). Skills are loaded only when
explicitly invoked (on-demand, token-efficient). **Recommendation**: Use an MCP
server only when paper discovery is integral to the task (e.g., lit reviews).
For most single-paper workflows the user already has the URL, so MCP would be
wasted context. Keep MCP optional — slash commands should work with or without it.
Without MCP, the user provides URLs or paper IDs manually.

**Recommendation**: Start with `arxiv-mcp-server` (free, arXiv-focused) for
search and download capabilities. Use it for its MCP tools (search, download,
convert to markdown), **not** its built-in analysis prompts — our own prompts in
`prompt.py` are tailored to this repo's output format and should be preferred.

### Claude Code Skills for Research

**Strategy**: Prioritize custom skills tailored to this repo's audience and output
format. Reference community skills for inspiration but don't force-fit them. No
"official" Claude research skills exist, so the community ones below are the best
available for evaluation.

| Skill | What it does | Link |
|-------|-------------|------|
| **Academic Research Skills** (Imbad0202) | Full pipeline: 13-agent deep research, paper writing with LaTeX, multi-perspective peer review, 10-stage pipeline orchestrator | [GitHub](https://github.com/Imbad0202/academic-research-skills) |
| **Claude Scientific Skills** (K-Dense) | 170+ scientific skills across biology, chemistry, engineering, finance, writing | [GitHub](https://github.com/K-Dense-AI/claude-scientific-skills) |
| **AI Research Skills** (Orchestra Research) | Comprehensive research + engineering skills library for any AI model | [GitHub](https://github.com/Orchestra-Research/AI-Research-SKILLs) |
| **ArXiv Research Search skill** (mcpmarket) | Automates paper-search MCP setup for Claude Code | [mcpmarket](https://mcpmarket.com/tools/skills/arxiv-academic-paper-search-setup) |
| **Academic Paper Writing skill** (mcpmarket) | Parallel subagents for fetch, analyze, summarize papers into HTML artifacts | [mcpmarket](https://mcpmarket.com/tools/skills/academic-paper-writing-review) |

**Recommendation**: Evaluate Imbad0202's `/deep-research` pipeline structure for
multi-paper workflows. Adopt useful patterns but generate custom skills that
integrate with paper-assist's storage, Notion sync, and prompt templates.

### Reference: Curated Lists

- [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) — curated skills, hooks, slash commands, plugins
- [awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) — community Claude skills directory

---

## Roadmap Items (Split for Individual Sessions)

### R1. `--sync-notion` flag on `import` and `add` commands
**Scope**: Small — CLI + web API only
**Effort**: ~1 session

Add `--sync-notion` boolean flag to `import`, `add` CLI commands. When set,
call `sync_notion(paper_id=...)` after save. Graceful degradation on failure.
Also add `sync_notion` parameter to `POST /api/add` and `POST /api/import`.

Files: `cli.py`, `routes.py`
Tests: `test_cli_import.py`, `test_web_routes.py`
Docs: `README.md`, `CLAUDE.md` API surface section

### R2. `SourceType.NOTE` + `paper-assist create` command + synthesis prompts
**Scope**: Medium — model + storage + CLI + web + prompt templates
**Effort**: ~1 session

**Purpose**: `SourceType.NOTE` is for saving URL-less synthesis outputs — lit
reviews, paper comparisons, study guides — generated by Claude Code from multiple
papers. The `create` command saves these as paper-assist entries alongside regular
paper summaries, with the same Notion sync, tagging, and reading-status support.

**Code changes**:
- Add `SourceType.NOTE` enum value in `models.py`
- Add `slugify_title()` utility in `web_article.py`
- Add `create` CLI command for URL-less entries in `cli.py`
- Update `make_summary_filename` to use `[Note]` prefix in `storage.py`
- Add `POST /api/create` endpoint in `routes.py`

**Synthesis prompt templates** (add to `prompt.py`):
We don't have prompts for multi-paper tasks yet. Draft new templates and mark
as **TODO for user to finalize**:
- `LIT_REVIEW_SYSTEM_PROMPT` — structured literature review across N papers
  Sections: Overview, Thematic Analysis, Methodology Comparison, Key Findings,
  Research Gaps, Future Directions, Reading List
- `COMPARE_SYSTEM_PROMPT` — side-by-side paper comparison
  Sections: Summary Table, Shared Contributions, Divergences, Strengths/Weaknesses,
  Methodology Comparison, When to Use Which, Verdict
- `STUDY_GUIDE_SYSTEM_PROMPT` — study guide for learning a topic from papers
  Sections: Prerequisites, Concept Progression, Key Definitions, Core Techniques,
  Practice Questions, Recommended Reading Order

These prompts are used by the `/lit-review`, `/compare` slash commands (R5) and
can also be used directly in Claude Code sessions. The user should review and
customize them before finalizing.

Files: `models.py`, `web_article.py`, `storage.py`, `cli.py`, `routes.py`, `prompt.py`
Tests: `test_models.py`, `test_storage.py`, `test_cli_create.py`, `test_web_routes.py`

### R3. Evaluate and install an academic MCP server
**Scope**: Small — config only, no code changes
**Effort**: ~30 min

Try `arxiv-mcp-server` or `paper-search-mcp`. Add to `.claude/settings.json`.
Test that Claude Code can search for and read papers through the MCP tools.

No code changes. Config in `.claude/settings.json`.

### R4. Claude Code slash commands — `/summarize`, `/import-summary`
**Scope**: Small — markdown files only
**Effort**: ~1 session
**Depends on**: R1

Create `.claude/commands/summarize.md` (a Claude Code slash command / skill):
- Accepts arXiv URL/ID or web article URL as argument
- Fetches the paper via `arxiv.py`/`web_article.py` or MCP (if available)
- Generates summary using the **existing** prompt templates from `prompt.py`:
  `SYSTEM_PROMPT` for arXiv papers, `ARTICLE_SYSTEM_PROMPT` for web articles
- The skill instructs Claude to read `prompt.py` and follow those templates,
  but also adapt/improve the output based on the paper's specific content
  (e.g., more math detail for theory papers, more architecture detail for
  systems papers)
- Saves via `paper-assist import <url> --file <tmpfile> --sync-notion`
- This enables single-paper summarization entirely within Claude Code when
  the user prefers convenience over subscription-chat cost savings
- **A skill/slash command is the natural form for this task** — it wraps the
  fetch-summarize-import-sync pipeline into a single `/summarize` invocation,
  reusing the repo's own prompt templates rather than relying on external tools

Create `.claude/commands/import-summary.md`:
- Thin wrapper: reads clipboard, runs `paper-assist import <url> --sync-notion`
- For when user already generated a summary externally (Claude Pro, ChatGPT)

### R5. Claude Code slash commands — `/lit-review`, `/compare`
**Scope**: Medium — markdown files with detailed prompt engineering
**Effort**: ~1 session
**Depends on**: R2 (for `paper-assist create` + synthesis prompts), R3 (optional)

These commands use the **new synthesis prompt templates** drafted in R2
(`LIT_REVIEW_SYSTEM_PROMPT`, `COMPARE_SYSTEM_PROMPT`, etc.) and reference them
from the slash command markdown files.

Create `.claude/commands/lit-review.md`:
- Accepts topic/domain + optional tag filter as arguments
- Lists papers by tag via `paper-assist list`
- Reads summaries of matching papers from the library
- **Also discovers additional relevant papers** via MCP search (if available)
  or user-provided URLs for papers not yet in the library
- Generates structured literature review using `LIT_REVIEW_SYSTEM_PROMPT`
- Saves via `paper-assist create --title "..." --file <tmpfile> --tags lit-review --sync-notion`

Create `.claude/commands/compare.md`:
- Accepts 2+ paper IDs or URLs as arguments
- Reads summaries of specified papers (fetches new ones if needed)
- Generates structured comparison using `COMPARE_SYSTEM_PROMPT`
- Saves via `paper-assist create`

### R6. Web UI — "Sync to Notion" checkbox on add/import forms
**Scope**: Small — frontend only
**Effort**: ~30 min
**Depends on**: R1

Add checkbox to both forms in `index.html`. Update JS to pass `sync_notion` param.
Show sync result or warning in status message.

Files: `templates/index.html`

### R7. Evaluate community research skills
**Scope**: Exploration — no code changes
**Effort**: ~1 session

Clone and evaluate [academic-research-skills](https://github.com/Imbad0202/academic-research-skills)
and [claude-scientific-skills](https://github.com/K-Dense-AI/claude-scientific-skills).
Determine which skills complement vs. overlap with our custom slash commands.
Consider adopting their deep-research pipeline for more thorough lit reviews.

---

## Additional Suggestions

### S1. Full-text search across summaries (existing roadmap item #6)
Would significantly improve `/lit-review` — currently the slash command has to
grep through files. A `paper-assist search <query>` command that searches titles,
tags, and summary content would make paper discovery much faster.

### S2. Extract shared pipeline logic (existing roadmap item #8)
The `create` command will add a 5th pipeline variant. Before that, extracting the
shared add/import logic into `pipeline.py` would reduce duplication from ~400 lines
to ~100 and make all future pipeline changes (like `--sync-notion`) single-point.

### S3. Batch import from arXiv (existing roadmap item #5)
Combined with an MCP server for paper discovery, batch import could enable a
workflow like: search arXiv via MCP → select papers → batch import summaries.

### S4. Custom prompt templates
Allow users to customize the summary prompt template (e.g., for different
research domains or summary styles) by supporting a `~/.paper-assistant/prompts/`
directory. The current prompts in `prompt.py` are ML-focused; other domains may
want different section structures.

### S5. MCP server for paper-assist itself
Expose paper-assist as an MCP server so Claude Desktop can query your paper library
(list papers, read summaries, search) without needing the CLI. This would make
the `/lit-review` and `/compare` workflows available from Claude Desktop too.

---

## Implementation Order

The recommended execution order, each as a separate Claude Code session:

1. **R1** — `--sync-notion` flag (quick win, immediately improves daily workflow)
2. **R3** — Install academic MCP server (config only, unlocks paper discovery)
3. **R4** — `/summarize` + `/import-summary` commands (uses R1)
4. **R2** — `SourceType.NOTE` + `create` command (enables synthesis entries)
5. **R5** — `/lit-review` + `/compare` commands (uses R2, R3)
6. **R6** — Web UI checkbox (uses R1)
7. **R7** — Evaluate community skills (informational)
