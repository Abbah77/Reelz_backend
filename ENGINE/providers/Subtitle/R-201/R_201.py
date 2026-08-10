"""
ENGINE/providers/Subtitle/R-201/R_201.py — OpenSubtitles

Largest subtitle database. Free public API.
Get your key at: https://www.opensubtitles.com/en/consumers

Tools needed: none
"""
from __future__ import annotations

from typing import Optional

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import get_client, UA

_API = "https://api.opensubtitles.com/api/v1"
_API_KEY = ""   # set your key here or move to config


def _headers() -> dict:
    return {
        "User-Agent": "ReelzApp v1.0",
        "Api-Key": _API_KEY,
        "Content-Type": "application/json",
    }


async def _search(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int], languages: list[str]) -> list[dict]:
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
        client = await get_client()
        res = await client.get(f"{_API}/subtitles", headers=_headers(), params=params, timeout=10)
        if res.status_code >= 400:
            return []
        j = res.json()
        return j.get("data", []) if j else []
    except Exception:
        return []


async def _get_download_url(file_id: int) -> Optional[str]:
    try:
        client = await get_client()
        res = await client.post(
            f"{_API}/download",
            json={"file_id": file_id},
            headers=_headers(),
            timeout=10,
        )
        if res.status_code >= 400:
            return None
        j = res.json()
        return j.get("link")
    except Exception:
        return None


class R201Provider(Provider):
    id = "R-201"
    name = "OpenSubtitles"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            langs = ["en"]
            hits = await _search(data.tmdb_id, data.type, data.season, data.episode, langs)
            for hit in hits[:10]:
                attrs = hit.get("attributes", {})
                files = attrs.get("files") or [{}]
                file_id = (files[0] or {}).get("file_id")
                if not file_id:
                    continue
                url = await _get_download_url(file_id)
                if not url:
                    continue
                result.subtitles.append(Subtitle(
                    url=url,
                    language=attrs.get("language", "en"),
                    label=attrs.get("release") or "OpenSubtitles",
                    format="srt",
                ))
        except Exception:
            pass
        return result
