"""
api/media.py — Media detail and season/episode routes.

GET /api/v1/media/{id}                 — full detail for movie or TV show
GET /api/v1/media/{id}/season/{season} — episode list for a specific season

ID format: "movie:550" or "tv:1396"

GUEST-accessible: no login required.

All responses wrapped in the standard envelope:
  { "ok": true, "data": {...}, "error": null, "cache_ttl_ms": ... }

cache_ttl_ms lives at envelope root — not inside data.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends
from api.auth import verify
from api.envelope import ok

router = APIRouter(prefix="/api/v1", tags=["Media"])

_DETAIL_TTL_MS  = 3_600_000   # 1 hour
_SEASON_TTL_MS  = 86_400_000  # 24 hours


@router.get("/media/{media_id}")
async def get_detail(
    media_id: str,
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.media import get_detail
    result = await get_detail(media_id)

    # get_detail returns the full detail dict; strip cache_ttl_ms if present
    cache_ttl_ms = result.pop("cache_ttl_ms", _DETAIL_TTL_MS)
    return ok(result, cache_ttl_ms=cache_ttl_ms)


@router.get("/media/{media_id}/season/{season}")
async def get_season(
    media_id: str,
    season: int,
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.media import get_season
    result = await get_season(media_id, season)

    # get_season returns {episodes, cache_ttl_ms}
    cache_ttl_ms = result.pop("cache_ttl_ms", _SEASON_TTL_MS)
    return ok(result, cache_ttl_ms=cache_ttl_ms)
