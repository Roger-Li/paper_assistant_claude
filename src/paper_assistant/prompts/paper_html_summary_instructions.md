# Paper Assistant — HTML learning brief

Use this supplement when the user explicitly requests an HTML paper summary.
Read `paper_summary_instructions.md` for the shared truth-first, citation,
method-fidelity, language, and redundancy policies. This supplement replaces its
Markdown section skeleton and mandatory follow-up ending for the HTML artifact.
It does not change the default Markdown workflow.

## What the brief is for

The reader wants to learn the paper: understand the mechanism well enough to
explain it, know what the evidence does and does not show, and still have both a
week later. Assume an ML engineer and researcher who is fluent in general ML
but not necessarily in this paper's sub-field, reading on a laptop, unless the
user supplies another profile.

Two failures to avoid. A referee report that is accurate but assumes the
background teaches nothing to someone outside the sub-field. A friendly tutorial
that drops the paper's caveats teaches something false. Teach first, then audit,
and keep every claim traceable.

## Deliverable and scope

Create a self-contained `summary.html` under
`.artifacts/summarize-paper/<paper_id>/`. Preserve the requested paper version
and show its exact title, authors, date, and version-linked source.

This is a local artifact pilot. Do not call Notion preflight/sync, generate a
transcript/audio, or run `skill-import`/`create`. HTML is not a supported library
summary format; do not put it in `summary_path` or mutate `index.json`. Keep the
source artifacts and the finished HTML available for review. Explicit requests
for a different delivery workflow take precedence.

## Workflow

The kit in `skills/shared/html-brief/` does the mechanical work so that effort
goes into understanding the paper and explaining it.

1. **Fetch.** `.venv/bin/python skills/shared/html-brief/build_brief.py fetch <id>[vN] --out .artifacts/summarize-paper/<id>`
   pins the version and downloads `sources/abs.html`, `sources/paper.html`
   (arXiv HTML), `sources/paper.pdf`, and every figure file the HTML references.
   It writes `metadata.json` and `paper.md`, an anchor-annotated text of the
   paper. If the paper has no arXiv HTML, work from the PDF, cite PDF pages, and
   say in the report that anchors, math and table rows could not be verified.
2. **Read and record.** Read the main text and the appendices behind its key
   claims, and look at the figures themselves. Write `evidence.json` as you go
   (see "First establish the evidence").
3. **Write.** Copy `skills/shared/html-brief/skeleton.html` to `brief.src.html`
   and replace its content. Keep the frame; design the paper-specific diagrams
   and interactions yourself.
4. **Build.** `.venv/bin/python skills/shared/html-brief/build_brief.py build .artifacts/summarize-paper/<id>`
   writes `summary.html` and `brief-check.json`. Fix every error. Resolve each
   warning or explain it in the report.
5. **Look.** Render the built file and inspect it (see "Review before delivery").

## First establish the evidence

Before designing the page, keep an evidence ledger in `evidence.json`. Each entry
in `claims` records:

- `claim`, `anchor` (the source element id: section, table, figure), `context`
  (model, dataset, budget, baseline, metric, direction), and `caveat`.
- `transcribed`: numbers copied from the paper's text at that anchor.
- `rows`: for table values, lists such as `["SFT+SST", "80.1", "80.0"]` that must
  all sit in one row of the anchored table.
- `read_from_figure`: values read off a plot. `derived`: your arithmetic, with
  the calculation. `status`: `paper`, `derived`, `inference`, or `illustration`.
- Seed count and interval type where the paper gives them. If it gives none, say
  so in the brief; that is a finding.

Put tabulated results you will show or chart under `tables.<name>` with
`anchor`, `columns`, `numeric`, `verify` (the columns that must co-occur in one
source row), and `rows`. The build renders these tables and checks every
transcribed value and row against the cited element of the source HTML, so a
transcription slip fails the build instead of reaching the reader.

