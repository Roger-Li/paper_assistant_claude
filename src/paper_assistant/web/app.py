"""FastAPI application factory for Paper Assistant web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from paper_assistant.config import Config

WEB_DIR = Path(__file__).parent


def create_app(config: Config) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Paper Assistant")

    # Store config on app state
    app.state.config = config

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    # Mount audio directory for podcast serving
    config.ensure_dirs()
    app.mount("/audio", StaticFiles(directory=str(config.audio_dir)), name="audio")

    # Set up templates
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    # Include routes
    from paper_assistant.web.routes import create_router

    app.include_router(create_router(config, templates))

    return app
