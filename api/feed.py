"""
api/feed.py — Home feed routes.

GET /api/v1/feed             — all sections in one shot
GET /api/v1/feed/{sectionId} — single section with pagination

GUEST-accessible: no login required. If a JWT is present, user_id is
available for future personalisation (e.g. highlighting watchlisted items).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Feed"])


@router.get("/feed")
async def get_feed(
    refresh: int = Query(0, description="1 = bypass server cache"),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import build_feed
    return await build_feed(force=bool(refresh))


@router.get("/feed/{section_id}")
async def get_feed_section(
    section_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.feed_builder import get_section
    return await get_section(section_id, cursor=cursor, limit=limit)
