"""
Anime-specific providers:
  - AniZone  (HLS + multi-audio + subtitles, no Cloudflare currently)
  - AniNeko  (anineko.to — packed embeds via FlareSolverr)
  - AnimeNoSub (animenosub.to — English SUB+DUB, plain HTTP)
  - AnimeWorld (Indian dubs + Eng/Jap)
  - AllAnime (allanime.day — ani-cli backend, GraphQL)
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from base64 import b64decode
from typing import Optional

from app.models import LinkData, ExtractorResult, Stream, Subtitle
from app.providers.base import Provider
from app.utils.http import app, safe_get, UA
from app.utils.hostextractors import load_extractor, get_and_unpack


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _overlaps(a: str, b: str) -> bool:
    ta = {w for w in _norm(a).split() if len(w) >= 3}
    tb = {w for w in _norm(b).split() if len(w) >= 3}
    return bool(ta & tb)


def _anime_titles(data: LinkData) -> list[str]:
    """Return search query candidates for anime providers."""
    titles = []
    if data.title:
        titles.append(data.title)
    if data.org_title and data.org_title != data.title:
        titles.append(data.org_title)
    titles.extend(data.anime_titles)
    return list(dict.fromkeys(titles))  # deduplicated, order-preserved


# ══════════════════════════════════════════════════════════════════
# AniZone
# ══════════════════════════════════════════════════════════════════

from app.config import get_settings as _settings

_ANIZONE_BASE = lambda: _settings().anizone_base_url.rstrip("/")


class AniZoneProvider(Provider):
    id = "anizone"
    name = "AniZone"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.is_anime or not data.title:
            return result

        base = _ANIZONE_BASE()
        episode_num = 1 if data.type == "movie" else (data.episode or 1)
        want_season = None if (data.type == "movie" or data.season_resolved) else (data.season or 1)

        async def fetch_html(url: str) -> Optional[str]:
            try:
                res = await app.get(url, timeout=12)
                if res.status == 200 and res.text and "just a moment" not in res.text.lower():
                    return res.text
            except Exception:
                pass
            # FlareSolverr fallback
            from app.utils.http import _flaresolverr_get
            solved = await _flaresolverr_get(url, timeout=60)
            return solved.text if solved else None

        def ep_page_title(html: str) -> str:
            m = re.search(r"<title>([^<]*)</title>", html, re.I)
            if not m:
                return ""
            t = re.sub(r"—\s*AniZone\s*$", "", m.group(1), flags=re.I)
            return re.sub(r"^\s*Episode\s+\d+\s*[-–]\s*", "", t, flags=re.I).strip()

        queries = _anime_titles(data)
        candidates: list[str] = []
        for q in queries:
            html = await fetch_html(f"{base}/anime?search={re.sub(chr(32), '+', q)}")
            if not html:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=re.compile(r"/anime/")):
                href = a.get("href", "")
                abs_href = href if href.startswith("http") else f"{base}{href}"
                if abs_href not in candidates:
                    candidates.append(abs_href)
            if candidates:
                break

        # Try candidates
        for candidate in candidates[:5]:
            try:
                # Build episode URL
                ep_url = f"{candidate.rstrip('/')}/{episode_num}"
                if want_season and want_season > 1:
                    # AniZone may encode season in path; try with s= param too
                    ep_html = await fetch_html(ep_url)
                    if not ep_html:
                        continue
                    page_title = ep_page_title(ep_html)
                    if not _overlaps(data.title, page_title):
                        continue
                else:
                    ep_html = await fetch_html(ep_url)
                    if not ep_html:
                        continue
                    page_title = ep_page_title(ep_html)
                    if not _overlaps(data.title, page_title):
                        continue

                # Extract media player src (master m3u8)
                from bs4 import BeautifulSoup
                ep_soup = BeautifulSoup(ep_html, "lxml")
                player = ep_soup.find("media-player") or ep_soup.find("video")
                if not player:
                    # Look for source in script
                    for sc in ep_soup.find_all("script"):
                        m2 = re.search(r'["\']([^"\']*\.m3u8[^"\']*)["\']', sc.get_text() or "")
                        if m2:
                            result.streams.append(Stream(
                                server="AniZone",
                                link=m2.group(1),
                                type="m3u8",
                                headers={"Referer": base},
                            ))
                    if result.streams:
                        break
                    continue

                src = player.get("src") or player.get("data-src") or ""
                if src:
                    result.streams.append(Stream(
                        server="AniZone",
                        link=src if src.startswith("http") else f"{base}{src}",
                        type="m3u8",
                        headers={"Referer": base},
                    ))

                # Subtitle tracks
                for track in ep_soup.find_all("track", kind=re.compile(r"subtitles|captions", re.I)):
                    src_url = track.get("src") or ""
                    label = track.get("label") or track.get("srclang") or "unknown"
                    if src_url:
                        abs_src = src_url if src_url.startswith("http") else f"{base}{src_url}"
                        result.subtitles.append(Subtitle(language=label, url=abs_src))

                if result.streams:
                    break
            except Exception:
                pass

        return result


# ══════════════════════════════════════════════════════════════════
# AnimeNoSub
# ══════════════════════════════════════════════════════════════════

class AnimeNoSubProvider(Provider):
    id = "animenosub"
    name = "AnimeNoSub"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.is_anime or not data.title:
            return result

        base = "https://animenosub.to"
        episode_num = data.episode or 1

        try:
            queries = _anime_titles(data)
            post_url: Optional[str] = None
            for q in queries:
                sr = await app.get(f"{base}/?s={re.sub(chr(32), '+', q)}", referer=base)
                if not sr or not sr.is_successful:
                    continue
                soup = sr.document
                for a in soup.find_all("article", class_=True):
                    title_el = a.find(["h2", "h3"])
                    if title_el and _overlaps(data.title, title_el.get_text()):
                        for link in a.find_all("a", href=True):
                            if "/anime/" in link["href"]:
                                post_url = link["href"]
                                break
                if post_url:
                    break

            if not post_url:
                return result

            # Get episode list
            anime_page = await app.get(post_url, referer=base)
            if not anime_page or not anime_page.is_successful:
                return result
            soup = anime_page.document

            # Find episode link
            ep_link: Optional[str] = None
            ep_re = re.compile(rf"\b0*{episode_num}\b")
            for a in soup.find_all("a", href=True):
                text = a.get_text().strip()
                if ep_re.search(text) and "episode" in (a.get("class") or [""])[0].lower():
                    ep_link = a["href"]
                    break

            if not ep_link:
                # Try direct episode URL pattern
                slug = post_url.rstrip("/").split("/")[-1]
                ep_link = f"{base}/watch/{slug}-episode-{episode_num}"

            ep_page = await app.get(ep_link, referer=post_url)
            if not ep_page or not ep_page.is_successful:
                return result

            # Look for megaplay or direct m3u8
            ep_text = ep_page.text
            m = re.search(r'["\']([^"\']*megaplay[^"\']*)["\']|["\']([^"\']*\.m3u8[^"\']*)["\']', ep_text)
            if m:
                embed_url = m.group(1) or m.group(2) or ""
                if "megaplay" in embed_url:
                    # Resolve the megaplay embed
                    mp_res = await app.get(embed_url, referer=ep_link)
                    if mp_res:
                        m2 = re.search(r'["\']([^"\']+\.m3u8[^"\']*)["\']', mp_res.text)
                        if m2:
                            result.streams.append(Stream(
                                server="AnimeNoSub",
                                link=m2.group(1),
                                type="m3u8",
                                headers={"Referer": embed_url},
                            ))
                elif ".m3u8" in embed_url:
                    result.streams.append(Stream(
                        server="AnimeNoSub",
                        link=embed_url,
                        type="m3u8",
                        headers={"Referer": ep_link},
                    ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# AnimeWorld
# ══════════════════════════════════════════════════════════════════

class AnimeWorldProvider(Provider):
    """
    AnimeWorld — serves anime INCLUDING Western cartoons + dubbed content.
    Intentionally NOT kind-gated (serves ALL kinds).
    """
    id = "animeworld"
    name = "AnimeWorld"
    # kinds = None  → serves all

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.title:
            return result

        base = "https://www.animeworld.so"
        episode_num = data.episode or 1

        try:
            queries = _anime_titles(data) if data.is_anime else [data.title]
            post_url: Optional[str] = None
            for q in queries:
                sr = await safe_get(
                    f"{base}/search?keyword={re.sub(chr(32), '+', q)}",
                    referer=base,
                )
                if not sr or not sr.is_successful:
                    continue
                soup = sr.document
                for a in soup.find_all("a", href=re.compile(r"/play/")):
                    if _overlaps(data.title, a.get_text()):
                        post_url = a["href"] if a["href"].startswith("http") else f"{base}{a['href']}"
                        break
                if post_url:
                    break

            if not post_url:
                return result

            # Navigate to episode
            if data.type != "movie" and episode_num > 1:
                ep_url = re.sub(r"/1$", f"/{episode_num}", post_url)
            else:
                ep_url = post_url

            ep_res = await safe_get(ep_url, referer=base)
            if not ep_res or not ep_res.is_successful:
                return result

            # Look for video sources in page JS
            text = ep_res.text
            for m in re.finditer(r'["\']([^"\']+\.m3u8[^"\']*)["\']', text):
                url = m.group(1)
                if url.startswith("http"):
                    result.streams.append(Stream(
                        server="AnimeWorld",
                        link=url,
                        type="m3u8",
                        headers={"Referer": base},
                    ))

            # zephyrflick embed pattern
            m_embed = re.search(r'src="(https://zephyrflick[^"]+)"', text)
            if m_embed:
                embed_res = await app.get(m_embed.group(1), referer=ep_url)
                if embed_res:
                    unpacked = get_and_unpack(embed_res.text)
                    source = unpacked if unpacked else embed_res.text
                    m2 = re.search(r'["\']([^"\']+\.m3u8[^"\']*)["\']', source)
                    if m2:
                        result.streams.append(Stream(
                            server="AnimeWorld (Zephyr)",
                            link=m2.group(1),
                            type="m3u8",
                            headers={"Referer": m_embed.group(1)},
                        ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# AllAnime (allanime.day — ani-cli backend)
# ══════════════════════════════════════════════════════════════════

import hashlib as _hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

_ALLANIME_KEY = _hashlib.sha256(b"Xot36i3lK3:v1").digest()
_AA_API = "https://api.allanime.day/api"
_AA_REFERER = "https://youtu-chan.com"
_AA_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
_AA_HDRS = {"User-Agent": _AA_AGENT, "Referer": _AA_REFERER}
_AA_POST_HDRS = {**_AA_HDRS, "Content-Type": "application/json", "Origin": _AA_REFERER}

_SEARCH_GQL = (
    "query($search:SearchInput $limit:Int $page:Int $translationType:VaildTranslationTypeEnumType "
    "$countryOrigin:VaildCountryOriginEnumType){shows(search:$search limit:$limit page:$page "
    "translationType:$translationType countryOrigin:$countryOrigin){edges{_id name availableEpisodes __typename}}}"
)
_EP_HASH = "d405d0edd690624b66baba3068e0edc3ac90f1597d898a1ec8db4e5c43c00fec"


def _decrypt_tobeparsed(b64: str) -> Optional[str]:
    try:
        buf = b64decode(b64)
        if len(buf) < 29:
            return None
        iv = buf[1:13]
        counter = iv + bytes([0, 0, 0, 2])
        ct = buf[13:len(buf) - 16]
        cipher = Cipher(algorithms.AES(_ALLANIME_KEY), modes.CTR(counter), backend=default_backend())
        dec = cipher.decryptor()
        return (dec.update(ct) + dec.finalize()).decode("utf-8")
    except Exception:
        return None


def _decode_source_url(raw: str) -> Optional[str]:
    if not raw.startswith("--"):
        return None
    hex_str = raw[2:]
    pairs = [hex_str[i:i+2] for i in range(0, len(hex_str), 2)]
    try:
        decoded = "".join(chr(int(p, 16) ^ 0x38) for p in pairs if len(p) == 2)
    except Exception:
        return None
    if not decoded.startswith("/"):
        return None
    return decoded.replace("/clock", "/clock.json")


class AllAnimeProvider(Provider):
    id = "allanime"
    name = "AllAnime"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.is_anime or not data.title:
            return result

        ep_str = str(data.episode or 1)
        queries = _anime_titles(data)

        try:
            # Search
            show_id: Optional[str] = None
            for q in queries:
                search_body = {
                    "variables": {
                        "search": {"query": q, "types": None},
                        "limit": 10,
                        "page": 1,
                        "translationType": "sub",
                        "countryOrigin": "ALL",
                    },
                    "query": _SEARCH_GQL,
                }
                resp = await app.post(
                    _AA_API,
                    body=search_body,
                    headers=_AA_POST_HDRS,
                    content_type="application/json",
                )
                j = resp.json() if resp else None
                edges = (((j or {}).get("data") or {}).get("shows") or {}).get("edges") or []
                for edge in edges:
                    if _overlaps(q, edge.get("name", "")):
                        show_id = edge.get("_id")
                        break
                if show_id:
                    break

            if not show_id:
                return result

            # Fetch episode sources (persisted query)
            ep_body = {
                "variables": {
                    "showId": show_id,
                    "translationType": "sub",
                    "episodeString": ep_str,
                },
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": _EP_HASH,
                    }
                },
            }
            ep_resp = await app.post(
                _AA_API,
                body=ep_body,
                headers=_AA_POST_HDRS,
                content_type="application/json",
            )
            ep_j = ep_resp.json() if ep_resp else None
            episode_data = ((ep_j or {}).get("data") or {}).get("episode") or {}

            # Decrypt tobeparsed blob
            tobeparsed = episode_data.get("tobeparsed") or episode_data.get("sourceUrls")
            source_urls: list[dict] = []
            if isinstance(tobeparsed, str):
                decrypted = _decrypt_tobeparsed(tobeparsed)
                if decrypted:
                    import json
                    try:
                        source_urls = json.loads(decrypted)
                    except Exception:
                        pass
            elif isinstance(tobeparsed, list):
                source_urls = tobeparsed

            async def resolve_source(src: dict) -> None:
                raw_url = src.get("sourceUrl", "")
                path = _decode_source_url(raw_url)
                if not path:
                    return
                clock_url = f"https://allanime.day{path}"
                try:
                    cr = await app.get(clock_url, headers=_AA_HDRS, timeout=10)
                    j = cr.json() if cr else None
                    links = (j or {}).get("links") or []
                    for link in links:
                        mp4_url = link.get("mp4") or link.get("hls") or link.get("src") or ""
                        if mp4_url and mp4_url.startswith("http"):
                            result.streams.append(Stream(
                                server=f"AllAnime [{src.get('sourceName', 'SUB')}]",
                                link=mp4_url,
                                type="m3u8" if ".m3u8" in mp4_url else "mp4",
                                headers={"Referer": _AA_REFERER},
                            ))
                except Exception:
                    pass

            await asyncio.gather(*[resolve_source(s) for s in source_urls])

        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# AniNeko
# ══════════════════════════════════════════════════════════════════

class AniNekoProvider(Provider):
    id = "anineko"
    name = "AniNeko"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.is_anime or not data.title:
            return result

        base = "https://anineko.to"
        ep_num = data.episode or 1

        try:
            queries = _anime_titles(data)
            post_url: Optional[str] = None
            for q in queries:
                sr = await safe_get(f"{base}/?s={re.sub(chr(32), '+', q)}", referer=base, cloudflare=True)
                if not sr:
                    continue
                soup = sr.document
                for a in soup.find_all("a", href=True):
                    if _overlaps(data.title, a.get_text()):
                        post_url = a["href"]
                        break
                if post_url:
                    break

            if not post_url:
                return result

            ep_url = f"{post_url.rstrip('/')}/episode-{ep_num}"
            ep_res = await safe_get(ep_url, referer=base, cloudflare=True)
            if not ep_res or not ep_res.is_successful:
                return result

            # Unpack and extract m3u8
            unpacked = get_and_unpack(ep_res.text)
            source = unpacked if unpacked else ep_res.text
            for m in re.finditer(r'["\']([^"\']+\.m3u8[^"\']*)["\']', source):
                url = m.group(1)
                if url.startswith("http"):
                    result.streams.append(Stream(
                        server="AniNeko",
                        link=url,
                        type="m3u8",
                        headers={"Referer": base},
                    ))
        except Exception:
            pass
        return result
