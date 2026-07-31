"""
Subtitle providers:
  - SubtitleAPI  (scraper-based, from the Node subtitleapi extractor)
  - WyzieSubs    (wyzie.ru API — needs WYZIE_KEY)
  - OpenSubtitles (direct API — proxied for the app)
"""
from __future__ import annotations

import re
from typing import Optional

from app.models import LinkData, ExtractorResult, Subtitle
from app.providers.base import Provider
from app.utils.http import app, safe_get, UA


# ══════════════════════════════════════════════════════════════════
# SubtitleAPI
# ══════════════════════════════════════════════════════════════════

class SubtitleApiProvider(Provider):
    id = "subtitleapi"
    name = "SubtitleAPI"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        imdb = data.imdb_id
        if not imdb:
            return result
        try:
            base = "https://subtitleapi.net"
            if data.season is None:
                url = f"{base}/subtitles?id={imdb}&type=movie"
            else:
                url = f"{base}/subtitles?id={imdb}&type=tv&season={data.season}&episode={data.episode}"
            res = await app.get(url, headers={"Referer": base}, timeout=12)
            j = res.json() if res else None
            for item in (j if isinstance(j, list) else []):
                sub_url = item.get("url") or item.get("download") or ""
                lang = item.get("lang") or item.get("language") or "en"
                if sub_url:
                    result.subtitles.append(Subtitle(
                        language=lang,
                        url=sub_url,
                        format=item.get("format", "srt"),
                    ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# WyzieSubs
# ══════════════════════════════════════════════════════════════════

class WyzieSubsProvider(Provider):
    id = "wyziesubs"
    name = "WyzieSubs"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        from app.config import get_settings
        key = get_settings().wyzie_key
        if not key:
            return result
        imdb = data.imdb_id
        if not imdb:
            return result
        try:
            params: dict = {"imdb_id": imdb, "api_key": key}
            if data.season is not None:
                params["season"] = str(data.season)
                params["episode"] = str(data.episode)
            res = await app.get(
                "https://store.wyzie.ru/api/subtitles",
                headers={"Referer": "https://wyzie.ru"},
                timeout=10,
            )
            j = res.json() if res else None
            for item in (j if isinstance(j, list) else []):
                sub_url = item.get("url") or ""
                lang = item.get("lang") or item.get("language") or "en"
                if sub_url:
                    result.subtitles.append(Subtitle(
                        language=lang,
                        url=sub_url,
                        format=item.get("format", "srt"),
                    ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# OpenSubtitles (direct API — called from routes, not as provider)
# ══════════════════════════════════════════════════════════════════

OPENSUBTITLES_API = "https://api.opensubtitles.com/api/v1"
OPENSUBTITLES_KEY = "C8jjWBqYDBiM4U3QA9xJfmf8BiC2ISyq"


async def search_opensubtitles(
    tmdb_id: int,
    media_type: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    languages: Optional[list[str]] = None,
) -> list[dict]:
    """
    Search OpenSubtitles directly — mirrors what the app's OpenSubtitlesRepository.kt does,
    but exposed here for the /api/v1/subtitles endpoint.
    """
    params: dict = {
        "tmdb_id": str(tmdb_id),
        "type": "episode" if media_type == "tv" else "movie",
    }
    if season is not None:
        params["season_number"] = str(season)
    if episode is not None:
        params["episode_number"] = str(episode)
    if languages:
        params["languages"] = ",".join(languages)

    try:
        res = await app.get(
            f"{OPENSUBTITLES_API}/subtitles",
            headers={
                "Api-Key": OPENSUBTITLES_KEY,
                "User-Agent": "Reelz v2.0",
            },
            timeout=15,
        )
        j = res.json() if res else None
        return (j or {}).get("data", [])
    except Exception:
        return []


async def download_opensubtitles(file_id: int) -> Optional[str]:
    """POST /api/v1/download to get the real subtitle download URL."""
    try:
        res = await app.post(
            f"{OPENSUBTITLES_API}/download",
            body={"file_id": file_id},
            headers={
                "Api-Key": OPENSUBTITLES_KEY,
                "User-Agent": "Reelz v2.0",
                "Content-Type": "application/json",
            },
            content_type="application/json",
        )
        j = res.json() if res else None
        return (j or {}).get("link")
    except Exception:
        return None
