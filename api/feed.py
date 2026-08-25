"""
api/feed.py — Home feed routes.

GET /api/v1/feed             — all sections in one shot
GET /api/v1/feed/{sectionId} — single section with pagination

GUEST-accessible: no login required. If a JWT is present, user_id is
available for future personalisation (e.g. highlighting watchlisted items).

All responses wrapped in the standard envelope:
  { "ok": true, "data": {...}, "error": null, "cache_ttl_ms": ... }

cache_ttl_ms lives at envelope root — not inside data.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify
from api.envelope import ok

router = APIRouter(prefix="/api/v1", tags=["Feed"])

_FEED_TTL_MS    = 3_600_000   # 1 hour
_SECTION_TTL_MS = 1_800_000   # 30 min


@router.get("/feed")
async def get_feed(
    refresh: int = Query(0, description="1 = bypass server cache"),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import build_feed
    result = await build_feed(force=bool(refresh))

    # build_feed returns {sections, cache_ttl_ms} — split into data vs envelope
    sections      = result.get("sections", [])
    cache_ttl_ms  = result.get("cache_ttl_ms", _FEED_TTL_MS)

    return ok({"sections": sections}, cache_ttl_ms=cache_ttl_ms)


@router.get("/feed/{section_id}")
async def get_feed_section(
    section_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import get_section
    result = await get_section(section_id, cursor=cursor, limit=limit)

    cache_ttl_ms = result.pop("cache_ttl_ms", _SECTION_TTL_MS)
    return ok(result, cache_ttl_ms=cache_ttl_ms)
