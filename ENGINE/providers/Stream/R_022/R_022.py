"""
ENGINE/providers/Stream/R-022/R_022.py — RogMovies (Indian content)

/search.php?q=<imdbId|title> -> JSON -> permalink -> V-Cloud/G-Direct -> stream.
Ported from Streamplay's RogMoviesProvider.
"""
from __future__ import annotations

import json
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.flaresolverr import solve_cloudflare

_VEGA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "cookie": "xla=s4t",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


async def _cf_get(url: str, referer: str = "") -> str | None:
    try:
        client = await get_client()
        h = {**_VEGA_HEADERS}
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
        streams.append(Stream(url=url, type="m3u8" if ".m3u8" in url else "mp4", server="R-022 RogMovies"))
    return streams


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
                raw = await _cf_get(f"{api}/search.php?q={query}", referer=api)
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

            main_html = await _cf_get(api + permalink, referer=api)
            if not main_html:
                return result

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(main_html, "html.parser")
            sources: set[str] = set()

            if data.season is None:
                for btn in soup.select("button.dwd-button"):
                    h = btn.parent.get("href") if btn.parent else None
                    if h:
                        page_html = await _cf_get(h, referer=api)
                        if not page_html:
                            continue
                        ps = BeautifulSoup(page_html, "html.parser")
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
                    page_html = await _cf_get(src, referer=api)
                    if not page_html:
                        continue
                    ps = BeautifulSoup(page_html, "html.parser")
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
                for s in await _load_extractor(src):
                    result.streams.append(s)
        except Exception:
            pass
        return result
