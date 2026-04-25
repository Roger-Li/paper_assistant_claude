"""Tests for paper_assistant.tts text preparation and backend factory."""

from paper_assistant.config import Config, load_config
from paper_assistant.tts import (
    EdgeTTSBackend,
    MlxTTSBackend,
    get_tts_backend,
    prepare_script_for_tts,
    prepare_text_for_tts,
    split_into_chunks,
)


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

    def test_intro_without_authors(self):
        text = prepare_text_for_tts("Content", "Local Note", [], source_label="note")
        assert text.startswith("This is a summary of the note: Local Note.")
        assert ", by" not in text

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

    def test_image_markdown_dropped(self):
        md = "Before paragraph.\n\n![Figure 1: caption](https://arxiv.org/html/x/x1.png)\n\nAfter paragraph."
        text = prepare_text_for_tts(md, "Title", ["A"])
        # URL must never be spoken; alt text must not leak either.
        assert "https://arxiv.org/html/x/x1.png" not in text
        assert "![" not in text
        assert "Figure 1: caption" not in text
        assert "Before paragraph." in text
        assert "After paragraph." in text

    def test_image_markdown_with_brackets_in_alt_dropped(self):
        # ML paper captions routinely include bracketed tokens like [CLS]
        # or citation markers. The image stripper must still match.
        md = "Before.\n\n![Figure 1: [CLS] token attention.](https://arxiv.org/html/x/x1.png)\n\nAfter."
        text = prepare_text_for_tts(md, "Title", ["A"])
        assert "https://arxiv.org/html/x/x1.png" not in text
        assert "![" not in text
        assert "[CLS]" not in text
        assert "Figure 1:" not in text
        assert "Before." in text
        assert "After." in text

class TestPrepareScriptForTts:
    def test_no_intro_prepended(self):
        script = "Today we're looking at VisualPRM. The authors tackle reward modeling."
        out = prepare_script_for_tts(script)
        assert out.startswith("Today we're looking at VisualPRM.")
        assert "summary of the paper" not in out

    def test_script_untouched_when_clean(self):
        script = "This is a clean narration paragraph. No markdown at all."
        assert prepare_script_for_tts(script) == script

    def test_markdown_markers_stripped(self):
        script = "# Header\nWe cover **bold** ideas and `code` inline."
        out = prepare_script_for_tts(script)
        assert "**" not in out
        assert "#" not in out
        assert "`" not in out
        assert "bold" in out
        assert "code" in out

    def test_display_math_not_labeled_omitted(self):
        # prepare_script_for_tts uses replace_equations=False — the script
        # already paraphrases math, so just drop display blocks silently.
        script = "We define $$E = mc^2$$ and discuss its meaning."
        out = prepare_script_for_tts(script)
        assert "$$" not in out
        assert "(equation omitted)" not in out


class TestSplitIntoChunks:
    def test_single_sentence_below_limit(self):
        chunks = split_into_chunks("One sentence here.", 100)
        assert chunks == ["One sentence here."]

    def test_splits_on_sentence_boundaries(self):
        text = "First sentence. Second sentence. Third sentence."
        chunks = split_into_chunks(text, 25)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 25

    def test_oversized_single_sentence_hard_split(self):
        word = "a" * 60
        chunks = split_into_chunks(word, 20)
        assert all(len(c) <= 20 for c in chunks)
        assert "".join(chunks) == word


class TestGetTtsBackend:
    def test_returns_mlx_by_default(self, tmp_path):
        config = Config(anthropic_api_key="k", data_dir=tmp_path)
        backend = get_tts_backend(config)
        assert isinstance(backend, MlxTTSBackend)
        assert backend.name == "mlx"

    def test_returns_edge_when_configured(self, tmp_path):
        config = Config(anthropic_api_key="k", data_dir=tmp_path, tts_backend="edge")
        backend = get_tts_backend(config)
        assert isinstance(backend, EdgeTTSBackend)
        assert backend.name == "edge"

    def test_mlx_backend_uses_config_values(self, tmp_path):
        config = Config(
            anthropic_api_key="k",
            data_dir=tmp_path,
            mlx_tts_url="http://example.com:9000",
            mlx_tts_model="TestModel",
            mlx_tts_voice="alloy",
            mlx_tts_speaker="Ryan",
            mlx_tts_chunk_chars=1234,
            mlx_tts_max_input_chars=5678,
        )
        backend = get_tts_backend(config)
        assert isinstance(backend, MlxTTSBackend)
        assert backend.url == "http://example.com:9000"
        assert backend.model == "TestModel"
        assert backend.voice == "alloy"
        assert backend.speaker == "Ryan"
        assert backend.chunk_chars == 1234
        assert backend.max_input_chars == 5678
        assert backend.endpoint == "http://example.com:9000/v1/audio/speech"

    def test_load_config_reads_mlx_speaker_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PAPER_ASSIST_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("PAPER_ASSIST_MLX_TTS_SPEAKER", "Ryan")

        config = load_config()

        assert config.mlx_tts_speaker == "Ryan"


class TestPrepareTextForTtsFullMarkdown:
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