Transcribe reported differences directly when available. A difference computed
from rounded displayed means can disagree with the authors' unrounded estimate.
Label any derived quantity and its calculation. Never invent error bars, infer
equivalence from an insignificant result, or turn a restricted negative result
into a universal claim. Keep training-mixture changes separate from changes in
evaluation data, and token-matched results separate from time-matched results.
Cross-check tables against each other and against the figures; note
disagreements as your reading, not as fact.

For each central result, include the strongest qualification in the same reading
context. Do not hide all uncertainty in a collapsed appendix. If the paper is
silent on a consequential detail, say "Not in paper."

## Then teach it

Organize around what a reader needs in order to understand, in this order. Adapt
the parts to the paper; if one does not apply, leave it out and say why in the
report. Mark each part with its `data-part` value so the build can confirm it.

1. **In one minute** (`verdict`, `objectives`). The thesis in one or two plain
   sentences, then four rows: the claim, the strongest evidence with its size
   and setting, the biggest caveat, and who should read the paper. Add three to
   five things the reader will be able to do afterwards. Each objective is
   tested later in "Check yourself", so write them as abilities, not topics.
2. **Before you read** (`primers`). Two to four ideas the paper assumes and a
   reader outside the sub-field may not own. Each primer states the idea as a
   plain claim, gives a small concrete example, and says where it is needed
   later. Include how the paper measures its results. Do not write a primer for
   something the default reader already knows.
3. **The idea** (`mechanism`). Lead with the paper's own key figure when it has
   one. Then the mechanism as numbered steps: what happens, what is held fixed,
   what the comparator isolates, and why each design choice was made. Put the
   one or two central equations here with a one-line reading in words. Dense
   derivations and pseudocode go in a disclosure; label illustrative code as
   untested.
4. **Worked example** (`worked-example`). One small case traced with real
   numbers the reader can check by hand, in the simplest setting that still
   shows the mechanism. Where it helps, add a second calculation at realistic
   scale. State what the example teaches. This part is usually your own
   construction: say so, and never present it as the paper's result.
5. **Evidence** (`evidence`). A few results with interpretable magnitudes,
   baseline, units, direction, matched controls, and source links. Give
   counterexamples and weak spots their own heading in this section. Read the
   per-condition numbers, not only the average: an average can hide that a
   headline claim rests on one condition.
6. **Skeptical reading** (`skeptic`). Six to eight reviewer questions, each
   graded Strong, Weak or Missing with the evidence behind the grade. Cover
   variance and seeds, baseline and comparison fairness, selection or leakage,
   cost, scope, internal consistency, and causal attribution: what else could
   explain the result, and which control would separate the explanations.
7. **Check yourself** (`check`). Four to six questions that require the idea
   rather than a number lookup: explain a design choice, compute something from
   the worked example, predict a result before seeing it, design the missing
   control. Give a short model answer and set `data-review` to the section that
   answers it.
8. **Use it** (`next`). Concrete implications labeled as your inference,
   connections to related papers in the user's library (from the best-effort
   related-paper lookup; say they come from the user's notes), open questions,
   three things to remember, and a short reading path from the bibliography.
9. **Reference** (`notation`, `glossary`, `sources`). A notation table, a
   glossary, and a provenance note: what was read, which version, what each tag
   means, and which figures are original and which are yours.

Each claim has one home; later appearances add mechanism, evidence, caveat, or
implementation detail. Keep the linear read under about 25 minutes. Move depth
into disclosures rather than cutting essential caveats.

## Write for a learner

- Concrete before abstract. Give the example, then the general statement.
- Define a term or symbol where it first appears, in words a newcomer would use,
  and add it to the glossary. Use one name for each thing throughout; the
  paper's own names for methods and symbols, so the reader recognizes them in
  the paper.
- Short sentences, one idea per paragraph. Headings may state the finding.
- Keep provenance visible with the kit's tags: `Paper`, `Read from figure`,
  `Derived`, `Inference`, `Illustration`. Tag where a reader could otherwise
  mistake your reasoning for the paper's.
