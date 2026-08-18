"""
api/discover.py — Explore/Discover + Genre list routes.

GET /api/v1/discover — paginated media discovery with filters
GET /api/v1/genres   — genre list for filter UI

GUEST-accessible: no login required.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Discover"])


def _decode_page(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except Exception:
        return 1


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()


@router.get("/discover")
async def discover(
    type: str            = Query("movie", description="movie | tv"),
    genre: Optional[str]     = Query(None, description="Genre ID from /genres"),
    sort: str            = Query("popularity", description="popularity | rating | newest"),
    cursor: Optional[str]   = Query(None),
    limit: int            = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.tmdb import discover as tmdb_discover, normalise_card

    page = _decode_page(cursor)
    raw  = await tmdb_discover(
        media_type=type, genre_id=genre,
        sort_by=sort, page=page,
    )
    results = raw.get("results", [])
    items   = [normalise_card(r, type) for r in results[:limit]]
    total   = raw.get("total_pages", 1)
    has_more = page < total

    return {
        "items":        items,
        "has_more":     has_more,
        "next_cursor":  _encode_cursor(page + 1) if has_more else None,
        "cache_ttl_ms": 1_800_000,
    }


@router.get("/genres")
async def get_genres(
    type: str = Query("movie", description="movie | tv"),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.tmdb import get_genres
    genres = await get_genres(type)
    return {
        "genres": [{"id": str(g["id"]), "name": g["name"]} for g in genres]
    }
