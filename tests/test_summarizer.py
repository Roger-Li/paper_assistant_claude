"""Tests for paper_assistant.summarizer parsing functions."""

from paper_assistant.models import PaperMetadata, SourceType
from paper_assistant.summarizer import (
    SummarizationResult,
    find_one_pager,
    format_summary_file,
    normalize_summary_body,
    parse_summary_sections,
)


class TestParseSummarySections:
    def test_single_section(self):
        md = "# Introduction\nThis is the intro."
        sections = parse_summary_sections(md)
        assert "Introduction" in sections
        assert sections["Introduction"] == "This is the intro."

    def test_multiple_sections(self):
        md = "# Section A\nContent A\n# Section B\nContent B"
        sections = parse_summary_sections(md)
        assert len(sections) == 2
        assert sections["Section A"] == "Content A"
        assert sections["Section B"] == "Content B"

    def test_subsections_not_split(self):
        md = "# Main\nSome text\n## Sub\nMore text"
        sections = parse_summary_sections(md)
        assert len(sections) == 1
        assert "## Sub" in sections["Main"]

    def test_empty_input(self):
        assert parse_summary_sections("") == {}

    def test_no_headers(self):
        assert parse_summary_sections("Just plain text\nNo headers") == {}

    def test_multiline_content(self):
        md = "# Header\nLine 1\nLine 2\nLine 3"
        sections = parse_summary_sections(md)
        assert sections["Header"] == "Line 1\nLine 2\nLine 3"

    def test_numbered_header(self):
        md = "# 1. One-Pager Summary\nContent here"
        sections = parse_summary_sections(md)
        assert "1. One-Pager Summary" in sections

    def test_parse_custom_instruction_sections(self):
        md = """# One-Pager
Alpha
# Deep-Structure Map
Beta
# Critical Q&A
Gamma
# My-Level Adaptation
Delta
# Reading List
Epsilon
"""
        sections = parse_summary_sections(md)
        assert list(sections) == [
            "One-Pager",
            "Deep-Structure Map",
            "Critical Q&A",
            "My-Level Adaptation",
            "Reading List",
        ]

    def test_parse_prompt_style_h2_sections_under_title(self):
        md = """# Paper Title

## One-Pager
Alpha

## Deep-Structure Map
### Problem
Beta

## Critical Q&A
Gamma
"""
        sections = parse_summary_sections(md)
        assert list(sections) == [
            "One-Pager",
            "Deep-Structure Map",
            "Critical Q&A",
        ]
        assert sections["One-Pager"] == "Alpha"
        assert "### Problem" in sections["Deep-Structure Map"]

    def test_legacy_h1_sections_do_not_split_h2_subsections(self):
        md = """# One-Pager
Alpha

# Deep-Structure Map
## Problem
Beta
"""
        sections = parse_summary_sections(md)
        assert list(sections) == ["One-Pager", "Deep-Structure Map"]
        assert "## Problem" in sections["Deep-Structure Map"]


class TestFindOnePager:
    def test_exact_match(self):
        sections = {"One-Pager Summary": "Summary content"}
        assert find_one_pager(sections) == "Summary content"

    def test_short_name(self):
        sections = {"One-Pager": "Content here"}
        assert find_one_pager(sections) == "Content here"

    def test_find_one_pager_matches_custom_header(self):
        sections = {"One-Pager": "Custom content"}
        assert find_one_pager(sections) == "Custom content"

    def test_numbered_header(self):
        sections = {"1. One-Pager Summary": "Numbered content"}
        assert find_one_pager(sections) == "Numbered content"

    def test_case_insensitive(self):
        sections = {"ONE-PAGER SUMMARY": "Upper case"}
        assert find_one_pager(sections) == "Upper case"

    def test_fallback_to_first_section(self):
        sections = {"Introduction": "Intro", "Method": "Method"}
        result = find_one_pager(sections)
        assert result == "Intro"

    def test_empty_sections(self):
        assert find_one_pager({}) == ""

    def test_prefers_one_pager_over_others(self):
        sections = {
            "Introduction": "Intro",
            "One-Pager": "The one pager",
            "Conclusion": "Done",
        }
        assert find_one_pager(sections) == "The one pager"


class TestFormatSummaryFile:
    def test_note_omits_empty_author_and_source_lines(self):
        metadata = PaperMetadata(
            source_type=SourceType.NOTE,
            source_slug="local-note",
            title="Local Note",
            authors=[],
        )
        summary = SummarizationResult(full_markdown="Body text", one_pager="", sections={})

        formatted = format_summary_file(metadata, summary)

        assert "source_type: note" in formatted
        assert "source_slug: local-note" in formatted
        assert "**Authors**" not in formatted
        assert "**Source**" not in formatted


class TestNormalizeSummaryBody:
    def test_strips_yaml_and_title_header(self):
        metadata = PaperMetadata(arxiv_id="2503.10291", title="Test Paper", authors=["Alice"])
        body = "# One-Pager\nMain content here."
        summary = SummarizationResult(full_markdown=body, one_pager=body, sections={})
        formatted = format_summary_file(metadata, summary)

        cleaned = normalize_summary_body(formatted)

        assert "---" not in cleaned.splitlines()[:3]
        assert cleaned.lstrip().startswith("# One-Pager")
        assert "Main content here." in cleaned

    def test_body_without_frontmatter_untouched(self):
        body = "# One-Pager\nJust a body."
        assert normalize_summary_body(body).strip() == body.strip()

    def test_strips_only_yaml_when_no_title_header(self):
        raw = "---\npaper_id: x\n---\n\n# One-Pager\nBody"
        cleaned = normalize_summary_body(raw)
        assert cleaned.startswith("# One-Pager")

    def test_does_not_strip_late_horizontal_rule(self):
        # A fresh body's own \n---\n (no front matter) must never be stripped —
        # it's a real section rule, regardless of position.
        padding = "Line of prose. " * 40  # > 400 chars
        raw = f"# One-Pager\n{padding}\n---\nTrailing section"
        cleaned = normalize_summary_body(raw)
        assert "Trailing section" in cleaned
        assert "---" in cleaned

    def test_strips_title_header_when_author_line_exceeds_400_chars(self):
        # Regression: with many authors the metadata header's closing ``---``
        # rule sits well past 400 chars. It must still be stripped — the old
        # fixed-window bound left the duplicated title/metadata header in the
        # narration/edit body for papers like the 25-author Agentic-RL survey.
        authors = [f"Firstname{i} Lastname{i}" for i in range(30)]
        authors_line = f"**Authors**: {', '.join(authors)}"
        assert len(authors_line) > 400  # ensure we exercise the >400 path
        metadata = PaperMetadata(
            arxiv_id="2509.02547", title="A Long Survey", authors=authors
        )
        body = "# One-Pager\nReal body content."
        summary = SummarizationResult(full_markdown=body, one_pager=body, sections={})
        formatted = format_summary_file(metadata, summary)

        cleaned = normalize_summary_body(formatted)

        assert cleaned.lstrip().startswith("# One-Pager")
        assert "**Authors**" not in cleaned
        assert "**arXiv**" not in cleaned
        assert "A Long Survey" not in cleaned  # the header title is gone

    def test_preserves_wrapperless_yaml_body_with_late_divider(self):
        # YAML front matter but NO generated title/metadata header, plus a real
        # ``---`` section rule later in the body (e.g. a hand-edited or legacy
        # file). The divider must NOT be treated as a header terminator — the
        # earlier content must survive (the strip checks header shape, not just
        # front-matter presence).
        intro = "Real intro paragraph. " * 20  # > 400 chars
        raw = f"---\npaper_id: x\n---\n# Background\n{intro}\n---\n## Methods\nDetails."
        cleaned = normalize_summary_body(raw)
        assert "# Background" in cleaned
        assert "Real intro paragraph." in cleaned
        assert "## Methods" in cleaned
