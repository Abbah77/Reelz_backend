"""
Torrent indexers for torrent-mode providers (movies / TV / anime).

Each returns normalised TRelease objects with a magnet + seeder count.
Callers sort by score and drop dead swarms (0 seeders).

Indexers:
  - YTS         — movies only; superb well-seeded 720p/1080p/2160p (query by IMDb ID)
  - EZTV        — TV episodes (by IMDb ID; carries season/episode fields)
  - Apibay      — The Pirate Bay JSON API, broad catalogue, movies + TV
  - Torrentio   — free public aggregator (wraps YTS/EZTV/RARBG mirrors/1337x/TPB/…);
                  with a debrid key returns DIRECT cached HTTP links (instant playback)
  - Nyaa        — THE anime tracker (RSS); fresh releases
  - Torznab     — meta-indexer gateway (Jackett / Prowlarr) — one call fans out across
                  every configured indexer; env-gated via TORZNAB_URL + TORZNAB_API_KEY

Mirrors StreamPlay's src/utils/torrentSearch.ts, ported to Python/aiohttp.
"""
from __future__ import annotations

import asyncio
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import aiohttp


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TRelease:
    title: str
    magnet: str
    size: int = 0
    seeders: int = 0
    files: int = 1
    season: Optional[int] = None
    episode: Optional[int] = None
    url: Optional[str] = None      # debrid-resolved direct HTTP link (instant playback)
    cached: bool = False           # True when served from a debrid cache


# ── Tracker list ──────────────────────────────────────────────────────────────
# Fresh, broad tracker set for maximum peer discovery.

TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker1.bt.moack.co.kr:80/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "https://tracker.tamersunion.org:443/announce",
]


def magnet_from_hash(info_hash: str, name: str = "") -> str:
    qs = f"xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}"
    qs += "".join(f"&tr={urllib.parse.quote(t)}" for t in TRACKERS)
    return f"magnet:?{qs}"


# ── Debrid config ─────────────────────────────────────────────────────────────

_DEBRID_MAP = [
    ("REALDEBRID_KEY", "realdebrid"),
    ("ALLDEBRID_KEY", "alldebrid"),
    ("TORBOX_KEY", "torbox"),
    ("PREMIUMIZE_KEY", "premiumize"),
    ("DEBRIDLINK_KEY", "debridlink"),
    ("OFFCLOUD_KEY", "offcloud"),
]


def debrid_config() -> str:
    """Return Torrentio URL config segment (e.g. 'realdebrid=KEY'), or '' if none set."""
    for env, provider in _DEBRID_MAP:
        k = os.environ.get(env, "")
        if k:
            return f"{provider}={k}"
    return ""


def debrid_enabled() -> bool:
    return bool(debrid_config())


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def _get_json(session: aiohttp.ClientSession, url: str, params: dict = None, timeout: int = 12) -> Optional[dict | list]:
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return None


async def _get_text(session: aiohttp.ClientSession, url: str, params: dict = None, timeout: int = 12) -> str:
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.text()
    except Exception:
        pass
    return ""


# ── Indexers ──────────────────────────────────────────────────────────────────

async def search_yts(imdb_id: str = "", title: str = "") -> list[TRelease]:
    """YTS — movies only. imdb_id (ttXXXXXXX) gives the most precise match."""
    term = imdb_id or title
    if not term:
        return []
    async with aiohttp.ClientSession() as s:
        data = await _get_json(s, "https://yts.mx/api/v2/list_movies.json", {"query_term": term, "limit": 6})
    movies = (data or {}).get("data", {}).get("movies", []) or []
    out = []
    for m in movies:
        for t in m.get("torrents", []) or []:
            out.append(TRelease(
                title=f"{m.get('title_long', '')} [{t.get('quality', '')}] [{t.get('type', '')}] YTS",
                magnet=magnet_from_hash(t.get("hash", ""), m.get("title_long", "")),
                size=int(t.get("size_bytes") or 0),
                seeders=int(t.get("seeds") or 0),
                files=1,
            ))
    return out


