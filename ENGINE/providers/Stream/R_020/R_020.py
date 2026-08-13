"""
ENGINE/providers/Stream/R-020/R_020.py — 4KHdHub (Indian 4K)

/?s=<title> -> div.card-grid > a.movie-card -> content page
Movie: div.download-item a -> getRedirectLinks -> stream
TV:    div.episode-download-item matching S##E## -> div.episode-links > a

Ported from Streamplay's FourKHdHubProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import get_redirect_link, bypass_hrefli
from ENGINE.tools.flaresolverr import solve_cloudflare


def _pad2(n: int | None) -> str:
    if n is None:
        return "0"
    return f"0{n}" if n < 10 else str(n)


async def _cf_get_html(url: str) -> str | None:
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
    html = await _cf_get_html(source)
    if not html:
        return []
    streams: list[Stream] = []
    for m in re.finditer(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', html):
        url = m.group(1)
        streams.append(Stream(
            url=url,
            type="m3u8" if ".m3u8" in url else "mp4",
            server="R-020 4KHdHub",
        ))
    return streams


class R020Provider(Provider):
    id = "R-020"
    name = "4KHdHub"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            domain = await get_domain("n4khdhub")
            if not domain:
                return result
            query = (data.title or "").strip()
            if not query:
                return result

            search_html = await _cf_get_html(f"{domain}/?s={query}")
            if not search_html:
                return result

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(search_html, "html.parser")
            norm_title = query.lower().strip()
            year_str = str(data.year) if data.year else None

            def content(el) -> str:
                return (soup(el).find("div", class_="movie-card-content") or soup(el)).get_text("", strip=True).lower()

            cards = soup.select("div.card-grid > a.movie-card")
            matched = None
            for card in cards:
                c = card.get_text("", strip=True).lower()
                if norm_title in c and (year_str is None or year_str in c):
                    matched = card
                    break
            if not matched:
                for card in cards:
                    if norm_title in card.get_text("", strip=True).lower():
                        matched = card
                        break
            if not matched:
                return result

            link = matched.get("href") or ""
            url = link if link.startswith("http") else f"{domain}{link}"
            detail_html = await _cf_get_html(url)
            if not detail_html:
                return result

            dsoup = BeautifulSoup(detail_html, "html.parser")
            hrefs: set[str] = set()

            if data.season is None:
                for a in dsoup.select("div.download-item a"):
                    h = a.get("href") or ""
                    if h:
                        hrefs.add(h)
            else:
                s_text = f"S{_pad2(data.season)}"
                e_text = f"E{_pad2(data.episode)}" if data.episode is not None else None
                for el in dsoup.select("div.episode-download-item"):
                    text = el.get_text()
                    if s_text.lower() in text.lower() and (e_text is None or e_text.lower() in text.lower()):
                        for a in el.select("div.episode-links > a"):
                            h = a.get("href") or ""
                            if h:
                                hrefs.add(h)

            for href in hrefs:
                source = (await get_redirect_link(href)) or (await bypass_hrefli(href)) or href
                for s in await _load_extractor(source):
                    result.streams.append(s)
        except Exception:
            pass
        return result
