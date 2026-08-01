"""
PrimeVids provider — embed aggregator.

Flow:
  1. Build the embed URL using TMDB id (movie) or TMDB id + season + episode (TV).
  2. Fetch the page and look for iframe src or direct source URLs in script tags.
  3. Return each source as an iframe or resolved m3u8/mp4 stream.

PrimeVids follows the common "multi-server embed" pattern used by sites like VidSrc,
2Embed, and VidLink: it wraps multiple upstream players behind a single embed URL.
We surface the iframe link and let the player resolve it client-side, exactly as
TwoEmbedProvider does — OR we attempt to extract the direct stream if it is present
in the page source.
"""
from __future__ import annotations

import re

from app.models import LinkData, ExtractorResult, Stream
from app.providers.base import Provider
from app.utils.http import app, safe_get, UA

# ── Domain list (update here on rotation; no redeploy needed) ────────────────
_DOMAINS = [
    "https://primevids.to",
    "https://www.primevids.to",
]

_M3U8_RE = re.compile(r'["\']([^"\']*\.m3u8[^"\']*)["\']')
_MP4_RE = re.compile(r'["\']([^"\']*\.mp4[^"\']*)["\']')
_IFRAME_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)


class PrimeVidsProvider(Provider):
    id = "primevids"
    name = "PrimeVids"
    # Serves movies and TV; skip for pure anime (dedicated anime providers handle those)
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if data.is_anime or not data.id:
            return result

        for base in _DOMAINS:
            try:
                if data.season is None:
                    embed_url = f"{base}/embed/movie?tmdb={data.id}"
                else:
                    embed_url = (
                        f"{base}/embed/tv"
                        f"?tmdb={data.id}"
                        f"&season={data.season}"
                        f"&episode={data.episode}"
                    )

                headers = {
                    "User-Agent": UA,
                    "Referer": f"{base}/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }

                res = await safe_get(embed_url, headers=headers)
                if not res or not res.is_successful:
                    continue

                text = res.text or ""

                # Priority 1: direct m3u8 in page source
                for m in _M3U8_RE.finditer(text):
                    link = m.group(1)
                    if link.startswith("http"):
                        result.streams.append(Stream(
                            server="PrimeVids",
                            link=link,
                            type="m3u8",
                            headers={"Referer": f"{base}/"},
                        ))

                # Priority 2: direct mp4
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

                # Priority 3: iframe passthrough (let the client resolve it)
                if not result.streams:
                    soup = res.document
                    iframes = soup.find_all("iframe")
                    for i, iframe in enumerate(iframes):
                        src = iframe.get("src") or iframe.get("data-src") or ""
                        if src and src.startswith("http"):
                            label = f"PrimeVids (Server {i + 1})" if len(iframes) > 1 else "PrimeVids (Iframe)"
                            result.streams.append(Stream(
                                server=label,
                                link=src,
                                type="iframe",
                            ))

                if result.streams:
                    break  # got results — no need to try the next domain mirror

            except Exception:
                continue

        return result