async def search_eztv(imdb_id: str = "") -> list[TRelease]:
    """EZTV — TV episodes by IMDb ID (digits only). Carries season/episode fields."""
    imdb = re.sub(r"^tt", "", imdb_id, flags=re.I)
    if not imdb:
        return []
    async with aiohttp.ClientSession() as s:
        data = await _get_json(s, "https://eztvx.to/api/get-torrents", {"imdb_id": imdb, "limit": 100})
    torrents = (data or {}).get("torrents", []) or []
    out = []
    for t in torrents:
        magnet = t.get("magnet_url", "")
        if not magnet:
            continue
        out.append(TRelease(
            title=str(t.get("title", "")),
            magnet=magnet,
            size=int(t.get("size_bytes") or 0),
            seeders=int(t.get("seeds") or 0),
            files=1,
            season=int(t["season"]) if t.get("season") else None,
            episode=int(t["episode"]) if t.get("episode") else None,
        ))
    return out


async def search_apibay(query: str, cats: str) -> list[TRelease]:
    """The Pirate Bay JSON API. cats e.g. '201,207' (movies) or '205,208' (TV)."""
    if not query.strip():
        return []
    async with aiohttp.ClientSession() as s:
        data = await _get_json(s, "https://apibay.org/q.php", {"q": query, "cat": cats})
    arr = data if isinstance(data, list) else []
    out = []
    for r in arr:
        ih = r.get("info_hash", "")
        name = r.get("name", "")
        if not ih or re.match(r"^0+$", ih) or not name or re.match(r"no results", name, re.I):
            continue
        out.append(TRelease(
            title=str(name),
            magnet=magnet_from_hash(ih, name),
            size=int(r.get("size") or 0),
            seeders=int(r.get("seeders") or 0),
            files=int(r.get("num_files") or 1),
        ))
    return out


async def search_torrentio(
    kind: str,  # "movie" | "series"
    imdb_id: str = "",
    season: int = None,
    episode: int = None,
) -> list[TRelease]:
    """
    Torrentio free public aggregator.
    With a debrid key returns DIRECT cached HTTP links (instant playback).
    Without one, returns infoHashes for P2P streaming.
    """
    if not imdb_id:
        return []
    if kind == "series":
        tid = f"{imdb_id}:{season or 1}:{episode or 1}"
    else:
        tid = imdb_id
    cfg = debrid_config()
    base = f"https://torrentio.strem.fun/{cfg}/stream" if cfg else "https://torrentio.strem.fun/stream"
    async with aiohttp.ClientSession() as s:
        data = await _get_json(s, f"{base}/{kind}/{tid}.json", timeout=15)
    streams = (data or {}).get("streams", []) or []
    out = []
    for st in streams:
        full = str(st.get("title") or "")
        name = full.split("\n")[0] or st.get("name") or "Torrent"
        seeders_match = re.search(r"👤\s*(\d+)", full)
        seeders = int(seeders_match.group(1)) if seeders_match else 0
        if st.get("url"):
            out.append(TRelease(
                title=name,
                magnet="",
                url=str(st["url"]),
                cached=True,
                size=0,
                seeders=seeders or 999,  # cached = no seeder concept; rank high
                files=1,
            ))
        elif st.get("infoHash"):
            out.append(TRelease(
                title=name,
                magnet=magnet_from_hash(st["infoHash"], name),
                size=0,
                seeders=seeders,
                files=1,
            ))
    return out


