"""
ENGINE/providers/Shorts/R-301/R_301.py — TMDB Trailers & Clips

Fetches trailers and teasers from TMDB API.
Needs TMDB_API_KEY in .env.

Tools needed: none
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA
from config import get_settings

_s = get_settings()


class R301Provider(Provider):
    id = "R-301"
    name = "TMDB Trailers"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not _s.tmdb_api_key:
            return result
        try:
            mtype = "movie" if data.type == "movie" else "tv"
            url = f"{_s.tmdb_base_url}/{mtype}/{data.tmdb_id}/videos"

            client = await get_client()
            res = await client.get(
                url,
                params={"api_key": _s.tmdb_api_key, "language": "en-US"},
                headers={"User-Agent": UA},
                timeout=10,
            )
            if res.status_code >= 400:
                return result

            for v in (res.json().get("results") or []):
                if v.get("site") == "YouTube":
                    result.shorts.append(Short(
                        url=f"https://www.youtube.com/watch?v={v['key']}",
                        title=v.get("name", "Trailer"),
                        thumbnail=f"https://img.youtube.com/vi/{v['key']}/hqdefault.jpg",
                    ))
        except Exception:
            pass
        return result
