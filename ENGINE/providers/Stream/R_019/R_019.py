"""
ENGINE/providers/Stream/R-019/R_019.py — HdHub4u (Indian content)

Typesense search (search.pingora.fyi) -> post page -> quality links -> stream.
Ported from Streamplay's HdHub4uProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import get_redirect_link
from ENGINE.tools.flaresolverr import solve_cloudflare

_QUALITY_RE = re.compile(r"480|720|1080|2160|4K", re.I)
_SEARCH_URL = (
    "https://search.pingora.fyi/collections/post/documents/search"
    "?q={q}&query_by=post_title,category&query_by_weights=4,2"
    "&sort_by=sort_by_date:desc&limit=20&highlight_fields=none&use_cache=true&page=1"
)


def _norm_alphanum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


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
    """Try to pull a direct m3u8/mp4 from a hosting page."""
    streams: list[Stream] = []
    html = await _cf_get(source)
    if not html:
        return streams
    for m in re.finditer(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', html):
        url = m.group(1)
        streams.append(Stream(
            url=url,
            type="m3u8" if ".m3u8" in url else "mp4",
            server="R-019 HdHub4u",
        ))
    return streams


class R019Provider(Provider):
    id = "R-019"
    name = "HdHub4u"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result
        try:
            base_url = await get_domain("hdhub4u")
            client = await get_client()
            response = (await client.get(
                _SEARCH_URL.format(q=data.title),
                headers={"User-Agent": UA, "Referer": base_url or ""},
                timeout=15,
            )).json()
            hits: list = response.get("hits") or []
            if not hits:
                return result

            norm_title = _norm_alphanum(data.title)
            season_text = f"season {data.season}" if data.season is not None else None

            posts: list[str] = []
            for hit in hits:
                doc = hit.get("document") or {}
                post_title = (doc.get("post_title") or "").lower()
                raw_link = doc.get("permalink") or ""
                permalink = raw_link if raw_link.startswith("http") else (base_url or "") + raw_link
                if not post_title or not permalink:
                    continue
                clean = _norm_alphanum(post_title)
                if data.season is not None:
                    matches = norm_title in clean and season_text in post_title
                elif data.year is not None:
                    matches = norm_title in clean and str(data.year) in post_title
                else:
                    matches = norm_title in clean
                if matches:
                    posts.append(permalink)

            if not posts:
                return result

            # IMDB narrowing (optional)
            if data.imdb_id:
                narrowed: list[str] = []
                for post_url in posts:
                    html = await _cf_get(post_url)
                    if html and f"imdb.com/title/{data.imdb_id}" in html:
                        narrowed.append(post_url)
                if narrowed:
                    posts = narrowed

            for post_url in posts[:3]:
                html = await _cf_get(post_url)
                if not html:
                    continue
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")

                if data.season is None:
                    for a in soup.select("h3 a, h4 a"):
                        if _QUALITY_RE.search(a.get_text()):
                            link = a.get("href") or ""
                            if not link:
                                continue
                            resolved = (await get_redirect_link(link)) if "id=" in link else link
                            for s in await _load_extractor(resolved or link):
                                result.streams.append(s)
                else:
                    ep_re = re.compile(rf"episode\s*{data.episode}", re.I)
                    current_season_block = False
                    season_re = re.compile(rf"season\s*{data.season}", re.I)
                    for h3 in soup.select("h3"):
                        if season_re.search(h3.get_text()):
                            current_season_block = True
                        elif current_season_block and re.search(r"season\s*\d+", h3.get_text(), re.I):
                            current_season_block = False
                        if not current_season_block:
                            continue
                        sib = h3.find_next_sibling()
                        while sib and sib.name not in ("h3", "h2"):
                            for a in sib.select("a"):
                                if ep_re.search(a.get_text()):
                                    link = a.get("href") or ""
                                    if link:
                                        resolved = (await get_redirect_link(link)) if "id=" in link else link
                                        for s in await _load_extractor(resolved or link):
                                            result.streams.append(s)
                            sib = sib.find_next_sibling()
        except Exception:
            pass
        return result
