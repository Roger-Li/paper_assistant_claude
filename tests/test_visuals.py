"""Tests for paper_assistant.visuals: visual extraction and injection."""

from __future__ import annotations

import textwrap

from paper_assistant.visuals import (
    VisualCandidate,
    enrich_summary_with_visuals,
    extract_visual_candidates,
    inject_visuals,
)


# ---------------------------------------------------------------------------
# Fixture markdown — a compact arXiv-HTML-style snippet.
# ---------------------------------------------------------------------------


_SAMPLE_MARKDOWN = textwrap.dedent(
    """\
    Title: Sample Paper

    URL Source: https://arxiv.org/html/2412.04468

    Markdown Content:

    ###### Abstract

    Visual language models matter.

    ![Image 1: [Uncaptioned image]](https://arxiv.org/html/2412.04468v2/x1.png)

    Figure 1: NVILA – Efficient Frontier VLMs. (a) NVILA trains image and video models faster.

    ![Image 2: Refer to caption](https://arxiv.org/html/2412.04468v2/x2.png)

    ![Image 3: Refer to caption](https://arxiv.org/html/2412.04468v2/x3.png)

    Figure 2: Qualitative examples.

    ![Image 4: Refer to caption](https://arxiv.org/html/2412.04468v2/x5.png)

    Figure 3: Model architecture.

    ![Image 5: Refer to caption](https://example.com/not-arxiv/x6.png)

    Figure 4: External image ignored.

    ![Image 6: Refer to caption](https://arxiv.org/html/2412.04468v2/x7.png)

    Some unrelated paragraph with no caption follows.

    Table 1: Spatial scale-then-compress.

    Some textual table content here.
    """
)


# ---------------------------------------------------------------------------
# extract_visual_candidates
# ---------------------------------------------------------------------------


class TestExtractVisualCandidates:
    def test_finds_canonical_figures(self):
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        labels = [(c.kind, c.number) for c in candidates]
        assert ("figure", 1) in labels
        assert ("figure", 2) in labels
        assert ("figure", 3) in labels

    def test_multi_panel_takes_first_image(self):
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        figure2 = next(c for c in candidates if (c.kind, c.number) == ("figure", 2))
        # Figure 2 has 3 images; the lead (x2.png) is the canonical visual.
        assert figure2.image_url == "https://arxiv.org/html/2412.04468v2/x2.png"

    def test_caption_text_preserved(self):
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        figure1 = next(c for c in candidates if (c.kind, c.number) == ("figure", 1))
        assert figure1.caption.startswith("NVILA – Efficient Frontier VLMs.")

    def test_short_caption_is_first_sentence(self):
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        figure1 = next(c for c in candidates if (c.kind, c.number) == ("figure", 1))
        assert figure1.short_caption == "NVILA – Efficient Frontier VLMs."

    def test_external_image_skipped(self):
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        labels = [(c.kind, c.number) for c in candidates]
        # Figure 4 references an external (non-arxiv) URL — must be skipped.
        assert ("figure", 4) not in labels

    def test_caption_without_pending_image_skipped(self):
        # Table 1 caption appears with no preceding image markdown.
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        labels = [(c.kind, c.number) for c in candidates]
        assert ("table", 1) not in labels

    def test_orphan_image_with_no_caption_skipped(self):
        # Image x7.png has no caption header following it — must not register.
        candidates = extract_visual_candidates(_SAMPLE_MARKDOWN)
        urls = [c.image_url for c in candidates]
        assert "https://arxiv.org/html/2412.04468v2/x7.png" not in urls

    def test_empty_markdown_returns_empty(self):
        assert extract_visual_candidates("") == []
        assert extract_visual_candidates("\n\n") == []

    def test_to_markdown_produces_alt_with_label_and_caption(self):
        cand = VisualCandidate(
            kind="figure",
            number=2,
            image_url="https://arxiv.org/html/x/x2.png",
            caption="Qualitative examples.",
        )
        assert cand.to_markdown() == (
            "![Figure 2: Qualitative examples.](https://arxiv.org/html/x/x2.png)"
        )

    def test_to_markdown_falls_back_to_label_when_no_caption(self):
        cand = VisualCandidate(
            kind="figure",
            number=2,
            image_url="https://arxiv.org/html/x/x2.png",
            caption="",
        )
        assert cand.to_markdown() == "![Figure 2](https://arxiv.org/html/x/x2.png)"


# ---------------------------------------------------------------------------
# inject_visuals
# ---------------------------------------------------------------------------


