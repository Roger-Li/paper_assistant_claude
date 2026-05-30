"""Configuration management for Paper Assistant."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


DEFAULT_DATA_DIR = Path.home() / ".paper-assistant"


class Config(BaseModel):
    """Application configuration."""

    anthropic_api_key: str | None = None
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
    notion_sync_enabled: bool = False
    notion_token: str | None = None
    notion_database_id: str | None = None
    notion_archive_on_delete: bool = True
    notion_upload_images: bool = True
    qmd_enabled: bool = False
    qmd_command: list[str] = ["qmd"]
    qmd_index_name: str = "paper-assistant"
    qmd_collection_name: str = "papers"

    # TTS backend + MLX settings
    tts_backend: str = "mlx"  # "mlx" | "edge"
    mlx_tts_url: str = "http://127.0.0.1:8000"
    mlx_tts_model: str = "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
    mlx_tts_voice: str | None = "ryan"
    mlx_tts_speaker: str | None = None
    mlx_tts_api_key: str | None = None
    mlx_tts_timeout_s: float = 120.0
    mlx_tts_chunk_chars: int = 2000
    mlx_tts_max_input_chars: int = 6000
    mlx_tts_speed: float = 1.0
    tts_edge_fallback: bool = True

    # Audio narration script (derived transcript) settings
    audio_script_model: str = "claude-haiku-4-5-20251001"

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def pdfs_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def images_dir(self) -> Path:
        """Figure images extracted from papers, served by the web UI at /images
        and uploaded to Notion as native file-upload image blocks."""
        return self.data_dir / "images"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"

    @property
    def search_dir(self) -> Path:
        return self.data_dir / "search"

    @property
    def feed_path(self) -> Path:
        return self.data_dir / "feed.xml"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        for d in [
            self.papers_dir,
            self.audio_dir,
            self.transcripts_dir,
            self.pdfs_dir,
            self.images_dir,
        ]:
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

    # API key (optional at load time; validated lazily at point of use)
    api_key = overrides.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY")
    if api_key:
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

    # Notion sync
    notion_sync_enabled = os.getenv("PAPER_ASSIST_NOTION_SYNC_ENABLED")
    if notion_sync_enabled is not None:
        kwargs["notion_sync_enabled"] = notion_sync_enabled.lower() in ("true", "1", "yes")

    notion_token = os.getenv("PAPER_ASSIST_NOTION_TOKEN")
    if notion_token:
        kwargs["notion_token"] = notion_token

    notion_database_id = os.getenv("PAPER_ASSIST_NOTION_DATABASE_ID")
    if notion_database_id:
        kwargs["notion_database_id"] = notion_database_id

    notion_archive_on_delete = os.getenv("PAPER_ASSIST_NOTION_ARCHIVE_ON_DELETE")
    if notion_archive_on_delete is not None:
        kwargs["notion_archive_on_delete"] = notion_archive_on_delete.lower() in (
            "true",
            "1",
            "yes",
        )

    notion_upload_images = os.getenv("PAPER_ASSIST_NOTION_UPLOAD_IMAGES")
    if notion_upload_images is not None:
        kwargs["notion_upload_images"] = notion_upload_images.lower() in (
            "true",
            "1",
            "yes",
        )

    # qmd search
    qmd_enabled = os.getenv("PAPER_ASSIST_QMD_ENABLED")
    if qmd_enabled is not None:
        kwargs["qmd_enabled"] = qmd_enabled.lower() in ("true", "1", "yes")

    qmd_command = os.getenv("PAPER_ASSIST_QMD_COMMAND")
    if qmd_command:
        kwargs["qmd_command"] = shlex.split(qmd_command)

    qmd_index_name = os.getenv("PAPER_ASSIST_QMD_INDEX")
    if qmd_index_name:
        kwargs["qmd_index_name"] = qmd_index_name

    qmd_collection_name = os.getenv("PAPER_ASSIST_QMD_COLLECTION")
    if qmd_collection_name:
        kwargs["qmd_collection_name"] = qmd_collection_name

    # TTS backend selection + MLX settings
    tts_backend = os.getenv("PAPER_ASSIST_TTS_BACKEND")
    if tts_backend:
        kwargs["tts_backend"] = tts_backend.strip().lower()

    mlx_tts_url = os.getenv("PAPER_ASSIST_MLX_TTS_URL")
    if mlx_tts_url:
        kwargs["mlx_tts_url"] = mlx_tts_url.rstrip("/")

    mlx_tts_model = os.getenv("PAPER_ASSIST_MLX_TTS_MODEL")
    if mlx_tts_model:
        kwargs["mlx_tts_model"] = mlx_tts_model

    mlx_tts_voice = os.getenv("PAPER_ASSIST_MLX_TTS_VOICE")
    if mlx_tts_voice:
        kwargs["mlx_tts_voice"] = mlx_tts_voice

    mlx_tts_speaker = os.getenv("PAPER_ASSIST_MLX_TTS_SPEAKER")
    if mlx_tts_speaker:
        kwargs["mlx_tts_speaker"] = mlx_tts_speaker

    mlx_tts_api_key = os.getenv("PAPER_ASSIST_MLX_TTS_API_KEY")
    if mlx_tts_api_key:
        kwargs["mlx_tts_api_key"] = mlx_tts_api_key

    mlx_tts_timeout_s = os.getenv("PAPER_ASSIST_MLX_TTS_TIMEOUT")
    if mlx_tts_timeout_s is not None:
        kwargs["mlx_tts_timeout_s"] = float(mlx_tts_timeout_s)

    mlx_tts_chunk_chars = os.getenv("PAPER_ASSIST_MLX_TTS_CHUNK_CHARS")
    if mlx_tts_chunk_chars is not None:
        kwargs["mlx_tts_chunk_chars"] = int(mlx_tts_chunk_chars)

    mlx_tts_max_input_chars = os.getenv("PAPER_ASSIST_MLX_TTS_MAX_INPUT_CHARS")
    if mlx_tts_max_input_chars is not None:
        kwargs["mlx_tts_max_input_chars"] = int(mlx_tts_max_input_chars)

    mlx_tts_speed = os.getenv("PAPER_ASSIST_MLX_TTS_SPEED")
    if mlx_tts_speed is not None:
        kwargs["mlx_tts_speed"] = float(mlx_tts_speed)

    tts_edge_fallback = os.getenv("PAPER_ASSIST_TTS_EDGE_FALLBACK")
    if tts_edge_fallback is not None:
        kwargs["tts_edge_fallback"] = tts_edge_fallback.lower() in ("true", "1", "yes")

    audio_script_model = os.getenv("PAPER_ASSIST_AUDIO_SCRIPT_MODEL")
    if audio_script_model:
        kwargs["audio_script_model"] = audio_script_model

    return Config(**kwargs)
