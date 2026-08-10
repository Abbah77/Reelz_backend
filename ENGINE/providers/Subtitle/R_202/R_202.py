"""
ENGINE/providers/Subtitle/R-202/R_202.py — Wyzie Subs

Fast subtitle API. Set WYZIE_KEY in .env.

Tools needed: none
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import get_client, UA
from config import get_settings

_s = get_settings()
_API = "https://subs.wyzie.ru"


class R202Provider(Provider):
    id = "R-202"
    name = "Wyzie"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not _s.wyzie_key:
            return result
        try:
            params: dict = {
                "tmdb": data.tmdb_id,
                "language": "en",
                "type": "movie" if data.type == "movie" else "show",
            }
            if data.imdb_id:
                params["imdb"] = data.imdb_id.replace("tt", "")
            if data.season is not None:
                params["season"] = data.season
            if data.episode is not None:
                params["episode"] = data.episode

            client = await get_client()
            res = await client.get(
                f"{_API}/subtitles",
                headers={"User-Agent": UA, "X-API-Key": _s.wyzie_key},
                params=params,
                timeout=10,
            )
            if res.status_code >= 400:
                return result

            items = res.json()
            if isinstance(items, dict):
                items = items.get("data", [])

            for item in (items or [])[:10]:
                url = item.get("url") or item.get("download_url", "")
                if not url:
                    continue
                result.subtitles.append(Subtitle(
                    url=url,
                    language=item.get("language", "en"),
                    label=item.get("title") or "Wyzie",
                    format=item.get("format", "srt"),
                ))
        except Exception:
            pass
        return result
