"""
ENGINE/providers/Stream/R-002/R-002.py — VidFast

Type: direct JSON API
Tools needed: none

FULL WORKING EXAMPLE — copy this as template for new providers.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA


class R002Provider(Provider):
    id = "R-002"
    name = "VidFast"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"https://vidfast.co/api/movie/{data.tmdb_id}"
            else:
                url = f"https://vidfast.co/api/tv/{data.tmdb_id}/{data.season}/{data.episode}"

            client = await get_client()
            res = await client.get(url, headers={"User-Agent": UA, "Referer": "https://vidfast.co/"})

            if res.status_code >= 400:
                return result

            j = res.json()
            sources = j.get("sources") or j.get("data", {}).get("sources", [])

            for src in (sources if isinstance(sources, list) else []):
                link = src.get("url") or src.get("file", "")
                if link:
                    result.streams.append(Stream(
                        url=link,
                        type="m3u8" if ".m3u8" in link else "mp4",
                        server="R-002 VidFast",
                        quality=src.get("label"),
                    ))
        except Exception:
            pass
        return result
