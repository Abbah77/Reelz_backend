"""
api/search.py — Search route.

GET /api/v1/search?q=<query>[&type=movie|tv][&cursor=...][&limit=20]

GUEST-accessible: no login required.

All responses wrapped in the standard envelope:
  { "ok": true, "data": {...}, "error": null, "cache_ttl_ms": ... }
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from api.auth import verify
from api.envelope import ok

router = APIRouter(prefix="/api/v1", tags=["Search"])


@router.get("/search")
async def search(
    q: str                = Query(..., min_length=2, description="Search query (min 2 chars)"),
    type: Optional[str]   = Query(None, description="movie | tv | null = both"),
    cursor: Optional[str] = Query(None),
    limit: int            = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    if type and type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="type must be 'movie' or 'tv'")

    from CATALOG.search import search as do_search
    result = await do_search(query=q, media_type=type, cursor=cursor, limit=limit)

    # search() returns {items, has_more, next_cursor, cache_ttl_ms}
    cache_ttl_ms = result.pop("cache_ttl_ms", 300_000)
    return ok(result, cache_ttl_ms=cache_ttl_ms)
