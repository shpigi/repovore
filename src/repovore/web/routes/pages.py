"""HTML page routes for Repovore web UI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from repovore.db import Database
from repovore.output import load_card

router = APIRouter()


def _get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def _get_cards_dir(request: Request) -> Path:
    return request.app.state.cards_dir  # type: ignore[no-any-return]


def _trending_staleness(db: Database) -> dict:  # type: ignore[type-arg]
    """Check if trending data is current for this ISO week."""
    raw = db.get_metadata("trending_updated_at")
    if not raw:
        return {"stale": True, "updated_at": None}
    updated_at = datetime.fromisoformat(raw)
    now = datetime.now(UTC)
    # Same ISO year and week number means "this week"
    stale = updated_at.isocalendar()[:2] != now.isocalendar()[:2]
    return {"stale": stale, "updated_at": updated_at}


@router.get("/", response_class=HTMLResponse)
async def cards_list_page(request: Request) -> HTMLResponse:
    """Render the cards list page."""
    db = _get_db(request)
    cards = db.list_cards(sort_by="health_score", order="desc")
    trending_status = _trending_staleness(db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "cards_list.html",
        {
            "cards": cards,
            "sort": "health_score",
            "order": "desc",
            "trending": trending_status,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request) -> HTMLResponse:
    """Render the about page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "about.html", {})


@router.get("/cards/{owner}/{repo}", response_class=HTMLResponse)
async def card_detail_page(request: Request, owner: str, repo: str) -> HTMLResponse:
    """Render the single card detail page."""
    cards_dir = _get_cards_dir(request)
    path = cards_dir / f"{owner}__{repo}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Card not found: {owner}/{repo}")

    card = load_card(path)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "card_detail.html",
        {"card": card},
    )
