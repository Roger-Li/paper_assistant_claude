"""Configuration management for Paper Assistant."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


DEFAULT_DATA_DIR = Path.home() / ".paper-assistant"


class Config(BaseModel):
    """Application configuration."""

    anthropic_api_key: str
    data_dir: Path = DEFAULT_DATA_DIR
    claude_model: str = "claude-sonnet-4-20250514"
    tts_voice: str = "en-US-AriaNeural"
    tts_rate: str = "+0%"
    web_host: str = "127.0.0.1"
    web_port: int = 8877
    podcast_title: str = "Paper Assistant - ML Paper Summaries"
    podcast_base_url: str = "http://127.0.0.1:8877"
    max_pdf_pages: int = 100
    cache_pdfs: bool = True
    icloud_sync: bool = True
    icloud_dir: Path = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Paper Assistant"
    arxiv_user_agent: str = (
        "paper-assistant/0.1 (+https://arxiv.org/help/api/user-manual; "
        "set PAPER_ASSIST_ARXIV_USER_AGENT with contact email)"
    )
    arxiv_max_retries: int = 6
    arxiv_backoff_base_seconds: float = 2.0
    arxiv_backoff_cap_seconds: float = 90.0

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def pdfs_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"

    @property
    def feed_path(self) -> Path:
        return self.data_dir / "feed.xml"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        for d in [self.papers_dir, self.audio_dir, self.pdfs_dir]:
            d.mkdir(parents=True, exist_ok=True)


def load_config(**overrides: object) -> Config:
    """Load config from environment variables, .env file, and overrides.

    Resolution order (highest priority first):
    1. Explicit overrides (CLI flags)
    2. Environment variables
    3. .env file
    4. Defaults
    """
    # Load .env from CWD or home
    load_dotenv()
    load_dotenv(Path.home() / ".paper-assistant" / ".env")

    kwargs: dict[str, object] = {}

    # API key (required)
    api_key = overrides.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required. Set it in .env or as an environment variable."
        )
    kwargs["anthropic_api_key"] = api_key

    # Data directory
    data_dir = overrides.get("data_dir") or os.getenv("PAPER_ASSIST_DATA_DIR")
    if data_dir:
        kwargs["data_dir"] = Path(data_dir)

    # Model
    model = overrides.get("model") or os.getenv("PAPER_ASSIST_MODEL")
    if model:
        kwargs["claude_model"] = model

    # TTS voice
    voice = overrides.get("tts_voice") or os.getenv("PAPER_ASSIST_TTS_VOICE")
    if voice:
        kwargs["tts_voice"] = voice

    # iCloud sync
    icloud_env = os.getenv("PAPER_ASSIST_ICLOUD_SYNC")
    if icloud_env is not None:
        kwargs["icloud_sync"] = icloud_env.lower() in ("true", "1", "yes")

    icloud_dir = os.getenv("PAPER_ASSIST_ICLOUD_DIR")
    if icloud_dir:
        kwargs["icloud_dir"] = Path(icloud_dir)

    # arXiv request policy
    arxiv_user_agent = os.getenv("PAPER_ASSIST_ARXIV_USER_AGENT")
    if arxiv_user_agent:
        kwargs["arxiv_user_agent"] = arxiv_user_agent

    arxiv_max_retries = os.getenv("PAPER_ASSIST_ARXIV_MAX_RETRIES")
    if arxiv_max_retries is not None:
        kwargs["arxiv_max_retries"] = int(arxiv_max_retries)

    arxiv_backoff_base_seconds = os.getenv("PAPER_ASSIST_ARXIV_BACKOFF_BASE_SECONDS")
    if arxiv_backoff_base_seconds is not None:
        kwargs["arxiv_backoff_base_seconds"] = float(arxiv_backoff_base_seconds)

    arxiv_backoff_cap_seconds = os.getenv("PAPER_ASSIST_ARXIV_BACKOFF_CAP_SECONDS")
    if arxiv_backoff_cap_seconds is not None:
        kwargs["arxiv_backoff_cap_seconds"] = float(arxiv_backoff_cap_seconds)

    return Config(**kwargs)