- Put source links next to the claims they support, as version-pinned arXiv HTML
  anchors. Descriptions of other papers taken from this paper's related-work
  section are this paper's account; say so. Bibliographic leads are not
  evidence that those papers were independently read.
- The glossary is the place for a standard 中文 term when the shared language
  policy calls for one.

## Figures, charts, and interaction

- Prefer the paper's original figures. `fetch` downloads them from the source
  HTML; embed the file unchanged, name the figure, link it, and add "how to
  read it": axes, encodings, and the one thing to notice. Never guess an arXiv
  image URL. Look at each figure before describing it, and write alt text that
  says what it shows.
- Draw your own diagram when it explains the mechanism better than prose. Use
  inline SVG with the kit's color tokens, labels at readable size, and a caption
  that marks it as yours.
- When you chart results, show baseline, units and direction on the chart, keep
  scales fixed across the states being compared, and draw the chart from the
  built ledger table so the two cannot disagree. Explain truncated axes. With
  many labeled points, prefer aligned bars in a table over a scatter.
- Add interaction only when it answers a real question: compare configurations,
  vary an explanatory parameter, or reveal a derivation. Separate illustrative
  models from measured data. Every control must update text the reader can see
  (caption, values, note), not only the graphic, and the same content must be
  available as a table without JavaScript.
- Do not build a presentation mode unless the user asks for one.

## The kit contract

`skeleton.html` shows each component once. The essentials:

- `<!-- brief:kit-css -->` and `<!-- brief:kit-js -->` are replaced by the kit.
  The frame is a section rail, a reading column, and margin notes
  (`<aside class="mn">` as a direct child of a section). Themes, the rail,
  glossary popovers, figure zoom, check-yourself state, expand-all and print are
  kit behavior; do not reimplement them.
- Math from the paper: `<div class="eq" data-eq="S2.E3"></div>` copies a display
  equation by its source id; `<span data-m="\Delta W"></span>` copies inline math
  by its LaTeX `alttext` (list them with `build_brief.py math sources/paper.html`).
  For symbols of your own use `<span class="m">…</span>`.
- `<table data-ledger-table="name">` is filled from `evidence.json`.
- `<img src="sources/…">` is embedded as a data URI. Give real alt text.
- Each `<dt id="g-…" data-match="a|b">` in `<dl class="glossary">` is linked
  automatically at its first use in every section. Mark regions that should not
  be linked with `data-no-terms`.
- `<span class="rt" data-rt="section-id">` and `<span data-rt-total>` are filled
  with reading time. Mark reference material `data-optional`.
- One file, no CDN, external font, framework, or network fetch. Public source
  links are ordinary hyperlinks. Use semantic headings, figures, tables and
  native `details`/`summary`. The substance must read without JavaScript and
  print in order with disclosures open.

## Review before delivery

Check scientific fidelity separately from visual polish.

- **Fidelity.** The build must pass with every source anchor and ledger value
  verified. Then reread the brief against the ledger for what a build cannot
  check: sign and direction, that each comparison uses its stated reference,
  values read from figures, your derived arithmetic, and that every objective
  is answered by a check question.
- **Rendering.** If Playwright is available, run
  `node skills/shared/html-brief/check_brief.cjs .artifacts/summarize-paper/<id>/summary.html`
  (set `BRIEF_PLAYWRIGHT` and `BRIEF_CHROMIUM` if it cannot find them). It
  exercises the kit, records `browser-check.json`, and saves screenshots under
  `screens/`. Otherwise render the file with your own browser tool. Either way,
  look at the screenshots: every section, each figure and control, the dark
  theme, and a narrow layout. Check for clipping, overlapping margin notes,
  unreadable diagram labels, and controls that change nothing.

Return a direct link to the HTML, a short paper takeaway, what was verified
(anchors, ledger values, browser checks), any material remaining limitation, and
the reusable prompt path if it was changed. Do not reproduce the brief in chat.