async def search_nyaa(query: str) -> list[TRelease]:
    """Nyaa (nyaa.si) — THE anime torrent tracker, via RSS. c=1_2 = English-translated anime."""
    if not query.strip():
        return []
    async with aiohttp.ClientSession() as s:
        text = await _get_text(s, "https://nyaa.si/", {"page": "rss", "q": query, "c": "1_2", "f": "0"})
    out = []
    for item_text in text.split("<item>")[1:]:
        title_m = re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</title>", item_text, re.I)
        hash_m = re.search(r"<nyaa:infoHash>([^<]*)</nyaa:infoHash>", item_text, re.I)
        if not title_m or not hash_m:
            continue
        title = title_m.group(1).strip()
        info_hash = hash_m.group(1).strip()
        size_m = re.search(r"<nyaa:size>([^<]*)</nyaa:size>", item_text, re.I)
        seed_m = re.search(r"<nyaa:seeders>([^<]*)</nyaa:seeders>", item_text, re.I)
        out.append(TRelease(
            title=title,
            magnet=magnet_from_hash(info_hash, title),
            size=_parse_size(size_m.group(1) if size_m else ""),
            seeders=int(seed_m.group(1)) if seed_m else 0,
            files=1,
        ))
    return out


def _get_torznab_config() -> tuple[str, str]:
    """Return (url, api_key) from env, trying Torznab → Jackett → Prowlarr."""
    if os.environ.get("TORZNAB_URL") and os.environ.get("TORZNAB_API_KEY"):
        return os.environ["TORZNAB_URL"], os.environ["TORZNAB_API_KEY"]
    if os.environ.get("JACKETT_URL") and os.environ.get("JACKETT_API_KEY"):
        url = os.environ["JACKETT_URL"].rstrip("/") + "/api/v2.0/indexers/all/results/torznab/api"
        return url, os.environ["JACKETT_API_KEY"]
    if os.environ.get("PROWLARR_URL") and os.environ.get("PROWLARR_API_KEY"):
        return os.environ["PROWLARR_URL"], os.environ["PROWLARR_API_KEY"]
    return "", ""


def torznab_enabled() -> bool:
    url, key = _get_torznab_config()
    return bool(url and key)


