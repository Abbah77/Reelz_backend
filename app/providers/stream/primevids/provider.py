"""
providers/stream/primevids/provider.py

PrimeVids — embed aggregator serving movie / series / asian.
Tries multiple domain mirrors. Extracts m3u8 > mp4 > iframe in priority order.
"""
from __future__ import annotations

import re

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Stream
from app.clients.http import app, safe_get, UA

_DOMAINS = [
    "https://primevids.to",
    "https://www.primevids.to",
]

_M3U8_RE = re.compile(r'["\']((?:https?:)?//[^"\']*\.m3u8[^"\']*)["\']')
_MP4_RE  = re.compile(r'["\']((?:https?:)?//[^"\']*\.mp4[^"\']*)["\']')


class PrimeVidsProvider(Provider):
    id = "primevids"
    name = "PrimeVids"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        if data.is_anime or not data.id:
            return result

        for base in _DOMAINS:
            try:
                if data.season is None:
                    url = f"{base}/embed/movie?tmdb={data.id}"
                else:
                    url = (
                        f"{base}/embed/tv"
                        f"?tmdb={data.id}"
                        f"&season={data.season}"
                        f"&episode={data.episode}"
                    )

                headers = {
                    "User-Agent": UA,
                    "Referer": f"{base}/",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                }
                res = await safe_get(url, headers=headers)
                if not res or not res.is_successful:
                    continue

                text = res.text or ""

                for m in _M3U8_RE.finditer(text):
                    link = m.group(1)
                    if link.startswith("http"):
                        result.streams.append(Stream(
                            server="PrimeVids",
                            link=link,
                            type="m3u8",
                            headers={"Referer": f"{base}/"},
                        ))

                if not result.streams:
                    for m in _MP4_RE.finditer(text):
                        link = m.group(1)
                        if link.startswith("http"):
                            result.streams.append(Stream(
                                server="PrimeVids",
                                link=link,
                                type="mp4",
                                headers={"Referer": f"{base}/"},
                            ))

                if not result.streams:
                    soup = res.document
                    for i, iframe in enumerate(soup.find_all("iframe")):
                        src = iframe.get("src") or iframe.get("data-src") or ""
                        if src and src.startswith("http"):
                            label = f"PrimeVids (Server {i + 1})" if i > 0 else "PrimeVids (Iframe)"
                            result.streams.append(Stream(server=label, link=src, type="iframe"))

                if result.streams:
                    break

            except Exception:
                continue

        return result
