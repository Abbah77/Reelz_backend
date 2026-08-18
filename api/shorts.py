"""
api/shorts.py — GET /api/v1/shorts

Cursor-paginated shorts feed.
GUEST-accessible: no login required.

Response: { items[], has_more, next_cursor }
Item:     { id, title, source, url, thumbnail }
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Shorts"])

_DEFAULT_TMDB_ID = 0


def _decode_page(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except Exception:
        return 1


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()


@router.get("/shorts")
async def get_shorts(
    cursor: Optional[str] = Query(None),
    limit:  int = Query(10, ge=1, le=50),
    fresh:  int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    page = _decode_page(cursor)

    from ENGINE.manager.shorts import get_shorts as engine_shorts
    result = await engine_shorts(
        tmdb_id=_DEFAULT_TMDB_ID,
        media_type="movie",
        page=page,
        fresh=bool(fresh),
    )

    raw_shorts = result.get("shorts", [])

    items = [
        {
            "id":        s.get("url", ""),
            "title":     s.get("title", ""),
            "source":    s.get("provider") or s.get("source") or "original",
            "url":       s.get("url", ""),
            "thumbnail": s.get("thumbnail") or None,
        }
        for s in raw_shorts[:limit]
        if s.get("url")
    ]

    has_more = len(raw_shorts) >= limit

    return {
        "items":       items,
        "has_more":    has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }
