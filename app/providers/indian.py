"""
Indian / multi-language scraper providers — all Cloudflare-gated (FlareSolverr needed):
  VegaMovies, HdHub4u, FourKHdHub, RogMovies, MultiMovies,
  Movies4u, UhdMovies, MoviesMod, TopMovies, Bollyflix, CineMacity
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import quote

from app.models import LinkData, ExtractorResult, Stream
from app.providers.base import Provider
from app.utils.http import safe_get, app, UA
from app.utils.hostextractors import load_extractor


# ── Domain registry (rotate occasionally) ────────────────────────────────────

_DOMAINS: dict[str, str] = {
    "vegamovies": "https://vegamovies.mov",
    "hdhub4u": "https://hdhub4u.skin",
    "fourkhdhub": "https://4kmovieshd.com",
    "rogmovies": "https://rogmovies.dev",
    "multimovies": "https://multimovies.beauty",
    "movies4u": "https://movies4u.photo",
    "uhdmovies": "https://uhdmovies.online",
    "moviesmod": "https://moviesmod.best",
    "topmovies": "https://topmovies.boo",
    "bollyflix": "https://bollyflix.rodeo",
    "cinemacity": "https://cinemacity.ink",
}

_VEGA_HEADERS: dict = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": UA,
    "cookie": "xla=s4t",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ── VegaMovies ────────────────────────────────────────────────────────────────

class VegaMoviesProvider(Provider):
    id = "vegamovies"
    name = "VegaMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        api = _DOMAINS.get("vegamovies", "")
        imdb = data.imdb_id
        if not imdb or not api:
            return result

        try:
            season = data.season
            episode = data.episode

            async def search(query: str) -> list[dict]:
                url = f"{api}/search.php?q={query.replace(' ', '%20')}"
                r = await safe_get(url, referer=api, headers=_VEGA_HEADERS, cloudflare=True)
                j = r.json() if r else None
                return [h.get("document", {}) for h in (j or {}).get("hits", [])]

            docs = await search(imdb)
            match = next((d for d in docs if (d.get("imdb_id") or "").lower() == imdb.lower()), None)
            if not match and data.title:
                docs = await search(data.title)
                match = next(
                    (d for d in docs if data.title.lower() in (d.get("post_title") or "").lower()),
                    docs[0] if docs else None,
                )
            if not match or not match.get("permalink"):
                return result

            main = await safe_get(api + match["permalink"], referer=api, headers=_VEGA_HEADERS, cloudflare=True)
            if not main or not main.is_successful:
                return result
            main_soup = main.document

            async def push_hosted(src_url: str) -> None:
                r = await load_extractor(src_url, api, "VegaMovies")
                result.streams.extend([Stream(**{**s.model_dump()}) for s in r.streams])
                result.subtitles.extend(r.subtitles)

            if season is None:
                # Movies: button.dwd-button -> intermediate -> V-Cloud
                pages: set[str] = set()
                for btn in main_soup.find_all("button", class_="dwd-button"):
                    href = (btn.parent or {}).get("href") if hasattr(btn, "parent") else None
                    if href:
                        pages.add(href)
                for page_url in pages:
                    try:
                        doc = (await safe_get(page_url, referer=api, headers=_VEGA_HEADERS, cloudflare=True)).document
                        for btn in doc.find_all("button", class_="btn"):
                            if re.search(r"V-Cloud", btn.get_text(), re.I):
                                parent = btn.parent
                                href = parent.get("href") if parent else None
                                if href:
                                    await push_hosted(href)
                    except Exception:
                        pass
            else:
                # TV: navigate h3/h5 blocks
                season_re = re.compile(f"Season {season}", re.I)
                link_re = re.compile(r"V-Cloud|Single|Episode", re.I)
                pages: set[str] = set()
                for header in main_soup.find_all(["h3", "h5"]):
                    ht = header.get_text()
                    if not (season_re.search(ht) or re.search(r"Episode", ht, re.I)):
                        continue
                    sib = header.find_next_sibling()
                    while sib and sib.name not in ("h3", "h5", "h4"):
                        for a in sib.find_all("a", href=True):
                            if link_re.search(a.get_text()):
                                pages.add(a["href"])
                        sib = sib.find_next_sibling()

                ep_re = re.compile(rf"Episodes?\s*:\s*{episode}", re.I)
                for page_url in pages:
                    try:
                        doc = (await safe_get(page_url, referer=api, headers=_VEGA_HEADERS, cloudflare=True)).document
                        ep_node = None
                        for h4 in doc.find_all("h4"):
                            if ep_re.search(h4.get_text()):
                                ep_node = h4
                                break
                        if not ep_node:
                            continue
                        for a in ep_node.find_next_sibling().find_all("a", href=True):
                            if link_re.search(a.get_text()):
                                await push_hosted(a["href"])
                    except Exception:
                        pass

        except Exception:
            pass
        return result


# ── HdHub4u ───────────────────────────────────────────────────────────────────

_SEARCH_URL = "https://search.pingora.fyi/collections/post/documents/search"
_QUALITY_RE = re.compile(r"480|720|1080|2160|4K", re.I)


class HdHub4uProvider(Provider):
    id = "hdhub4u"
    name = "HDHub4u"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        title = data.title
        if not title:
            return result

        try:
            base = _DOMAINS.get("hdhub4u", "")
            norm_title = _norm(title)

            search_url = (
                f"{_SEARCH_URL}?q={re.sub(chr(32), '+', title)}"
                f"&query_by=post_title,category&query_by_weights=4,2"
                f"&sort_by=sort_by_date:desc&limit=20&highlight_fields=none"
                f"&use_cache=true&page=1"
            )
            resp = await safe_get(search_url, referer=base)
            j = resp.json() if resp else None
            hits: list = (j or {}).get("hits", [])
            if not hits:
                return result

            season_text = f"season {data.season}" if data.season is not None else None
            posts: list[str] = []
            for hit in hits:
                doc = hit.get("document", {})
                post_title = (doc.get("post_title") or "").lower()
                raw_permalink = doc.get("permalink") or ""
                permalink = raw_permalink if raw_permalink.startswith("http") else (base or "") + raw_permalink
                if not post_title or not permalink:
                    continue
                clean = _norm(post_title)
                if data.season is not None:
                    if clean.find(norm_title) >= 0 and season_text and season_text in post_title:
                        posts.append(permalink)
                elif data.year:
                    if clean.find(norm_title) >= 0 and str(data.year) in post_title:
                        posts.append(permalink)
                else:
                    if clean.find(norm_title) >= 0:
                        posts.append(permalink)

            # Narrow by IMDB link if possible
            if data.imdb_id and posts:
                narrowed: list[str] = []
                async def check_imdb(post_url: str) -> None:
                    try:
                        doc = (await safe_get(post_url, cloudflare=True)).document
                        if doc.find("a", href=re.compile(f"imdb\\.com/title/{data.imdb_id}")):
                            narrowed.append(post_url)
                    except Exception:
                        pass
                await asyncio.gather(*[check_imdb(p) for p in posts[:5]])
                if narrowed:
                    posts = narrowed

            for post_url in posts[:3]:
                try:
                    post_doc = (await safe_get(post_url, cloudflare=True)).document
                    if data.season is None:
                        # Movies: h3/h4 quality links
                        for tag in post_doc.find_all(["h3", "h4"]):
                            for a in tag.find_all("a", href=True):
                                if _QUALITY_RE.search(a.get_text()):
                                    href = a["href"]
                                    if "id=" in href:
                                        # redirect resolve
                                        r2 = await app.get(href, referer=post_url, max_redirects=0)
                                        loc = r2.response_headers.get("location", "")
                                        if loc:
                                            href = loc
                                    r = await load_extractor(href, post_url, "HDHub4u")
                                    result.streams.extend(r.streams)
                                    result.subtitles.extend(r.subtitles)
                    else:
                        # TV: find season block -> episode block
                        ep_re = re.compile(rf"[Ee]pisode\s*0*{data.episode}\b")
                        for h3 in post_doc.find_all("h3"):
                            sib = h3.find_next_sibling()
                            while sib and sib.name not in ("h3",):
                                for a in sib.find_all("a", href=True):
                                    if ep_re.search(a.get_text()):
                                        # episode page
                                        ep_doc = (await safe_get(a["href"], cloudflare=True)).document
                                        for tag in ep_doc.find_all(["h3", "h4", "h5"]):
                                            for a2 in tag.find_all("a", href=True):
                                                r = await load_extractor(a2["href"], a["href"], "HDHub4u")
                                                result.streams.extend(r.streams)
                                                result.subtitles.extend(r.subtitles)
                                sib = sib.find_next_sibling()
                except Exception:
                    pass
        except Exception:
            pass
        return result


# ── Generic CF-scraper base ────────────────────────────────────────────────────

class _GenericCfProvider(Provider):
    _domain_key: str = ""
    _quality_re = re.compile(r"480p|720p|1080p|2160p|4K", re.I)

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        base = _DOMAINS.get(self._domain_key, "")
        if not base or not data.title:
            return result
        try:
            # Simple search
            q = data.title.replace(" ", "+")
            sr = await safe_get(f"{base}/?s={q}", referer=base, cloudflare=True)
            if not sr:
                return result
            soup = sr.document
            # Find matching post link
            title_norm = _norm(data.title)
            post_url: Optional[str] = None
            for a in soup.find_all("a", href=True):
                if _norm(a.get_text()).find(title_norm) >= 0:
                    post_url = a["href"]
                    break
            if not post_url:
                return result
            post_doc = (await safe_get(post_url, referer=base, cloudflare=True)).document
            for a in post_doc.find_all("a", href=True):
                if self._quality_re.search(a.get_text()) or re.search(r"download|server", a.get_text(), re.I):
                    r = await load_extractor(a["href"], post_url, self.name)
                    result.streams.extend(r.streams)
                    result.subtitles.extend(r.subtitles)
        except Exception:
            pass
        return result


# FourKHdHubProvider — full implementation below (title-matched scraper)


class RogMoviesProvider(_GenericCfProvider):
    id = "rogmovies"
    name = "RogMovies"
    _domain_key = "rogmovies"
    kinds = ["movie", "series", "asian"]


class MultiMoviesProvider(_GenericCfProvider):
    id = "multimovies"
    name = "MultiMovies"
    _domain_key = "multimovies"
    kinds = ["movie", "series", "asian"]


# Movies4uProvider — full implementation below (IMDB-verified scraper)


class UhdMoviesProvider(_GenericCfProvider):
    id = "uhdmovies"
    name = "UHDMovies"
    _domain_key = "uhdmovies"
    kinds = ["movie", "series", "asian"]


class MoviesModProvider(_GenericCfProvider):
    id = "moviesmod"
    name = "MoviesMod"
    _domain_key = "moviesmod"
    kinds = ["movie", "series", "asian"]


class TopMoviesProvider(_GenericCfProvider):
    id = "topmovies"
    name = "TopMovies"
    _domain_key = "topmovies"
    kinds = ["movie", "series", "asian"]


class BollyflixProvider(_GenericCfProvider):
    id = "bollyflix"
    name = "Bollyflix"
    _domain_key = "bollyflix"
    kinds = ["movie", "series", "asian"]


class CineMacityProvider(_GenericCfProvider):
    id = "cinemacity"
    name = "CineMacity"
    _domain_key = "cinemacity"
    kinds = ["movie", "series", "asian"]


# ══════════════════════════════════════════════════════════════════
# 4kHDHub
# ══════════════════════════════════════════════════════════════════
#
# Flow (ported from StreamPlay fourkhdhub.ts):
#   1. GET <domain>/?s=<title> (CF-gated) → div.card-grid > a.movie-card
#   2. Match card by normalized title (+ year). Open matched href.
#   3. movie:  div.download-item a → getRedirectLinks → load_extractor
#      tv:     div.episode-download-item matching "Sxx[Exx]" → div.episode-links > a → load_extractor
#
# Domain fetched from the shared _DOMAINS dict (key: "fourkhdhub") — update there on rotation.

def _pad2(n) -> str:
    return f"0{n}" if n is not None and int(n) < 10 else str(n) if n is not None else ""


class FourKHdHubProvider(Provider):
    id = "fourkhdhub"
    name = "4kHDHub"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        domain = _DOMAINS.get("fourkhdhub", "")
        title = (data.title or "").strip()
        if not domain or not title:
            return result
        try:
            norm_title = _norm(title)
            year_str = str(data.year) if data.year else None

            search_res = await safe_get(
                f"{domain}/?s={quote(title)}",
                cloudflare=True,
            )
            if not search_res or not search_res.is_successful:
                return result
            soup = search_res.document

            cards = soup.find_all("a", class_="movie-card")

            def card_text(el) -> str:
                content = el.find("div", class_="movie-card-content")
                return _norm(content.get_text() if content else "")

            matched = None
            for el in cards:
                ct = card_text(el)
                if norm_title in ct and (year_str is None or year_str in ct):
                    matched = el
                    break
            if not matched:
                for el in cards:
                    if norm_title in card_text(el):
                        matched = el
                        break
            if not matched:
                return result

            href = matched.get("href", "")
            page_url = href if href.startswith("http") else f"{domain}{href}"
            doc_res = await safe_get(page_url, cloudflare=True)
            if not doc_res or not doc_res.is_successful:
                return result
            doc = doc_res.document

            hrefs: list[str] = []
            if data.season is None:
                for a in doc.find_all("a"):
                    parent = a.find_parent("div", class_="download-item")
                    if parent:
                        h = a.get("href", "")
                        if h:
                            hrefs.append(h)
            else:
                s_text = f"S{_pad2(data.season)}"
                e_text = f"E{_pad2(data.episode)}" if data.episode is not None else None
                for item in doc.find_all("div", class_="episode-download-item"):
                    item_text = item.get_text()
                    if s_text.lower() not in item_text.lower():
                        continue
                    if e_text and e_text.lower() not in item_text.lower():
                        continue
                    for a in (item.find("div", class_="episode-links") or item).find_all("a"):
                        h = a.get("href", "")
                        if h:
                            hrefs.append(h)

            async def resolve(href: str) -> None:
                try:
                    from app.utils.common import get_redirect_links
                    source = await get_redirect_links(href) or href
                    hosted = await load_extractor(source, "")
                    for s in hosted.streams:
                        s.server = f"4kHDHub {s.server}"
                        result.streams.append(s)
                    result.subtitles.extend(hosted.subtitles)
                except Exception:
                    pass

            await asyncio.gather(*[resolve(h) for h in hrefs])
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# Movies4u
# ══════════════════════════════════════════════════════════════════
#
# Flow (ported from StreamPlay movies4u.ts):
#   1. GET <domain>/?s=<title year> → article h2 a / article h3 a candidate posts
#   2. Each post: verify IMDB id from "p a:contains(IMDb Rating)" href
#   3. movie:  div.download-links-div a.btn → inner page → div.downloads-btns-div a.btn → load_extractor
#      tv:     div.downloads-btns-div whose prev sibling contains "Season N" →
#              first non-zip a.btn → episode page → Nth downloads-btns-div → a.btn → load_extractor
#
# Requires IMDB id for verification — skips silently if unavailable.

class Movies4uProvider(Provider):
    id = "movies4u"
    name = "Movies4u"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        domain = _DOMAINS.get("movies4u", "")
        want_imdb = data.imdb_id
        if not domain or not want_imdb:
            return result
        try:
            query = f"{data.title or ''} {data.year or ''}".strip()
            search_res = await safe_get(
                f"{domain}/?s={quote(query)}",
                cloudflare=True,
            )
            if not search_res or not search_res.is_successful:
                return result
            soup = search_res.document

            post_urls: list[str] = []
            for tag in ("h2", "h3"):
                for a in soup.find_all(tag):
                    link = a.find("a")
                    if link:
                        h = link.get("href", "")
                        if h and h not in post_urls:
                            post_urls.append(h)

            host_urls: set[str] = set()

            for post_url in post_urls:
                post_res = await safe_get(post_url, cloudflare=True)
                if not post_res or not post_res.is_successful:
                    continue
                post_doc = post_res.document

                # Verify IMDB id
                imdb_a = post_doc.find("a", string=re.compile("IMDb Rating", re.I))
                if not imdb_a:
                    continue
                imdb_href = imdb_a.get("href", "")
                parts = imdb_href.split("title/")
                extracted_imdb = parts[1].split("/")[0] if len(parts) > 1 else ""
                if extracted_imdb != want_imdb:
                    continue

                if data.season is None:
                    # Movie: download-links-div → inner page
                    dl_div = post_doc.find("div", class_="download-links-div")
                    inner_a = dl_div.find("a", class_="btn") if dl_div else None
                    inner_url = inner_a.get("href", "") if inner_a else ""
                    if not inner_url:
                        continue
                    inner_res = await safe_get(inner_url, cloudflare=True)
                    if not inner_res or not inner_res.is_successful:
                        continue
                    inner_doc = inner_res.document
                    for a in inner_doc.find_all("a", class_="btn"):
                        h = a.get("href", "")
                        if h:
                            host_urls.add(h)
                else:
                    # TV: find "Season N" block
                    for block in post_doc.find_all("div", class_="downloads-btns-div"):
                        prev = block.find_previous_sibling()
                        header_text = prev.get_text() if prev else ""
                        if not re.search(rf"Season\s+{data.season}", header_text, re.I):
                            continue
                        season_link = ""
                        for a in block.find_all("a", class_="btn"):
                            if not re.search(r"zip", a.get_text(), re.I):
                                season_link = a.get("href", "")
                                break
                        if not season_link:
                            continue
                        ep_res = await safe_get(season_link, cloudflare=True)
                        if not ep_res or not ep_res.is_successful:
                            continue
                        ep_doc = ep_res.document
                        ep_blocks = ep_doc.find_all("div", class_="downloads-btns-div")
                        ep_idx = (data.episode or 1) - 1
                        if 0 <= ep_idx < len(ep_blocks):
                            for a in ep_blocks[ep_idx].find_all("a", class_="btn"):
                                h = a.get("href", "")
                                if h:
                                    host_urls.add(h)

            async def resolve(host_url: str) -> None:
                try:
                    hosted = await load_extractor(host_url, "")
                    for s in hosted.streams:
                        s.server = f"Movies4u {s.server}"
                        result.streams.append(s)
                    result.subtitles.extend(hosted.subtitles)
                except Exception:
                    pass

            await asyncio.gather(*[resolve(u) for u in host_urls])
        except Exception:
            pass
        return result
