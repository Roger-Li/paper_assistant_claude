"""Shared audio-asset helper: transcript + MP3 generation for every call site.

Every inline TTS call site in the codebase (CLI add/import/skill-import,
`POST /api/add`, web summary edit/regen, web transcript regenerate, CLI
transcript regenerate, pipeline create_local_entry) delegates audio work
through :func:`render_audio_assets` to preserve invariants 1, 3, 5, and 7.

Error policy (see plan §4.3): backends raise typed errors; this helper
converts them to warnings so summary import keeps progressing. Diagnostic
surfaces (``paper-assist tts check``) invoke the backend directly so
misconfiguration is visible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from paper_assistant.config import Config
from paper_assistant.models import Paper, ProcessingStatus
from paper_assistant.storage import StorageManager, make_audio_filename
from paper_assistant.tts import (
    EdgeTTSError,
    FfmpegMissingError,
    MlxConfigError,
    MlxTransientError,
    TTSBackendError,
    get_edge_backend,
    get_tts_backend,
    prepare_script_for_tts,
    prepare_text_for_tts,
)

logger = logging.getLogger(__name__)


@dataclass
class AudioAssetsResult:
    transcript_path: Path | None = None
    audio_path: Path | None = None
    script_model: str | None = None
    backend_used: Literal["mlx", "edge"] | None = None
    warnings: list[str] = field(default_factory=list)


async def render_audio_assets(
    *,
    config: Config,
    storage: StorageManager,
    paper: Paper,
    source_markdown: str,
    skip_transcript: bool,
    skip_audio: bool,
    provided_script_markdown: str | None = None,
    script_model_override: str | None = None,
) -> AudioAssetsResult:
    """Render transcript + audio for a paper. Never raises upward.

    See module docstring for the error contract. See plan §5 for the
    skip × force matrix (the caller controls preservation rules; this
    helper only regenerates what it's asked to).
    """
    paper_id = paper.metadata.paper_id
    result = AudioAssetsResult()

    # --- skip-audio short-circuit (also implies skip-transcript) ---
    if skip_audio:
        fresh = storage.get_paper(paper_id) or paper
        result.transcript_path = (
            config.data_dir / fresh.transcript_path if fresh.transcript_path else None
        )
        result.audio_path = (
            config.data_dir / fresh.audio_path if fresh.audio_path else None
        )
        return result

    # --- Decide the script ---
    script_markdown: str | None = None
    if provided_script_markdown is not None:
        script_markdown = provided_script_markdown.strip() or None
        if script_markdown is None:
            result.warnings.append(
                "Provided transcript was empty; falling back to raw summary."
            )
    elif not skip_transcript:
        script_markdown, script_model, script_warning = await _try_generate_script(
            config=config,
            paper=paper,
            source_markdown=source_markdown,
            model_override=script_model_override,
        )
        if script_model:
            result.script_model = script_model
        if script_warning:
            result.warnings.append(script_warning)

    if script_markdown:
        try:
            storage.save_transcript(paper_id, script_markdown)
            paper = storage.get_paper(paper_id) or paper
            result.transcript_path = config.transcripts_dir / f"{paper_id}.md"
        except Exception as exc:
            logger.warning("Failed to persist transcript for %s: %s", paper_id, exc)
            result.warnings.append(f"Failed to persist transcript: {exc}")
            script_markdown = None

    # --- Choose TTS input ---
    if script_markdown:
        tts_text = prepare_script_for_tts(script_markdown)
    else:
        tts_text = prepare_text_for_tts(
            source_markdown,
            paper.metadata.title,
            paper.metadata.authors,
            source_label=paper.metadata.source_label,
        )

    # --- Synthesize audio ---
    audio_path = config.audio_dir / make_audio_filename(paper_id)
    backend_used = await _synthesize_with_fallback(
        config=config,
        text=tts_text,
        audio_path=audio_path,
        warnings=result.warnings,
    )

    if backend_used is not None and audio_path.exists():
        try:
            paper_for_update = storage.get_paper(paper_id) or paper
            paper_for_update.audio_path = f"audio/{make_audio_filename(paper_id)}"
            paper_for_update.status = ProcessingStatus.AUDIO_GENERATED
            storage.add_paper(paper_for_update)
        except Exception as exc:
            logger.warning("Failed to record audio path for %s: %s", paper_id, exc)
            result.warnings.append(f"Failed to record audio path: {exc}")
        result.audio_path = audio_path
        result.backend_used = backend_used
    else:
        # Preserve existing audio path on failure
        fresh = storage.get_paper(paper_id) or paper
        if fresh.audio_path:
            result.audio_path = config.data_dir / fresh.audio_path

    return result


async def _try_generate_script(
    *,
    config: Config,
    paper: Paper,
    source_markdown: str,
    model_override: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Attempt to generate a narration script. Returns (script, model, warning)."""
    from paper_assistant.audio_script import AudioScriptError, generate_audio_script

    if not config.anthropic_api_key:
        return None, None, (
            "ANTHROPIC_API_KEY missing; generated audio uses raw summary "
            "(set key and run `paper-assist transcript regenerate <paper_id>` to upgrade)."
        )

    try:
        script_result = await generate_audio_script(
            markdown=source_markdown,
            metadata=paper.metadata,
            config=config,
            model=model_override,
        )
    except AudioScriptError as exc:
        return None, None, f"Transcript generation failed ({exc}); audio uses raw summary."
    except Exception as exc:
        logger.exception("Unexpected audio-script failure")
        return None, None, f"Transcript generation failed ({exc}); audio uses raw summary."

    return script_result.script_markdown, script_result.model_used, None


async def _synthesize_with_fallback(
    *,
    config: Config,
    text: str,
    audio_path: Path,
    warnings: list[str],
) -> Literal["mlx", "edge"] | None:
    """Run primary backend; optionally fall back to edge. Returns backend used or None."""
    primary = get_tts_backend(config)
    primary_name = primary.name

    try:
        await primary.synthesize(text, audio_path)
        return primary_name  # type: ignore[return-value]
    except MlxConfigError as exc:
        warnings.append(
            f"MLX TTS rejected the request ({exc}); fix config and retry."
            " Audio skipped to avoid masking the misconfiguration."
        )
        return None
    except (MlxTransientError, FfmpegMissingError) as exc:
        if primary_name == "mlx" and config.tts_edge_fallback:
            warnings.append(f"MLX TTS unavailable ({exc}); falling back to edge-tts.")
            return await _try_edge(config, text, audio_path, warnings)
        warnings.append(f"MLX TTS failed ({exc}); audio skipped.")
        return None
    except EdgeTTSError as exc:
        warnings.append(f"edge-tts failed ({exc}); audio skipped.")
        return None
    except TTSBackendError as exc:
        warnings.append(f"TTS failed ({exc}); audio skipped.")
        return None
    except Exception as exc:
        logger.exception("Unexpected TTS failure")
        warnings.append(f"TTS failed unexpectedly ({exc}); audio skipped.")
        return None


async def _try_edge(
    config: Config, text: str, audio_path: Path, warnings: list[str]
) -> Literal["mlx", "edge"] | None:
    backend = get_edge_backend(config)
    try:
        await backend.synthesize(text, audio_path)
        return "edge"
    except EdgeTTSError as exc:
        warnings.append(f"edge-tts fallback also failed ({exc}); audio skipped.")
        return None
    except Exception as exc:
        logger.exception("Unexpected edge-tts failure during fallback")
        warnings.append(f"edge-tts fallback failed unexpectedly ({exc}); audio skipped.")
        return None
