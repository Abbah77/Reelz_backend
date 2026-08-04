"""
providers/subtitle/opensubtitles/provider.py

OpenSubtitles REST API v1 — search + download URL resolution.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Subtitle
from app.clients.http import app, UA

_OS_API = "https://api.opensubtitles.com/api/v1"
_OS_HEADERS = {
    "User-Agent": "ReelzApp v1.0",
    "Api-Key": "sZnPp5jJkqpIAkNQa9K7G3OGjSZFXnPs",  # public demo key
    "Content-Type": "application/json",
}


async def search_opensubtitles(
    tmdb_id: int,
    media_type: str,
    season: Optional[int],
    episode: Optional[int],
    languages: list[str],
) -> list[dict]:
    params: dict = {
        "tmdb_id": tmdb_id,
        "type": "movie" if media_type == "movie" else "episode",
        "languages": ",".join(languages),
    }
    if season is not None:
        params["season_number"] = season
    if episode is not None:
        params["episode_number"] = episode

    try:
        res = await app.get(f"{_OS_API}/subtitles", headers=_OS_HEADERS, params=params, timeout=10)
        if not res or not res.is_successful:
            return []
        j = res.json()
        return j.get("data", []) if j else []
    except Exception:
        return []


async def download_opensubtitles(file_id: int) -> Optional[str]:
    try:
        res = await app.post(
            f"{_OS_API}/download",
            body={"file_id": file_id},
            headers=_OS_HEADERS,
            timeout=10,
        )
        if not res or not res.is_successful:
            return None
        j = res.json()
        return j.get("link") if j else None
    except Exception:
        return None


class OpenSubtitlesProvider(Provider):
    id = "opensubtitles"
    name = "OpenSubtitles"

    async def invoke(self, data: LinkData) -> ProviderResult:
        # The subtitle manager calls the helper functions directly for more control.
        # This provider is here for registry completeness.
        return ProviderResult()
