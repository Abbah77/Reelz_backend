"""
api/shorts.py — GET /api/v1/shorts

Cursor-paginated shorts feed matching ShortsResponseDto:
    { items, has_more, next_cursor }

Cursor encodes a page number for the ENGINE shorts provider.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Shorts"])

# Shorts are fetched by TMDB trending movies/tv from the ENGINE shorts provider.
# The ENGINE manager returns a flat list; we paginate it here via cursor.

_DEFAULT_TMDB_ID = 0      # 0 = "all" — handled by provider


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
    _: None = Depends(verify),
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

    # Map ENGINE Short → ShortVideoDto shape
    items = [
        {
            "id":           s.get("url", ""),  # URL is the stable ID for shorts
            "title":        s.get("title", ""),
            "author":       s.get("provider", ""),
            "hls_url":      s.get("url", "") if s.get("url", "").endswith(".m3u8") else "",
            "fallback_url": s.get("url", ""),
            "thumbnail":    s.get("thumbnail", ""),
            "duration":     0,
            "width":        1080,
            "height":       1920,
        }
        for s in raw_shorts[:limit]
    ]

    has_more = len(raw_shorts) >= limit

    return {
        "items":       items,
        "has_more":    has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }
