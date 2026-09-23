"""
ENGINE/providers/Stream/R_028/R_028.py — VidEasy

Type: m3u8 | mp4
Flow:
  1. Fan out across 14 VidEasy sub-servers concurrently
  2. Each: GET api.videasy.net/<server>/sources-with-title?... → encrypted blob
  3. POST enc-dec.app/api/dec-videasy {text, id} → decrypted sources + subtitles
  4. Push each source as a stream
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_post

_API = "https://api.videasy.net"
_ORIGIN = "https://www.cineby.sc"

_SERVERS = [
    "myflixerzupcloud",
    "1movies",
    "moviebox",
    "primewire",
    "m4uhd",
    "hdmovie",
    "cdn",
    "primesrcme",
    "visioncine",
    "overflix",
    "superflix",
    "cuevana",
    "lamovie",
    "mb-flix",
]


class R028Provider(Provider):
    id = "R-028"
    name = "VidEasy"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title or not data.tmdb_id:
            return result
        try:
            client = await get_client()
            headers = {
                "Accept": "*/*",
                "User-Agent": UA,
                "Origin": _ORIGIN,
                "Referer": f"{_ORIGIN}/",
            }
            enc_title = quote(quote(data.title))
            lock = asyncio.Lock()

            async def fetch_server(server: str) -> None:
                try:
                    if data.season is None:
                        url = (
                            f"{_API}/{server}/sources-with-title"
                            f"?title={enc_title}&mediaType=movie"
                            f"&year={data.year or ''}&tmdbId={data.tmdb_id}"
                            f"&imdbId={data.imdb_id or ''}"
                        )
                    else:
                        url = (
                            f"{_API}/{server}/sources-with-title"
                            f"?title={enc_title}&mediaType=tv"
                            f"&year={data.year or ''}&tmdbId={data.tmdb_id}"
                            f"&episodeId={data.episode}&seasonId={data.season}"
                            f"&imdbId={data.imdb_id or ''}"
                        )

                    res = await client.get(url, headers=headers, timeout=10)
                    enc_blob = res.text.strip()
                    if not enc_blob or not enc_blob.startswith("{") is False and not isinstance(enc_blob, str):
                        # enc_blob is a JSON-string payload (not an object)
                        pass
                    if not enc_blob:
                        return

                    dec = await enc_dec_post("dec-videasy", {"text": enc_blob, "id": data.tmdb_id})
                    if not dec:
                        return

                    dec_result = dec.get("result") or {}
                    sources = dec_result.get("sources", [])
                    subs = dec_result.get("subtitles", [])

                    async with lock:
                        for src in (sources if isinstance(sources, list) else []):
                            link = src.get("url", "")
                            if not link:
                                continue
                            ltype = "m3u8" if ".m3u8" in link else ("mp4" if ".mp4" in link or ".mkv" in link else "iframe")
                            if ltype == "iframe":
                                continue
                            result.streams.append(Stream(
                                url=link,
                                type=ltype,
                                server=f"R-028 VidEasy [{server}]",
                                quality=src.get("quality"),
                                headers=dict(headers),
                                referer=f"{_ORIGIN}/",
                            ))
                        for sub in (subs if isinstance(subs, list) else []):
                            sub_url = sub.get("url", "")
                            if sub_url:
                                result.subtitles.append(Subtitle(
                                    language=sub.get("language", "Unknown"),
                                    url=sub_url,
                                ))
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(s) for s in _SERVERS])
        except Exception:
            pass
        return result
