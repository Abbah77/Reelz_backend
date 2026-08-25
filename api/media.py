"""
api/media.py — Detail + Season.
cache_ttl_ms: 3_600_000 detail / 86_400_000 season
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Media"])
_DETAIL_TTL = 3_600_000
_SEASON_TTL = 86_400_000


@router.get("/media/{media_id}")
async def get_detail(
    media_id: str,
    response: Response,
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.media import get_detail
    result = await get_detail(media_id)
    ttl = result.pop("cache_ttl_ms", _DETAIL_TTL)
    set_cache(response, ttl)
    return ok(result, cache_ttl_ms=ttl)


@router.get("/media/{media_id}/season/{season}")
async def get_season(
    media_id: str,
    season: int,
    response: Response,
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.media import get_season
    result = await get_season(media_id, season)
    ttl = result.pop("cache_ttl_ms", _SEASON_TTL)
    set_cache(response, ttl)
    return ok(result, cache_ttl_ms=ttl)
