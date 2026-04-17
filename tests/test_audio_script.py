"""Tests for paper_assistant.audio_script.generate_audio_script."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paper_assistant.audio_script import (
    AudioScriptError,
    generate_audio_script,
)
from paper_assistant.config import Config
from paper_assistant.models import PaperMetadata


def _metadata() -> PaperMetadata:
    return PaperMetadata(
        arxiv_id="2503.10291",
        title="Test Paper",
        authors=["Alice", "Bob"],
        abstract="Abstract goes here.",
    )


@pytest.mark.asyncio
async def test_generate_audio_script_returns_text(tmp_path):
    config = Config(anthropic_api_key="key", data_dir=tmp_path)

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(text="Narration script body here.")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=200),
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    with patch(
        "paper_assistant.audio_script.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        result = await generate_audio_script(
            markdown="# One-Pager\nBody content",
            metadata=_metadata(),
            config=config,
        )

    assert result.script_markdown == "Narration script body here."
    assert result.model_used == config.audio_script_model
    assert result.input_tokens == 100
    assert result.output_tokens == 200

    call_kwargs = fake_client.messages.create.await_args.kwargs
    assert call_kwargs["model"] == config.audio_script_model
    user_content = call_kwargs["messages"][0]["content"]
    assert "Title: Test Paper" in user_content
    assert "Authors: Alice, Bob" in user_content
    assert "Body content" in user_content


@pytest.mark.asyncio
async def test_generate_audio_script_uses_override_model(tmp_path):
    config = Config(anthropic_api_key="key", data_dir=tmp_path)

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(text="x")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    with patch(
        "paper_assistant.audio_script.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        result = await generate_audio_script(
            markdown="# One-Pager\nBody",
            metadata=_metadata(),
            config=config,
            model="claude-haiku-alt",
        )

    assert result.model_used == "claude-haiku-alt"
    assert fake_client.messages.create.await_args.kwargs["model"] == "claude-haiku-alt"


@pytest.mark.asyncio
async def test_generate_audio_script_missing_api_key_raises(tmp_path):
    config = Config(anthropic_api_key=None, data_dir=tmp_path)

    with pytest.raises(AudioScriptError, match="ANTHROPIC_API_KEY"):
        await generate_audio_script(
            markdown="# One-Pager\nBody",
            metadata=_metadata(),
            config=config,
        )


@pytest.mark.asyncio
async def test_generate_audio_script_empty_markdown_raises(tmp_path):
    config = Config(anthropic_api_key="key", data_dir=tmp_path)

    with pytest.raises(AudioScriptError, match="empty"):
        await generate_audio_script(
            markdown="   ",
            metadata=_metadata(),
            config=config,
        )


@pytest.mark.asyncio
async def test_generate_audio_script_api_error_wraps(tmp_path):
    import anthropic

    config = Config(anthropic_api_key="key", data_dir=tmp_path)

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        side_effect=anthropic.APIError(
            "rate limited", request=MagicMock(), body=None
        )
    )

    with patch(
        "paper_assistant.audio_script.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        with pytest.raises(AudioScriptError):
            await generate_audio_script(
                markdown="# One-Pager\nBody",
                metadata=_metadata(),
                config=config,
            )


@pytest.mark.asyncio
async def test_generate_audio_script_empty_response_raises(tmp_path):
    config = Config(anthropic_api_key="key", data_dir=tmp_path)

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(text="   ")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    with patch(
        "paper_assistant.audio_script.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        with pytest.raises(AudioScriptError, match="empty"):
            await generate_audio_script(
                markdown="# One-Pager\nBody",
                metadata=_metadata(),
                config=config,
            )