def _candidate(number: int, kind: str = "figure", url: str | None = None) -> VisualCandidate:
    return VisualCandidate(
        kind=kind,
        number=number,
        image_url=url or f"https://arxiv.org/html/x/x{number}.png",
        caption=f"caption {number}.",
    )


class TestInjectVisuals:
    def test_inserts_image_after_first_reference_block(self):
        summary = textwrap.dedent(
            """\
            ## Section

            We discuss Figure 1 here.

            And another paragraph.
            """
        )
        candidates = [_candidate(1)]
        out = inject_visuals(summary, candidates)
        # Image markdown is inserted as its own block, right after the
        # paragraph that mentions Figure 1.
        assert "![Figure 1: caption 1.](https://arxiv.org/html/x/x1.png)" in out
        before, image_block, _rest = out.partition(
            "![Figure 1: caption 1.](https://arxiv.org/html/x/x1.png)"
        )
        assert "We discuss Figure 1 here." in before
        assert image_block

    def test_caps_at_max_visuals(self):
        summary = textwrap.dedent(
            """\
            We mention Figure 1.

            We mention Figure 2.

            We mention Figure 3.

            We mention Figure 4.
            """
        )
        candidates = [_candidate(i) for i in range(1, 5)]
        out = inject_visuals(summary, candidates, max_visuals=3)
        assert out.count("](https://arxiv.org/html/x/") == 3
        assert "x4.png" not in out

    def test_idempotent_when_image_already_present(self):
        summary = textwrap.dedent(
            """\
            See Figure 1.

            ![Figure 1: caption 1.](https://arxiv.org/html/x/x1.png)
            """
        )
        candidates = [_candidate(1)]
        out = inject_visuals(summary, candidates)
        # Image URL must appear exactly once after a no-op injection.
        assert out.count("https://arxiv.org/html/x/x1.png") == 1

    def test_only_first_reference_per_figure_triggers_injection(self):
        summary = textwrap.dedent(
            """\
            Figure 1 first mention.

            Figure 1 second mention.
            """
        )
        candidates = [_candidate(1)]
        out = inject_visuals(summary, candidates)
        assert out.count("https://arxiv.org/html/x/x1.png") == 1

    def test_unreferenced_candidates_are_not_injected(self):
        summary = "We talk only about Figure 1 in this short summary."
        candidates = [_candidate(1), _candidate(2), _candidate(3)]
        out = inject_visuals(summary, candidates)
        assert "x1.png" in out
        assert "x2.png" not in out
        assert "x3.png" not in out

    def test_reference_inside_code_block_is_ignored(self):
        summary = textwrap.dedent(
            """\
            Some prose without references.

            ```python
            # mentions Figure 1 in a comment
            print("hi")
            ```

            Trailing prose.
            """
        )
        candidates = [_candidate(1)]
        out = inject_visuals(summary, candidates)
        assert "x1.png" not in out

    def test_table_reference_resolved(self):
        summary = "Look at Table 2 for the numbers."
        candidates = [_candidate(2, kind="table")]
        out = inject_visuals(summary, candidates)
        assert "![Table 2: caption 2.](https://arxiv.org/html/x/x2.png)" in out

    def test_no_op_when_no_candidates(self):
        summary = "Reference Figure 1 here."
        assert inject_visuals(summary, []) == summary

    def test_no_op_for_empty_summary(self):
        candidates = [_candidate(1)]
        assert inject_visuals("", candidates) == ""
        assert inject_visuals("   ", candidates) == "   "


# ---------------------------------------------------------------------------
# enrich_summary_with_visuals
# ---------------------------------------------------------------------------


class TestEnrichSummaryWithVisuals:
    def test_extracts_then_injects(self):
        summary = textwrap.dedent(
            """\
            ## One-Pager

            The paper's Figure 1 shows the architecture.
            """
        )
        out = enrich_summary_with_visuals(
            full_markdown=summary,
            source_markdown=_SAMPLE_MARKDOWN,
        )
        assert "https://arxiv.org/html/2412.04468v2/x1.png" in out

    def test_returns_summary_unchanged_when_no_source(self):
        summary = "Reference Figure 1 here."
        assert enrich_summary_with_visuals(
            full_markdown=summary,
            source_markdown=None,
        ) == summary

    def test_returns_summary_unchanged_when_no_candidates(self):
        summary = "Reference Figure 1 here."
        out = enrich_summary_with_visuals(
            full_markdown=summary,
            source_markdown="No images, no captions, just prose.",
        )
        assert out == summary
