"""
managers/download.py — download manager.

Responsibilities:
  - Cache lookup / write
  - Concurrent provider fan-out
  - m3u8 master playlist expansion into per-resolution entries
  - mp4 deduplication by (language, normalised quality)
  - File-size probing for direct mp4/mkv links
  - Provider statistics recording
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from app.cache import cache
from app.cache.keys import download_key
from app.clients.http import get_client, UA
from app.clients.tmdb import enrich_link_data
from app.managers.provider_stats import provider_stats
from app.providers.base import safe_invoke, _TimedOutResult
from app.providers.download.registry import get_download_providers_for_kind
from app.schemas.provider import LinkData, ContentKind
from app.schemas.request import DownloadRequest
from app.schemas.response import DownloadLink
from app.utils.helpers import lang_label, normalise_quality, fmt_size, RESOLUTION_ORDER
from app.config import get_settings

_settings = get_settings()


# ── File-size probe ────────────────────────────────────────────────────────────

async def _probe_size(url: str, headers: dict) -> Optional[int]:
    try:
        client = await get_client()
        r = await client.head(
            url,
            headers={"User-Agent": UA, **headers},
            timeout=5,
            follow_redirects=True,
        )
        cl = r.headers.get("content-length")
        val = int(cl) if cl else None
        return val if val and val > 1_000_000 else None
    except Exception:
        return None


# ── m3u8 playlist expansion ────────────────────────────────────────────────────

async def _expand_m3u8(
    url: str, headers: dict, provider: str, provider_id: str, language: str,
    original_quality: Optional[str] = None,
) -> list[DownloadLink]:
    try:
        client = await get_client()
        r = await client.get(
            url,
            headers={"User-Agent": UA, **headers},
            timeout=8,
            follow_redirects=True,
        )
        if not r or r.status_code >= 400:
            raise ValueError("bad status")

        results: list[DownloadLink] = []
        seen_res: set[str] = set()
        lines = r.text.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                bw_m  = re.search(r"BANDWIDTH=(\d+)", line)
                height = int(res_m.group(2)) if res_m else 0
                bandwidth = int(bw_m.group(1)) if bw_m else 0

                i += 1
                while i < len(lines) and lines[i].strip().startswith("#"):
                    i += 1
                if i >= len(lines):
                    break
                seg_url = lines[i].strip()
                if not seg_url or seg_url.startswith("#"):
                    i += 1
                    continue

                if not seg_url.startswith("http"):
                    base = url.rsplit("/", 1)[0] + "/"
                    seg_url = base + seg_url

                if height >= 2000:   q = "2160p"
                elif height >= 900:  q = "1080p"
                elif height >= 600:  q = "720p"
                elif height >= 420:  q = "480p"
                elif height >= 300:  q = "360p"
                elif height > 0:     q = "240p"
                elif bandwidth >= 4_000_000:   q = "1080p"
                elif bandwidth >= 2_000_000:   q = "720p"
                elif bandwidth >= 800_000:     q = "480p"
                elif bandwidth >= 400_000:     q = "360p"
                else:                           q = "240p"

                if q not in seen_res:
                    seen_res.add(q)
                    results.append(DownloadLink(
                        provider=provider, provider_id=provider_id,
                        url=seg_url, type="m3u8", quality=q,
                        language=language, headers=headers,
                    ))
            i += 1

        if results:
            order = {q: i for i, q in enumerate(RESOLUTION_ORDER)}
            results.sort(key=lambda x: order.get(x.quality or "", 99))
            return results

    except Exception:
        pass

    return [DownloadLink(
        provider=provider, provider_id=provider_id,
        url=url, type="m3u8", quality=original_quality or "Auto",
        language=language, headers=headers,
    )]


# ── Redirect resolver for mp4 ──────────────────────────────────────────────────

async def _resolve_direct_url(url: str, headers: dict) -> Optional[str]:
    try:
        client = await get_client()
        r = await client.head(
            url,
            headers={"User-Agent": UA, **headers},
            timeout=8,
            follow_redirects=True,
        )
        final = str(r.url)
        ct = r.headers.get("content-type", "")
        is_direct = (
            any(ext in final.lower() for ext in (".mp4", ".mkv", ".webm"))
            or any(t in ct for t in ("video/", "application/octet-stream"))
        )
        return final if is_direct else None
    except Exception:
        return None


# ── Public manager API ─────────────────────────────────────────────────────────

async def get_downloads(
    req: DownloadRequest,
    base_url: str,
    *,
    fresh: bool = False,
    warp_mode: str = "off",
) -> dict:
    t0 = time.monotonic()
    key = download_key(req.tmdb_id, req.type, req.season, req.episode)

    # ── Cache fast path ────────────────────────────────────────────────────────
    if not fresh:
        cached = await cache.get(key)
        if cached:
            links = cached.get("links", [])
            return {
                "ok": bool(links),
                "links": links,
                "cached": True,
                "took_ms": int((time.monotonic() - t0) * 1000),
            }

    # ── TMDB enrichment ────────────────────────────────────────────────────────
    link_data = LinkData(
        id=req.tmdb_id, imdb_id=req.imdb_id, type=req.type,
        season=req.season, episode=req.episode, title=req.title, year=req.year,
    )
    link_data, kind = await enrich_link_data(link_data, req.type)

    # ── Provider fan-out ───────────────────────────────────────────────────────
    from app.utils.warp import run_with_warp, normalize_warp_mode
    mode = normalize_warp_mode(warp_mode)

    providers = get_download_providers_for_kind(kind)
    eligible = [p for p in providers if await provider_stats.should_run(p.id)]

    lock = asyncio.Lock()
    seen_mp4: dict[str, set[str]] = {}
    raw_m3u8: list[DownloadLink] = []
    mp4_links: list[DownloadLink] = []

    async def invoke_one(p) -> None:
        t0i = time.monotonic()
        result = await safe_invoke(p, link_data, _settings.provider_timeout_ms)
        dur_ms = int((time.monotonic() - t0i) * 1000)
        has_items = bool(result.downloads)
        outcome = (
            "found" if has_items
            else "failed" if isinstance(result, _TimedOutResult)
            else "empty"
        )
        await provider_stats.record(p.id, outcome, dur_ms)

        async with lock:
            for item in result.downloads:
                if not item.link:
                    continue
                language = lang_label(item.server)
                dl = DownloadLink(
                    provider=p.name, provider_id=p.id,
                    url=item.link, type=item.type or "mp4",
                    quality=item.quality, language=language, headers=item.headers,
                )
                if item.type == "m3u8":
                    raw_m3u8.append(dl)
                else:
                    q_norm = normalise_quality(item.quality)
                    lang_seen = seen_mp4.setdefault(language, set())
                    if q_norm not in lang_seen:
                        lang_seen.add(q_norm)
                        dl.quality = q_norm
                        mp4_links.append(dl)

    await run_with_warp(
        lambda: asyncio.gather(*[invoke_one(p) for p in eligible]),
        mode=mode,
    )

    # Expand m3u8 masters in parallel
    expand_tasks = [
        _expand_m3u8(lnk.url, lnk.headers, lnk.provider, lnk.provider_id,
                     lnk.language, lnk.quality)
        for lnk in raw_m3u8
    ]
    expanded = await asyncio.gather(*expand_tasks, return_exceptions=True)

    seen_m3u8: dict[str, set[str]] = {}
    m3u8_links: list[DownloadLink] = []
    for res in expanded:
        if isinstance(res, Exception):
            continue
        for lnk in res:
            q_norm = normalise_quality(lnk.quality)
            lang_seen = seen_m3u8.setdefault(lnk.language, set())
            if q_norm not in lang_seen:
                lang_seen.add(q_norm)
                lnk.quality = q_norm
                m3u8_links.append(lnk)

    # Resolve mp4 redirect chains
    async def resolve_mp4(lnk: DownloadLink) -> None:
        if lnk.type in ("mp4", "mkv") and lnk.url:
            resolved = await _resolve_direct_url(lnk.url, lnk.headers)
            if resolved and resolved != lnk.url:
                lnk.url = resolved
                lnk.headers = {}

    await asyncio.gather(*[resolve_mp4(lnk) for lnk in mp4_links], return_exceptions=True)

    all_links = mp4_links + m3u8_links

    # Probe file sizes
    await asyncio.gather(
        *[_probe_size_into(lnk) for lnk in all_links],
        return_exceptions=True,
    )

    # Sort: best quality first, grouped by language
    order = {q: i for i, q in enumerate(RESOLUTION_ORDER)}
    all_links.sort(key=lambda x: (x.language, order.get(x.quality or "", 99)))

    # Build download proxy URLs and format sizes
    links_out = []
    for lnk in all_links:
        lnk.download_url = _build_download_url(lnk, req.title, base_url)
        lnk.size_label = fmt_size(lnk.size_bytes)
        links_out.append(lnk.model_dump())

    took_ms = int((time.monotonic() - t0) * 1000)

    if links_out:
        await cache.set(key, {"links": links_out})

    return {
        "ok": bool(links_out),
        "links": links_out,
        "cached": False,
        "took_ms": took_ms,
        "error": None if links_out else "No download links resolved",
    }


async def _probe_size_into(lnk: DownloadLink) -> None:
    if lnk.size_bytes is None and lnk.type != "m3u8":
        lnk.size_bytes = await _probe_size(lnk.url, lnk.headers)


def _build_download_url(lnk: DownloadLink, title: str, base_url: str) -> Optional[str]:
    from urllib.parse import urlencode
    if lnk.type == "m3u8":
        return lnk.url
    params: dict[str, str] = {"url": lnk.url}
    if lnk.headers.get("Referer"):
        params["referer"] = lnk.headers["Referer"]
    if lnk.headers.get("Origin"):
        params["origin"] = lnk.headers["Origin"]
    quality = lnk.quality or "video"
    ext = "mkv" if lnk.type == "mkv" else "mp4"
    params["filename"] = f"{title} {quality}.{ext}"
    return f"{base_url.rstrip('/')}/api/v1/download-proxy?{urlencode(params)}"
