"""
CATALOG/media.py — Media detail and season/episode resolution.

ID format: "<type>:<tmdb_id>"  e.g. "movie:550" or "tv:1396"
"""
from __future__ import annotations

from fastapi import HTTPException

from CATALOG.tmdb import (
    get_movie_detail, get_tv_detail, get_season_detail,
    normalise_detail, normalise_episode,
)


def _parse_id(media_id: str) -> tuple[str, int]:
    parts = media_id.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="ID must be 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")
    try:
        return parts[0], int(parts[1])
    except ValueError:
        raise HTTPException(status_code=400, detail="TMDB ID must be an integer")


async def get_detail(media_id: str) -> dict:
    media_type, tmdb_id = _parse_id(media_id)

    if media_type == "movie":
        raw = await get_movie_detail(tmdb_id)
    else:
        raw = await get_tv_detail(tmdb_id)

    if raw is None:
        raise HTTPException(status_code=404, detail="Media not found")

    return normalise_detail(raw, media_type)


async def get_season(media_id: str, season_number: int) -> dict:
    media_type, tmdb_id = _parse_id(media_id)
    if media_type != "tv":
        raise HTTPException(status_code=400, detail="Season episodes are only available for TV shows")

    raw = await get_season_detail(tmdb_id, season_number)
    if raw is None:
        raise HTTPException(status_code=404, detail="Season not found")

    episodes = [
        normalise_episode(ep, season_number)
        for ep in raw.get("episodes", [])
    ]

    return {
        "episodes":     episodes,
        "cache_ttl_ms": 86_400_000,
    }
