"""
api/search.py — Search.
cache_ttl_ms: 300_000 (5 min)
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Search"])
_TTL = 300_000


@router.get("/search")
async def search(
    response: Response,
    q: str = Query(..., min_length=2),
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    if type and type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="type must be 'movie' or 'tv'")
    from CATALOG.search import search as do_search
    result = await do_search(query=q, media_type=type, cursor=cursor, limit=limit)
    ttl = result.pop("cache_ttl_ms", _TTL)
    set_cache(response, ttl)
    return ok(result, cache_ttl_ms=ttl)
