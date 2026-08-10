"""
ENGINE/providers/Stream/R-001/R-001.py — 2Embed

Type: iframe embed
No scraping needed — URL is constructed from TMDB id.
No tools needed.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream


class R001Provider(Provider):
    id = "R-001"
    name = "2Embed"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"https://www.2embed.cc/embed/{data.tmdb_id}"
            else:
                url = f"https://www.2embed.cc/embedtv/{data.tmdb_id}&s={data.season}&e={data.episode}"
            result.streams.append(Stream(
                url=url,
                type="iframe",
                server="R-001 2Embed",
            ))
        except Exception:
            pass
        return result
