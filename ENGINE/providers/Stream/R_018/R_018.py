"""
ENGINE/providers/Stream/R-018/R_018.py — VegaMovies (Indian content)

JSON search by IMDB id or title -> permalink -> V-Cloud/G-Direct buttons -> stream.
Requires imdb_id in LinkData for reliable matching.
Uses WARP for Cloudflare-protected pages.

Ported from Streamplay's VegaMoviesProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import bypass_hrefli
from ENGINE.tools.flaresolverr import solve_cloudflare

_VEGA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "cookie": "xla=s4t",
}


async def _cf_get(url: str, referer: str = "") -> str | None:
    """Fetch HTML with Cloudflare bypass."""
    try:
        client = await get_client()
        h = {**_VEGA_HEADERS}
        if referer:
            h["Referer"] = referer
        r = await client.get(url, headers=h, timeout=20)
        if r.status_code < 400 and not re.search(r"just a moment|cf-browser-verification", r.text, re.I):
            return r.text
    except Exception:
        pass
    html, _, _ = await solve_cloudflare(url)
    return html


class R018Provider(Provider):
    id = "R-018"
    name = "VegaMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("vegamovies")
            if not api:
                return result
            if not data.imdb_id:
                return result

            # 1) Search
            async def fetch_docs(query: str) -> list[dict]:
                html = await _cf_get(f"{api}/search.php?q={query}", referer=api)
                if not html:
                    return []
                import json
                try:
                    j = json.loads(html)
                    return [(h.get("document") or {}) for h in (j.get("hits") or [])]
                except Exception:
                    return []

            docs = await fetch_docs(data.imdb_id)
            match = next((d for d in docs if (d.get("imdb_id") or "").lower() == data.imdb_id.lower()), None)
            if not match and data.title:
                docs = await fetch_docs(data.title)
                tl = data.title.lower()
                match = next((d for d in docs if tl in (d.get("post_title") or "").lower()), None) or (docs[0] if docs else None)
            if not match or not match.get("permalink"):
                return result

            main_html = await _cf_get(api + match["permalink"], referer=api)
            if not main_html:
                return result
            from bs4 import BeautifulSoup

            async def push_hosted(source: str) -> None:
                """Follow a V-Cloud or G-Direct link, extract stream URLs."""
                try:
                    page_html = await _cf_get(source, referer=api)
                    if not page_html:
                        return
                    for m in re.finditer(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', page_html):
                        url = m.group(1)
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-018 VegaMovies",
                        ))
                except Exception:
                    pass

            soup = BeautifulSoup(main_html, "html.parser")

            if data.season is None:
                pages: set[str] = set()
                for btn in soup.select("button.dwd-button"):
                    h = btn.parent.get("href") if btn.parent else None
                    if h:
                        pages.add(h)
                for page in pages:
                    page_html = await _cf_get(page, referer=api)
                    if not page_html:
                        continue
                    ps = BeautifulSoup(page_html, "html.parser")
                    for btn in ps.select("button.btn"):
                        if re.search(r"V-Cloud", btn.get_text(), re.I):
                            h = btn.parent.get("href") if btn.parent else None
                            if h:
                                await push_hosted(h)
            else:
                season_re = re.compile(rf"Season {data.season}", re.I)
                link_re = re.compile(r"(V-Cloud|Single|Episode)", re.I)
                pages_tv: set[str] = set()
                for tag in soup.select("h3,h5"):
                    if not season_re.search(tag.get_text()):
                        continue
                    sib = tag.find_next_sibling()
                    while sib and sib.name not in ("h3", "h5", "h4"):
                        for a in sib.select("a"):
                            if link_re.search(a.get_text()):
                                h = a.get("href")
                                if h:
                                    pages_tv.add(h)
                        sib = sib.find_next_sibling()

                ep_re = re.compile(rf"Episodes?\s*:\s*{data.episode}", re.I)
                for page in pages_tv:
                    page_html = await _cf_get(page, referer=api)
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
                                        await push_hosted(h)
                                sib = sib.find_next_sibling()
                            break
        except Exception:
            pass
        return result
