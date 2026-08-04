"""
providers/subtitle/wyzie/provider.py — Wyzie subtitle service.
"""
from __future__ import annotations

from typing import Optional

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Subtitle
from app.clients.http import app, UA
from app.config import get_settings

_settings = get_settings()
_WYZIE_API = "https://subs.wyzie.ru"


async def search_wyzie(
    imdb_id: Optional[str],
    tmdb_id: int,
    media_type: str,
    season: Optional[int],
    episode: Optional[int],
    languages: list[str],
) -> list[dict]:
    if not _settings.wyzie_key:
        return []
    try:
        params: dict = {
            "tmdb": tmdb_id,
            "language": ",".join(languages),
            "type": "movie" if media_type == "movie" else "show",
        }
        if imdb_id:
            params["imdb"] = imdb_id.replace("tt", "")
        if season is not None:
            params["season"] = season
        if episode is not None:
            params["episode"] = episode

        res = await app.get(
            f"{_WYZIE_API}/subtitles",
            headers={"User-Agent": UA, "X-API-Key": _settings.wyzie_key},
            params=params,
            timeout=10,
        )
        if not res or not res.is_successful:
            return []
        j = res.json()
        return j if isinstance(j, list) else j.get("data", []) if j else []
    except Exception:
        return []


class WyzieSubsProvider(Provider):
    id = "wyzie"
    name = "Wyzie Subs"

    async def invoke(self, data: LinkData) -> ProviderResult:
        # Managed directly by subtitle manager.
        return ProviderResult()
