"""
ENGINE/providers/Stream/R-010/R_010.py — PrimeVids (NineTV / moviesapi.club)

Loads moviesapi.club/<type>/<id>, pulls the embedded iframe src,
follows the iframe and tries to extract a direct m3u8/mp4.
If not found, returns the iframe as a passthrough stream.
Ported from Streamplay's NineTvProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://moviesapi.club"
_REFERER = "https://pressplay.top/"


class R010Provider(Provider):
    id = "R-010"
    name = "PrimeVids"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"{_API}/movie/{data.tmdb_id}"
            else:
                url = f"{_API}/tv/{data.tmdb_id}-{data.season}-{data.episode}"

            client = await get_client()
            headers = {"User-Agent": UA, "Referer": _REFERER}
            res = await client.get(url, headers=headers, timeout=15)
            if res.status_code != 200:
                return result

            # Extract iframe src
            iframe_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', res.text, re.I)
            if not iframe_m:
                return result
            iframe = iframe_m.group(1)

            # Try to resolve a direct stream from the embed page
            try:
                embed_html = (await client.get(iframe, headers={"User-Agent": UA, "Referer": f"{_API}/"})).text
                for pattern in [
                    r'(https?:[^"\'\\s]+\.m3u8[^"\'\\s]*)',
                    r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                ]:
                    m = re.search(pattern, embed_html, re.I)
                    if m:
                        link = m.group(1)
                        result.streams.append(Stream(
                            url=link,
                            type="m3u8",
                            server="R-010 PrimeVids",
                            headers={"Referer": iframe},
                        ))
                        return result
            except Exception:
                pass

            # Fallback: return iframe for downstream player
            result.streams.append(Stream(
                url=iframe,
                type="iframe",
                server="R-010 PrimeVids",
                headers={"Referer": f"{_API}/"},
            ))
        except Exception:
            pass
        return result
