"""
api/feed.py — Home feed routes.
cache_ttl_ms: 3_600_000 feed / 1_800_000 section
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Feed"])
_FEED_TTL    = 3_600_000
_SECTION_TTL = 1_800_000


@router.get("/feed")
async def get_feed(
    response: Response,
    refresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import build_feed
    result = await build_feed(force=bool(refresh))
    sections     = result.get("sections", [])
    ttl          = result.get("cache_ttl_ms", _FEED_TTL)
    set_cache(response, ttl)
    return ok({"sections": sections}, cache_ttl_ms=ttl)


@router.get("/feed/{section_id}")
async def get_feed_section(
    section_id: str,
    response: Response,
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import get_section
    result = await get_section(section_id, cursor=cursor, limit=limit)
    ttl = result.pop("cache_ttl_ms", _SECTION_TTL)
    set_cache(response, ttl)
    return ok(result, cache_ttl_ms=ttl)
