"""
ENGINE/providers/Stream/R_022/R_022.py — RogMovies (Indian content)

/search.php?q=<imdbId|title> -> JSON -> permalink -> V-Cloud/G-Direct -> stream.

Type: m3u8 | mp4
Flow:
  1. cf_get /search.php -> JSON -> match by imdb_id or title keywords
  2. cf_get permalink -> scrape V-Cloud/G-Direct sources
  3. For TV: navigate episode pages; for each source cf_get -> extract_media_urls

Ported from Streamplay's RogMoviesProvider.
"""
from __future__ import annotations

import json
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls

_VEGA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "cookie": "xla=s4t",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


class R022Provider(Provider):
    id = "R-022"
    name = "RogMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("rogmovies")
            if not api:
                return result

            async def search(query: str) -> list[dict]:
                raw = await cf_get(f"{api}/search.php?q={query}", referer=api, extra_headers=_VEGA_HEADERS)
                if not raw:
                    return []
                try:
                    j = json.loads(raw)
                    return [(h.get("document") or {}) for h in (j.get("hits") or [])]
                except Exception:
                    return []

            docs = (await search(data.imdb_id)) if data.imdb_id else []
            if not docs and data.title:
                docs = await search(data.title)
            if not docs:
                return result

            keywords = [w for w in _norm(data.title or "").split() if len(w) > 2]
            match = (
                next((d for d in docs if data.imdb_id and (d.get("imdb_id") or "").lower() == data.imdb_id.lower()), None)
                or next((d for d in docs if any(k in _norm(d.get("post_title") or "") for k in keywords)), None)
                or docs[0]
            )
            permalink: str = match.get("permalink") or ""
            if not permalink:
                return result

            main_html = await cf_get(api + permalink, referer=api, extra_headers=_VEGA_HEADERS)
            if not main_html:
                return result

            soup = parse(main_html)
            sources: set[str] = set()

            if data.season is None:
                for btn in soup.select("button.dwd-button"):
                    h = btn.parent.get("href") if btn.parent else None
                    if h:
                        page_html = await cf_get(h, referer=api, extra_headers=_VEGA_HEADERS)
                        if not page_html:
                            continue
                        ps = parse(page_html)
                        for btn2 in ps.select("button.btn"):
                            if re.search(r"V-Cloud|G-Direct", btn2.get_text(), re.I):
                                h2 = btn2.parent.get("href") if btn2.parent else None
                                if h2:
                                    sources.add(h2)
            else:
                season_re = re.compile(rf"Season {data.season}", re.I)
                link_re = re.compile(r"(V-Cloud|Single|Episode|G-Direct)", re.I)
                for tag in soup.select("h3,h5"):
                    if not (season_re.search(tag.get_text()) or re.search(r"Episode", tag.get_text(), re.I)):
                        continue
                    sib = tag.find_next_sibling()
                    while sib and sib.name not in ("h3", "h5", "h4"):
                        for a in sib.select("a"):
                            if link_re.search(a.get_text()):
                                h = a.get("href")
                                if h:
                                    sources.add(h)
                        sib = sib.find_next_sibling()

                ep_re = re.compile(rf"Episodes?\s*:\s*{data.episode}", re.I)
                ep_sources: set[str] = set()
                for src in sources:
                    page_html = await cf_get(src, referer=api, extra_headers=_VEGA_HEADERS)
                    if not page_html:
                        continue
                    ps = parse(page_html)
                    for h4 in ps.select("h4"):
                        if ep_re.search(h4.get_text()):
                            sib = h4.find_next_sibling()
                            while sib and sib.name not in ("h4",):
                                for a in sib.select("a"):
                                    h = a.get("href")
                                    if h:
                                        ep_sources.add(h)
                                sib = sib.find_next_sibling()
                            break
                sources = ep_sources

            for src in sources:
                src_html = await cf_get(src, referer=api, extra_headers=_VEGA_HEADERS)
                if src_html:
                    for url in extract_media_urls(src_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-022 RogMovies",
                        ))
        except Exception:
            pass
        return result
