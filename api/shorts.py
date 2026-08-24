"""
api/shorts.py — GET /api/v1/shorts

Cursor-paginated shorts feed.
GUEST-accessible: no login required.

Response: { items[], has_more, next_cursor }
Item:     { id, title, source, url, thumbnail }
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Shorts"])


def _decode_page(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except Exception:
        return 1


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()


def _make_id(url: str, idx: int) -> str:
    """
    Stable, unique ID per item.
    Using url+idx so two occurrences of the same URL (possible when R302
    random-samples across paginated calls) get distinct IDs — preventing
    Compose VerticalPager duplicate-key crashes on the Android client.
    """
    return hashlib.md5(f"{url}:{idx}".encode()).hexdigest()[:16]


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
        tmdb_id=0,
        media_type="movie",
        page=page,
        fresh=bool(fresh),
    )

    raw_shorts = result.get("shorts", [])

    items = [
        {
            "id":        _make_id(s.get("url", ""), i),   # FIX: unique id, not url
            "title":     s.get("title", ""),
            "source":    s.get("provider") or s.get("source") or "original",
            "url":       s.get("url", ""),
            "thumbnail": s.get("thumbnail") or None,
        }
        for i, s in enumerate(raw_shorts[:limit])
        if s.get("url")
    ]

    # FIX: compare page_items length, not raw_shorts length.
    # Old code: has_more = len(raw_shorts) >= limit
    # That returned True even when page_items was empty (e.g. page 3 of 30
    # total items with limit=10), causing the Android client to loop on
    # empty pages and appear to "buffer forever".
    has_more = len(items) >= limit

    return {
        "items":       items,
        "has_more":    has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }
