"""Tests for paper_assistant.summarizer parsing functions."""

from paper_assistant.models import PaperMetadata, SourceType
from paper_assistant.summarizer import (
    SummarizationResult,
    find_one_pager,
    format_summary_file,
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
