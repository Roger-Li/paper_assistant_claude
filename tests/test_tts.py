"""Tests for paper_assistant.tts text preparation."""

from paper_assistant.tts import prepare_text_for_tts


class TestPrepareTextForTts:
    def test_intro_added(self):
        text = prepare_text_for_tts("Some content", "Paper Title", ["Alice", "Bob"])
        assert text.startswith("This is a summary of the paper: Paper Title, by Alice, Bob.")

    def test_intro_many_authors(self):
        authors = ["Alice", "Bob", "Charlie", "Dave"]
        text = prepare_text_for_tts("Content", "Title", authors)
        assert "Alice, Bob, Charlie, and others" in text

    def test_intro_three_authors(self):
        text = prepare_text_for_tts("Content", "Title", ["A", "B", "C"])
        assert "A, B, C" in text

    def test_headers_removed(self):
        text = prepare_text_for_tts("# Header\nContent", "T", ["A"])
        assert "# Header" not in text
        assert "Content" in text

    def test_bold_removed(self):
        text = prepare_text_for_tts("**bold text**", "T", ["A"])
        assert "**" not in text
        assert "bold text" in text

    def test_italic_removed(self):
        text = prepare_text_for_tts("*italic*", "T", ["A"])
        assert text.endswith("italic")

    def test_links_text_preserved(self):
        text = prepare_text_for_tts("[click here](http://example.com)", "T", ["A"])
        assert "click here" in text
        assert "http://example.com" not in text

    def test_inline_code_removed(self):
        text = prepare_text_for_tts("`some_code`", "T", ["A"])
        assert "`" not in text
        assert "some_code" in text

    def test_code_blocks_removed(self):
        text = prepare_text_for_tts("```python\nprint('hi')\n```", "T", ["A"])
        assert "```" not in text
        assert "print" not in text

    def test_latex_display_math_removed(self):
        text = prepare_text_for_tts("Before $$E=mc^2$$ After", "T", ["A"])
        assert "$$" not in text
        assert "(equation omitted)" in text

    def test_latex_inline_kept_text(self):
        text = prepare_text_for_tts("The value $x$ is", "T", ["A"])
        assert "$" not in text
        assert "x" in text

    def test_bullet_points_cleaned(self):
        text = prepare_text_for_tts("- item one\n- item two", "T", ["A"])
        assert "- item" not in text
        assert "item one" in text

    def test_excessive_newlines_collapsed(self):
        text = prepare_text_for_tts("A\n\n\n\n\nB", "T", ["A"])
        assert "\n\n\n" not in text

    def test_full_markdown_input(self):
        md = """# One-Pager Summary

This paper introduces **VisualPRM**, a novel approach to visual process reward modeling.

## Key Contributions

- First contribution here
- Second contribution with $\\alpha$ parameter

## Results

The model achieves **state-of-the-art** on [benchmark](http://example.com).

$$L = \\sum_{i} loss_i$$
"""
        text = prepare_text_for_tts(md, "VisualPRM", ["Alice", "Bob"])
        assert "This is a summary of the paper: VisualPRM" in text
        assert "**" not in text
        assert "# " not in text
        assert "$$" not in text
        assert "[benchmark]" not in text
        assert "VisualPRM" in text
        assert "First contribution here" in text
