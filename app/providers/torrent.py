"""
Torrent-mode providers for Movies, TV, and Anime.

Magnets are returned as stream URLs that the mobile app can route to the
/api/v1/torrent/stream endpoint for actual playback (WebTorrent P2P) or
to /api/v1/torrent/http for debrid-cached direct links.

Debrid-cached releases (via Torrentio + RealDebrid/AllDebrid/TorBox) are
ranked first — they stream from the debrid CDN with no buffering lag.

Anime uses AnimeTosho + Nyaa + Torznab (Jackett/Prowlarr, cat 5070).
Movies use YTS + Torrentio + Apibay + Torznab (cat 2000).
TV uses EZTV + Torrentio + Apibay + Torznab (cat 5000).
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Optional

from app.models import ContentKind, ExtractorResult, LinkData, Stream
from app.providers.base import Provider, register
from app.utils.torrent_search import (
    TRelease,
    quality_of,
    score_video,
    search_apibay,
    search_eztv,
    search_nyaa,
    search_torrentio,
    search_torznab,
    search_yts,
    short_label,
    sxe,
    title_similarity,
    title_variants,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_t(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _title_matches(rel_title: str, wants: list[str]) -> bool:
    """Accept a release if ANY title variant matches (exact substring OR fuzzy)."""
    r = _norm_t(rel_title)
    for w in wants:
        wn = _norm_t(w.split(":")[0].split("(")[0])
        if len(wn) >= 3 and wn in r:
            return True
        if len(w.split()) >= 3 and title_similarity(w, rel_title) >= 0.85:
            return True
    return False


def _to_streams(
    rels: list[TRelease],
    wants: list[str],
    year: int = None,
    ep_re: re.Pattern = None,
) -> list[Stream]:
    """Rank, deduplicate, and convert TRelease list to Stream list."""
    matched = [r for r in rels if _title_matches(r.title, wants)]
    pool = matched if matched else rels

    scored = []
    for r in pool:
        # Skip dead swarms (but not debrid-cached)
        if not r.cached and (r.seeders or 0) <= 0:
            continue
        s = score_video(r, year)
        if ep_re and ep_re.search(r.title):
            s += 4
        if s <= 0:
            continue
        scored.append((r, s))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate by URL (cached) or first 60 chars of magnet (P2P)
    seen: set[str] = set()
    deduped = []
    for r, _ in scored:
        key = (r.url or r.magnet)[:60]
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    deduped = deduped[:6]

    streams = []
    for r in deduped:
        if r.cached and r.url:
            # Debrid-cached: encode URL for the torrent/http passthrough endpoint
            encoded = urllib.parse.quote(
                urllib.parse.b64encode(r.url.encode()).decode() if hasattr(urllib.parse, "b64encode") else r.url,
                safe="",
            )
            streams.append(Stream(
                server=f"Torrent · {short_label(r.title)} · Cached ⚡",
                link=f"/api/v1/torrent/http?url={encoded}",
                type="mp4",
                quality=quality_of(r.title),
                debrid=True,
            ))
        else:
            # P2P magnet: encode for the torrent/stream endpoint
            encoded_m = urllib.parse.quote(r.magnet, safe="")
            streams.append(Stream(
                server=f"Torrent · {short_label(r.title)} · {r.seeders}⬆",
                link=f"/api/v1/torrent/stream?magnet={encoded_m}",
                type="mp4",
                quality=quality_of(r.title),
            ))
    return streams


# ── AnimeTosho search ─────────────────────────────────────────────────────────

import aiohttp

_TOSHO_FEED = "https://feed.animetosho.org/json"
_GOOD_GROUPS = re.compile(
    r"\b(subsplease|erai-raws|horriblesubs|ember|judas|asw|cerberus|nandesu|commie|dkb|nep_blanc|yameii)\b",
    re.I,
)
_DUB_RE = re.compile(r"\b(dual[\s._-]?audio|dual|dub|multi[\s._-]?audio|eng[\s._-]?dub)\b", re.I)


async def _search_tosho(q: str) -> list[TRelease]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_TOSHO_FEED, params={"q": q, "only_tor": 1}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                else:
                    return []
        arr = data if isinstance(data, list) else []
        out = []
        for item in arr:
            magnet = item.get("magnet_uri", "")
            if not magnet:
                ih = item.get("info_hash") or item.get("infohash")
                if ih:
                    from app.utils.torrent_search import magnet_from_hash
                    magnet = magnet_from_hash(ih, item.get("torrent_name") or item.get("title") or "")
            if not magnet:
                continue
            out.append(TRelease(
                title=str(item.get("title") or item.get("torrent_name") or ""),
                magnet=magnet,
                size=int(item.get("total_size") or 0),
                seeders=int(item.get("seeders") or item.get("num_seeders") or 0),
                files=int(item.get("num_files") or 1),
            ))
        return out
    except Exception:
        return []


def _score_anime_release(r: TRelease, episode: int, want_dub: bool) -> float:
    t = r.title
    s: float = 0
    if re.search(r"\b1080p?\b", t, re.I):
        s += 4
    elif re.search(r"\b720p?\b", t, re.I):
        s += 2
    elif re.search(r"\b2160p|4k\b", t, re.I):
        s += 1
    if _GOOD_GROUPS.search(t):
        s += 3
    if re.search(r"\b(h[\s._-]?264|x264|avc)\b", t, re.I):
        s += 2    # browser-friendly codec
    if re.search(r"\b(h[\s._-]?265|x265|hevc)\b", t, re.I):
        s -= 1    # needs transcode
    # Episode match
    ep_pat = re.compile(
        rf"(?:^|[\s\-._([])(?:e|ep|episode\s*)?0*{episode}(?:[\s\-._)\]v]|$)", re.I
    )
    is_batch = r.files > 3 or bool(re.search(r"\b\d{1,3}\s*[-~]\s*\d{1,3}\b", t)) or bool(re.search(r"\b(batch|complete|season)\b", t, re.I))
    if ep_pat.search(t) and not is_batch:
        s += 6
    elif is_batch:
        s += 2
    if want_dub:
        if _DUB_RE.search(t):
            s += 5
        else:
            s -= 3
    elif _DUB_RE.search(t):
        s -= 1
    s += min(3.0, (len(str(r.seeders or 0)) - 1) * 1.5)
    return s


async def _anime_releases(titles: list[str], episode: int, want_dub: bool) -> list[TRelease]:
    import asyncio

    seen: set[str] = set()
    out: list[TRelease] = []

    p2 = lambda n: str(n).zfill(2)

    for base in titles[:3]:
        if want_dub:
            queries = [
                f"{base} {p2(episode)} dual audio",
                f"{base} dual audio",
                f"{base} {p2(episode)}",
                f"{base} {episode}",
                base,
            ]
        else:
            queries = [
                f"{base} {p2(episode)}",
                f"{base} - {p2(episode)}",
                f"{base} {episode}",
                base,
            ]

        for q in queries:
            tosho, nyaa, torznab = await asyncio.gather(
                _search_tosho(q),
                search_nyaa(q),
                search_torznab(q, "5070"),
            )
            for r in [*tosho, *nyaa, *torznab]:
                k = (re.search(r"btih:([a-z0-9]+)", r.magnet, re.I) or [None, r.magnet[:40]])[1].lower()
                if k not in seen:
                    seen.add(k)
                    out.append(r)

            good = [r for r in out if _score_anime_release(r, episode, want_dub) >= 8]
            if len(good) >= 3:
                break

        if out:
            break

    return out


# ── Provider implementations ──────────────────────────────────────────────────

class TorrentMovieProvider(Provider):
    id = "torrent-movie"
    name = "Torrent (Movies)"
    kinds = ["movie"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        if not data.title:
            return ExtractorResult()
        wants = title_variants(data.title, data.org_title)
        import asyncio
        tasks = [
            search_yts(data.imdb_id or "", data.title),
            search_torrentio("movie", data.imdb_id or ""),
            search_torznab(f"{data.title} {data.year or ''}".strip(), "2000"),
            *[search_apibay(f"{w} {data.year or ''}".strip(), "201,207") for w in wants[:2]],
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        rels: list[TRelease] = []
        for r in results:
            if isinstance(r, list):
                rels.extend(r)
        return ExtractorResult(streams=_to_streams(rels, wants, data.year))


class TorrentTvProvider(Provider):
    id = "torrent-tv"
    name = "Torrent (TV)"
    kinds = ["series"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        if not data.title:
            return ExtractorResult()
        s = data.season or 1
        e = data.episode or 1
        tag = sxe(s, e)
        ep_re = re.compile(tag, re.I)
        wants = title_variants(data.title, data.org_title)
        import asyncio
        eztv, torrentio, torznab_ep, *apibay_results = await asyncio.gather(
            search_eztv(data.imdb_id or ""),
            search_torrentio("series", data.imdb_id or "", s, e),
            search_torznab(f"{data.title} {tag}", "5000"),
            *[search_apibay(f"{w} {tag}", "205,208") for w in wants[:2]],
            search_apibay(f"{data.title} Season {s}", "205,208"),
        )
        eztv_hit = [r for r in eztv if (r.season == s and r.episode == e) or ep_re.search(r.title)]
        pack_q = apibay_results[-1] if apibay_results else []
        pack_hit = [r for r in pack_q if re.search(rf"(season\s*0*{s}|s0*{s})\b", r.title, re.I) and not re.search(r"S\d{1,2}E\d{1,2}", r.title, re.I)]
        ep_flat = [r for lst in apibay_results[:-1] for r in lst]
        rels = [*eztv_hit, *ep_flat, *pack_hit, *torznab_ep, *torrentio]
        return ExtractorResult(streams=_to_streams(rels, wants, data.year, ep_re))


class TorrentAnimeProvider(Provider):
    id = "torrent-anime"
    name = "Torrent (Anime)"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        if not data.is_anime or not data.episode:
            return ExtractorResult()
        episode = data.absolute_episode or data.episode or 1
        want_dub = False  # could be a user preference in future

        titles: list[str] = []
        if data.anime_titles:
            titles.extend(data.anime_titles[:3])
        if data.title and data.title not in titles:
            titles.insert(0, data.title)

        rels = await _anime_releases(titles, episode, want_dub)
        if not rels:
            return ExtractorResult()

        scored = sorted(rels, key=lambda r: _score_anime_release(r, episode, want_dub), reverse=True)
        streams = []
        seen: set[str] = set()
        for r in scored[:6]:
            if r.seeders <= 0 and not r.cached:
                continue
            k = (r.url or r.magnet)[:60]
            if k in seen:
                continue
            seen.add(k)
            encoded = urllib.parse.quote(r.magnet, safe="")
            server_label = f"Torrent · {short_label(r.title)} · {r.seeders}⬆"
            streams.append(Stream(
                server=server_label,
                link=f"/api/v1/torrent/stream?magnet={encoded}&ep={episode}",
                type="mp4",
                quality=quality_of(r.title),
            ))
        return ExtractorResult(streams=streams)


# ── Register (disabled by default — enabled when TORRENT_ENABLED=1 in env) ────
import os as _os

if _os.environ.get("TORRENT_ENABLED", "").lower() in ("1", "true", "yes"):
    register(TorrentMovieProvider())
    register(TorrentTvProvider())
    register(TorrentAnimeProvider())
    # EZTV: real JSON API for TV show torrents — no scraping, very reliable
    from app.providers.eztv import EztvProvider
    register(EztvProvider())
