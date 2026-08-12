"""
api/media.py — Media detail and season episode routes.

GET /api/v1/media/{id}                 — full detail for movie or TV show
GET /api/v1/media/{id}/season/{season} — episode list for a specific season

ID format: "movie:550" or "tv:1396"
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Media"])


@router.get("/media/{media_id}")
async def get_detail(
    media_id: str,
    _: None = Depends(verify),
):
    from CATALOG.media import get_detail
    return await get_detail(media_id)


@router.get("/media/{media_id}/season/{season}")
async def get_season(
    media_id: str,
    season: int,
    _: None = Depends(verify),
):
    from CATALOG.media import get_season
    return await get_season(media_id, season)
