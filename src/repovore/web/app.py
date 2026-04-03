"""FastAPI application factory for the Repovore web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from repovore.config import RepovoreConfig, load_config
from repovore.db import Database
from repovore.output import reindex_all_cards

_HERE = Path(__file__).parent


def create_app(config: RepovoreConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if config is None:
        config = load_config()

    data_dir = Path(config.output.data_dir)
    db = Database(data_dir / "repovore.db")

    # Reindex cards on startup so the index is always in sync
    cards_dir = data_dir / "cards"
    reindex_all_cards(cards_dir, db)

    app = FastAPI(title="Repovore", version="0.1.0")

    # Shared state accessible via request.app.state
    app.state.config = config
    app.state.db = db
    app.state.data_dir = data_dir
    app.state.cards_dir = cards_dir

    # Templates & static files
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # Register routers
    from repovore.web.routes.api import router as api_router
    from repovore.web.routes.pages import router as pages_router

    app.include_router(api_router)
    app.include_router(pages_router)

    return app
