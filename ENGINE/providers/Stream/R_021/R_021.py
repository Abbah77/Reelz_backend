"""
ENGINE/providers/Stream/R_021/R_021.py — Movies4u (Indian content)

/?s=<title year> -> article links -> IMDb id verify -> download buttons -> host links -> stream.
Requires imdb_id for reliable matching.

Type: m3u8 | mp4
Flow:
  1. cf_get search page -> article hrefs
  2. cf_get each post -> verify IMDb id -> scrape download button hrefs
  3. cf_get host link -> extract_media_urls -> Stream objects

Ported from Streamplay's Movies4uProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls


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
            search_html = await cf_get(f"{api}/?s={search_q}", referer=api)
            if not search_html:
                return result

            ssoup = parse(search_html)
            post_urls: list[str] = []
            for a in ssoup.select("article h2 a, article h3 a"):
                href = a.get("href") or ""
                if href and href not in post_urls:
                    post_urls.append(href)

            host_urls: set[str] = set()

            for post_url in post_urls:
                post_html = await cf_get(post_url, referer=api)
                if not post_html:
                    continue
                psoup = parse(post_html)
                # Verify IMDb id
                imdb_a = psoup.select_one(f'a[href*="imdb.com/title/{data.imdb_id}"]')
                if not imdb_a:
                    continue

                if data.season is None:
                    inner_url = psoup.select_one("div.download-links-div a.btn")
                    if not inner_url:
                        continue
                    inner_html = await cf_get(inner_url.get("href") or "", referer=api)
                    if not inner_html:
                        continue
                    isoup = parse(inner_html)
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
                        ep_html = await cf_get(season_link_a.get("href") or "", referer=api)
                        if not ep_html:
                            continue
                        esoup = parse(ep_html)
                        ep_blocks = esoup.select("div.downloads-btns-div")
                        ep_idx = (data.episode or 1) - 1
                        if 0 <= ep_idx < len(ep_blocks):
                            for a in ep_blocks[ep_idx].select("a.btn"):
                                h = a.get("href") or ""
                                if h:
                                    host_urls.add(h)

            for href in host_urls:
                host_html = await cf_get(href, referer=api)
                if host_html:
                    for url in extract_media_urls(host_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-021 Movies4u",
                        ))
        except Exception:
            pass
        return result
