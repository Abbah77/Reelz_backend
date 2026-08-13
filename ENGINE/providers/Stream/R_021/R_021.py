"""
ENGINE/providers/Stream/R-021/R_021.py — Movies4u (Indian content)

/?s=<title year> -> article links -> IMDb id verify -> download buttons -> host links -> stream.
Requires imdb_id for reliable matching.
Ported from Streamplay's Movies4uProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.flaresolverr import solve_cloudflare


async def _cf_get(url: str, referer: str = "") -> str | None:
    try:
        client = await get_client()
        h = {"User-Agent": UA}
        if referer:
            h["Referer"] = referer
        r = await client.get(url, headers=h, timeout=20)
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
        streams.append(Stream(
            url=url,
            type="m3u8" if ".m3u8" in url else "mp4",
            server="R-021 Movies4u",
        ))
    return streams


class R021Provider(Provider):
    id = "R-021"
    name = "Movies4u"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("movies4u")
            if not api or not data.imdb_id:
                return result

            search_q = f"{data.title or ''} {data.year or ''}".strip()
            search_html = await _cf_get(f"{api}/?s={search_q}", referer=api)
            if not search_html:
                return result

            from bs4 import BeautifulSoup
            ssoup = BeautifulSoup(search_html, "html.parser")
            post_urls: list[str] = []
            for a in ssoup.select("article h2 a, article h3 a"):
                href = a.get("href") or ""
                if href and href not in post_urls:
                    post_urls.append(href)

            host_urls: set[str] = set()

            for post_url in post_urls:
                post_html = await _cf_get(post_url, referer=api)
                if not post_html:
                    continue
                psoup = BeautifulSoup(post_html, "html.parser")
                # Verify IMDb id
                imdb_a = psoup.select_one(f'a[href*="imdb.com/title/{data.imdb_id}"]')
                if not imdb_a:
                    continue

                if data.season is None:
                    inner_url = psoup.select_one("div.download-links-div a.btn")
                    if not inner_url:
                        continue
                    inner_html = await _cf_get(inner_url.get("href") or "", referer=api)
                    if not inner_html:
                        continue
                    isoup = BeautifulSoup(inner_html, "html.parser")
                    for a in isoup.select("div.downloads-btns-div a.btn"):
                        h = a.get("href") or ""
                        if h:
                            host_urls.add(h)
                else:
                    for block in psoup.select("div.downloads-btns-div"):
                        prev = block.find_previous_sibling()
                        header_text = (prev.get_text() if prev else "") or ""
                        if not re.search(rf"Season {data.season}", header_text, re.I):
                            continue
                        season_link_a = next(
                            (a for a in block.select("a.btn") if not re.search(r"zip", a.get_text(), re.I)), None
                        )
                        if not season_link_a:
                            continue
                        ep_html = await _cf_get(season_link_a.get("href") or "", referer=api)
                        if not ep_html:
                            continue
                        esoup = BeautifulSoup(ep_html, "html.parser")
                        ep_blocks = esoup.select("div.downloads-btns-div")
                        ep_idx = (data.episode or 1) - 1
                        if 0 <= ep_idx < len(ep_blocks):
                            for a in ep_blocks[ep_idx].select("a.btn"):
                                h = a.get("href") or ""
                                if h:
                                    host_urls.add(h)

            for href in host_urls:
                for s in await _load_extractor(href):
                    result.streams.append(s)
        except Exception:
            pass
        return result