def _xml_text(s: str) -> str:
    """Strip CDATA wrapper and unescape XML entities."""
    s = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", s)
    return (s
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&apos;", "'"))


async def search_torznab(query: str, cat: str) -> list[TRelease]:
    """
    Torznab meta-indexer (Jackett / Prowlarr).
    Newznab cats: movies=2000, tv=5000, anime=5070.
    """
    base, key = _get_torznab_config()
    if not base or not key or not query.strip():
        return []
    # Bare Jackett base URL → all-indexers Torznab endpoint
    if not re.search(r"torznab|/api\b", base, re.I):
        base = base.rstrip("/") + "/api/v2.0/indexers/all/results/torznab/api"
    async with aiohttp.ClientSession() as s:
        text = await _get_text(s, base, {"apikey": key, "t": "search", "q": query, "cat": cat}, timeout=15)
    out = []
    for item_text in text.split("<item>")[1:]:
        title_m = re.search(r"<title>([\s\S]*?)</title>", item_text, re.I)
        if not title_m:
            continue
        title = _xml_text(title_m.group(1)).strip()
        if not title:
            continue

        def attr(n: str) -> str:
            m = re.search(rf'name="{n}"\s+value="([^"]*)"', item_text, re.I)
            return m.group(1) if m else ""

        enclosure_m = re.search(r'<enclosure[^>]+url="([^"]+)"', item_text, re.I)
        enclosure = enclosure_m.group(1) if enclosure_m else ""
        magnet = attr("magneturl")
        if not magnet and re.match(r"^magnet:", enclosure, re.I):
            magnet = _xml_text(enclosure)
        if not magnet:
            ih = attr("infohash")
            if ih:
                magnet = magnet_from_hash(ih, title)
        if not magnet or not re.match(r"^magnet:", magnet, re.I):
            continue
        size_attr = attr("size")
        size_tag_m = re.search(r"<size>(\d+)</size>", item_text, re.I)
        out.append(TRelease(
            title=title,
            magnet=magnet,
            size=int(size_attr or (size_tag_m.group(1) if size_tag_m else 0) or 0),
            seeders=int(attr("seeders") or 0),
            files=int(attr("files") or 1),
        ))
    return out


# ── Scoring / utility helpers ──────────────────────────────────────────────────

_SIZE_UNITS = {
    "b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
    "kb": 1000, "mb": 1_000_000, "gb": 1_000_000_000, "tb": 1_000_000_000_000,
}


def _parse_size(s: str) -> int:
    m = re.search(r"([\d.]+)\s*(b|[kmgt]i?b)", s or "", re.I)
    if not m:
        return 0
    unit = _SIZE_UNITS.get(m.group(2).lower(), 1)
    return int(float(m.group(1)) * unit)


def score_video(r: TRelease, year: int = None) -> float:
    """Score a release: debrid-cached > quality > codec > seeders; penalise cams."""
    t = r.title
    s: float = 0
    if r.cached:
        s += 10        # debrid-cached = instant, no buffering → rank first
    if re.search(r"\b1080p\b", t, re.I):
        s += 4
    elif re.search(r"\b720p\b", t, re.I):
        s += 2
    elif re.search(r"\b(2160p|4k)\b", t, re.I):
        s += 2
    else:
        s += 1
    if re.search(r"\b(x264|h\.?264|avc)\b", t, re.I):
        s += 2   # browser-friendly codec → no transcode
    if re.search(r"\b(cam|hdcam|ts|telesync|hdts|scr)\b", t, re.I):
        s -= 8   # drop cams / screeners
    s += min(4.0, (len(str(r.seeders or 0)) - 1) * 1.5)  # log-ish seeder bonus
    if year and str(year) in t:
        s += 1
    return s


def quality_of(title: str) -> Optional[str]:
    m = re.search(r"\b(2160p|1080p|720p|480p)\b", title, re.I)
    return m.group(1) if m else None


def short_label(title: str) -> str:
    """Short human label: '1080p WEB-DL RARBG'."""
    q = quality_of(title) or ""
    src_m = re.search(r"\b(WEB[-\s.]?DL|WEBRip|BluRay|BRRip|HDRip|HDTV|DVDRip|REMUX)\b", title, re.I)
    src = src_m.group(1) if src_m else ""
    group_m = re.search(r"[-\s]([A-Za-z0-9]+)$", title)
    group = group_m.group(1) if group_m else ""
    return " ".join(x for x in [q, src, group] if x) or "Torrent"


def title_variants(title: str, org_title: str = None) -> list[str]:
    """Alternate spellings to search — covers subtitle-stripped, and/& swap, etc."""
    out: list[str] = []

    def add(t: str) -> None:
        t = re.sub(r"\s+", " ", t).strip()
        if t and len(t) >= 2 and t.lower() not in [x.lower() for x in out]:
            out.append(t)

    add(title)
    if org_title:
        add(org_title)
    # Drop subtitle after colon or spaced dash (but not intra-word hyphen like Spider-Man)
    add(re.sub(r"(\s*[:\u2013\u2014]|\s+-).*$", "", title))
    add(re.sub(r"\band\b", "&", title, flags=re.I))
    add(re.sub(r"&", "and", title))
    add(re.sub(r"[''.,!?]", "", title))
    return out[:4]


def title_similarity(a: str, b: str) -> float:
    """Token overlap 0..1 — tolerant title match for fuzzy/typo'd names."""
    stopwords = {"the", "a", "an", "of", "and", "to", "in", "season", "part"}

    def tokens(s: str) -> list[str]:
        return [w for w in re.findall(r"[a-z0-9]{2,}", s.lower()) if w not in stopwords]

    ta = tokens(a)
    tb = set(tokens(b))
    if not ta:
        return 0.0
    return sum(1 for t in ta if t in tb) / len(ta)


def sxe(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"
