"""
ENGINE/providers/Stream/R-024/R_024.py — UhdMovies (Indian UHD)

/search/<title year> -> first article -> detail page -> per-quality links
-> driveleech/driveseed JS redirect OR href.li bypass -> stream.
Ported from Streamplay's UhdMoviesProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import bypass_hrefli, get_redirect_link, get_base_url
from ENGINE.tools.flaresolverr import solve_cloudflare

_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\([\"'](.*?)[\"']\)")


async def _cf_get(url: str) -> str | None:
    try:
        client = await get_client()
        r = await client.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code < 400 and not re.search(r"just a moment", r.text, re.I):
            return r.text
    except Exception:
        pass
    html, _, _ = await solve_cloudflare(url)
    return html


async def _load_extractor(source: str) -> list[Stream]:
    html = await _cf_get(source)
    if not html:
        return []
    streams: list[Stream] = []
    for m in re.finditer(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', html):
        url = m.group(1)
        streams.append(Stream(url=url, type="m3u8" if ".m3u8" in url else "mp4", server="R-024 UhdMovies"))
    return streams


class R024Provider(Provider):
    id = "R-024"
    name = "UhdMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("uhdmovies")
            if not api:
                return result
            query = re.sub(r"[-:]", " ", data.title or "").strip()
            if not query:
                return result

            search_html = await _cf_get(f"{api}/search/{query} {data.year or ''}".strip())
            if not search_html:
                return result

            from bs4 import BeautifulSoup
            ssoup = BeautifulSoup(search_html, "html.parser")
            page_link = ssoup.select_one("article div.entry-image a")
            if not page_link:
                return result
            page_url = page_link.get("href") or ""
            if not page_url.startswith("http"):
                page_url = api + page_url

            detail_html = await _cf_get(page_url)
            if not detail_html:
                return result
            dsoup = BeautifulSoup(detail_html, "html.parser")

            season_re = (
                re.compile(rf"(S0?{data.season}|Season 0?{data.season})", re.I)
                if data.season is not None
                else re.compile(str(data.year or ""))
            )
            ep_re = (
                re.compile(rf"Episode {data.episode}", re.I)
                if data.season is not None
                else re.compile(r"Download", re.I)
            )

            links: list[str] = []
            for p in dsoup.select("div.entry-content p"):
                if not season_re.search(p.get_text()):
                    continue
                sib = p.find_next_sibling()
                if not sib:
                    continue
                for a in sib.select("a"):
                    if ep_re.search(a.get_text()):
                        h = a.get("href")
                        if h and h not in links:
                            links.append(h)

            for link in links:
                drive_link: str | None = None
                try:
                    if re.search(r"driveleech|driveseed", link, re.I):
                        text = await _cf_get(link)
                        if text:
                            m = _JS_REDIRECT_RE.search(text)
                            if m:
                                drive_link = get_base_url(link) + m.group(1)
                    else:
                        drive_link = await bypass_hrefli(link)
                except Exception:
                    continue
                if not drive_link:
                    continue
                for s in await _load_extractor(drive_link):
                    result.streams.append(s)
        except Exception:
            pass
        return result
