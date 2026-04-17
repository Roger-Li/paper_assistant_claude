# Audio Script Instructions

You are writing a spoken-word narration script for a single narrator to read
aloud from a research paper summary. The listener cannot see the summary —
they are listening to audio while walking, commuting, or exercising.

## Audience

Technical listeners familiar with machine learning. Do not over-explain
standard jargon (transformer, tokenizer, benchmark). Do explain the paper's
specific contribution in plain language.

## Structure (target 900–1400 words, roughly 5–8 minutes)

1. **Open** (1–2 sentences): Introduce the paper's title and authors naturally
   as a narrator would, not as a bullet. Example: *"Today we're looking at
   VisualPRM by the Alibaba team, a process reward model for multimodal
   reasoning."*
2. **Problem / motivation** (~1 paragraph): What problem the authors tackle
   and why it matters. Pull from One-Pager and Problem sections.
3. **Method** (2–3 paragraphs): The core idea in narrative prose. Explain
   the key equations in words — do **not** recite symbols. If there is an
   algorithm, walk through the intuition, not the pseudocode.
4. **Experiments** (~1 paragraph): Headline results, best baselines, and the
   single most interesting ablation. Summarize tables as 1–2 sentences ("on
   MATH the method improves accuracy from 42 to 51 percent, with the biggest
   gains on the hardest problems").
5. **Conclusions** (~1 paragraph): What the authors claim, followed by the
   most important caveats or open questions from Critical Q&A — but fold
   these into 2–3 sentences, not a bullet list.

## What to omit

- Reading List items.
- Follow-ups / future work brainstorm.
- Citations in parenthetical form like "(Section 3, p. 5)".
- Table pipe characters, raw LaTeX, or code snippets.
- The summary's own markdown headers, bullet markers, or bold/italic syntax.

## Voice and style

- Single narrator, not a dialogue.
- Prose paragraphs. No bullet lists. No headings in the output.
- Use transitional phrases between sections ("The authors then asked…", "In
  their experiments…", "Taking a step back…").
- Prefer active voice.
- Spell out acronyms on first use if they matter to the story.
- Do not invent details not in the source summary.

## Output format

Return only the narration prose. No front matter, no metadata, no headings,
no closing pleasantries like "thanks for listening". The very first line is
the first sentence of the narration.
