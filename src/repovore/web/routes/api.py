"""JSON API routes for Repovore web UI."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from repovore.db import Database
from repovore.output import load_card

router = APIRouter(prefix="/api")


def _get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def _get_cards_dir(request: Request) -> Path:
    return request.app.state.cards_dir  # type: ignore[no-any-return]


def _card_path(cards_dir: Path, owner: str, repo: str) -> Path:
    return cards_dir / f"{owner}__{repo}.json"


@router.get("/cards", response_model=None)
async def list_cards(
    request: Request,
    sort: str = Query("health_score"),
    order: str = Query("desc"),
    maintenance: str | None = Query(None),
    verdict: str | None = Query(None),
    q: str | None = Query(None),
) -> HTMLResponse | JSONResponse:
    """List card summaries. Returns HTML partial for htmx, JSON otherwise."""
    db = _get_db(request)
    rows = db.list_cards(
        sort_by=sort, order=order, maintenance=maintenance, verdict=verdict, search=q,
    )

    # If htmx request, return the HTML partial
    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "partials/cards_table.html",
            {"cards": rows, "sort": sort, "order": order},
        )

    return JSONResponse([dict(r) for r in rows])


@router.get("/cards/{owner}/{repo}")
async def get_card(request: Request, owner: str, repo: str) -> JSONResponse:
    """Return the full card JSON for a single repo."""
    path = _card_path(_get_cards_dir(request), owner, repo)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Card not found: {owner}/{repo}")
    card = load_card(path)
    return JSONResponse(json.loads(card.model_dump_json()))


@router.get("/cards/{owner}/{repo}/staleness")
async def check_staleness(request: Request, owner: str, repo: str) -> JSONResponse:
    """Check if the repo has been pushed to since the card was last fetched."""
    from datetime import datetime

    import requests as http_requests

    path = _card_path(_get_cards_dir(request), owner, repo)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Card not found: {owner}/{repo}")

    card = load_card(path)
    fetched_at = card.fetched_at

    # Lightweight GitHub API call for pushed_at
    resp = await asyncio.to_thread(
        http_requests.get,
        f"https://api.github.com/repos/{owner}/{repo}",
        headers={"Accept": "application/vnd.github.v3+json"},
        timeout=10,
    )
    if resp.status_code != 200:
        return JSONResponse({"error": f"GitHub API returned {resp.status_code}"}, status_code=502)

    pushed_at_str = resp.json().get("pushed_at", "")
    if not pushed_at_str:
        return JSONResponse({
            "stale": False, "pushed_at": None, "fetched_at": fetched_at.isoformat(),
        })

    pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
    stale = pushed_at > fetched_at

    return JSONResponse({
        "stale": stale,
        "pushed_at": pushed_at.isoformat(),
        "fetched_at": fetched_at.isoformat(),
    })


@router.post("/cards/{owner}/{repo}/refresh")
async def refresh_card(request: Request, owner: str, repo: str) -> JSONResponse:
    """Re-process a repo by running the pipeline as a subprocess."""
    url = f"https://github.com/{owner}/{repo}"

    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "repovore", "process", "--url", url, "--force"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        return JSONResponse(
            {"error": "Pipeline failed", "stderr": result.stderr[-500:]},
            status_code=500,
        )

    # Reload and return the updated card
    path = _card_path(_get_cards_dir(request), owner, repo)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Card not found after refresh")

    # Re-index the updated card
    card = load_card(path)
    db = _get_db(request)
    db.upsert_card_index(card.model_dump())

    return JSONResponse(json.loads(card.model_dump_json()))
