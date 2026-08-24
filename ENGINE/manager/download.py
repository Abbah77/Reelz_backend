"""
ENGINE/manager/download.py — Download manager (v2).

Flow:
    1. Cache → return instantly if hit
    2. Fan-out to all Download providers (mp4/direct links)
    3. Fan-out to all Stream providers → resolve HLS masters → extract quality variants
    4. Merge all results: every quality from every provider
    5. Deduplicate by (quality, type) — keep best per slot
    6. Cache + return

Key upgrade:
    - Download providers produce direct mp4/hls links as before
    - Stream providers now also contribute to downloads:
      their m3u8 URLs are resolved to quality-specific index.m3u8 files
      so the app can download individual quality .ts segments
    - Result schema: {label, type, url, language, size_bytes, premium}
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from ENGINE.cache.cache import get as cache_get, set as cache_set, download_key
from ENGINE.manager.health import record, should_run
from ENGINE.manager.tmdb import enrich
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Download.registry import get_all as get_download_providers
from ENGINE.providers.Stream.registry import get_all as get_stream_providers
from ENGINE.tools.hls import resolve_master
from config import get_settings

_s = get_settings()

# Resolution sort weight
_QUAL_WEIGHT = {"4k": 500, "2160p": 500, "1080p": 400, "720p": 300, "480p": 200, "360p": 100, "240p": 50}

def _qual_weight(label: str) -> int:
    return _QUAL_WEIGHT.get(label.lower().replace(" ", ""), 0)


async def _collect_from_download_providers(data: LinkData) -> list[dict]:
    """Fan-out to all dedicated Download providers."""
    providers = [p for p in get_download_providers() if await should_run(p.id)]
    collected = []

    async def invoke(p):
        t0 = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)
        local = []
        for item in result.downloads:
            if not item.url:
                continue
            local.append({
                "provider":    p.name,
                "provider_id": p.id,
                "label":       item.quality or "Auto",
                "type":        item.type,          # "mp4" | "hls"
                "url":         item.url,
                "language":    item.language,
                "size_bytes":  item.size_bytes,
                "headers":     item.headers,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            collected.extend(r)
    return collected


async def _collect_from_stream_providers(data: LinkData) -> list[dict]:
    """
    Fan-out to Stream providers, resolve HLS masters to quality-specific index.m3u8 URLs.
    MP4 streams are returned as-is for direct download.
    iframes are skipped (not downloadable).
    """
    providers = [p for p in get_stream_providers() if await should_run(p.id)]
    collected = []

    async def invoke(p):
        t0 = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)
        local = []
        for s in result.streams:
            if not s.url or s.type == "iframe":
                continue
            if s.type == "mp4":
                local.append({
                    "provider":    p.name,
                    "provider_id": p.id,
                    "label":       s.quality or "Auto",
                    "type":        "mp4",
                    "url":         s.url,
                    "language":    "English",
                    "size_bytes":  0,
                    "headers":     s.headers,
                })
            elif s.type in ("m3u8", "hls"):
                # Resolve master → per-quality index.m3u8 URLs
                variants = await resolve_master(s.url, headers=s.headers)
                for v in variants:
                    local.append({
                        "provider":    p.name,
                        "provider_id": p.id,
                        "label":       v["quality"],
                        "type":        "hls",
                        "url":         v["url"],
                        "language":    "English",
                        "size_bytes":  0,
                        "headers":     s.headers,
                    })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            collected.extend(r)
    return collected


def _merge_and_rank(
    download_links: list[dict],
    stream_links:   list[dict],
) -> list[dict]:
    """
    Merge download provider links + stream-derived links.
    Strategy:
      - Prefer dedicated download provider links (they're stable direct links)
      - Fill missing quality slots from stream-resolved HLS
      - Result: richest quality ladder possible (4K, 1080p, 720p, 480p, 360p, 240p)
    Dedup: for each (label, type) slot, keep the one from a dedicated download provider first,
    then HLS from streams.
    """
    # Slot: quality label → best entry
    slots: dict[str, dict] = {}

    def _upsert(entry: dict, priority: int):
        key = entry["label"].lower()
        existing = slots.get(key)
        if existing is None:
            slots[key] = {**entry, "_priority": priority}
        elif priority > existing["_priority"]:
            slots[key] = {**entry, "_priority": priority}

    # Download providers win (priority 2)
    for e in download_links:
        _upsert(e, 2)

    # Stream-derived fill gaps (priority 1)
    for e in stream_links:
        _upsert(e, 1)

    ranked = sorted(slots.values(), key=lambda x: _qual_weight(x["label"]), reverse=True)
    # Strip internal priority key
    for r in ranked:
        r.pop("_priority", None)
    return ranked


async def get_downloads(req, base_url: str = "", *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    key = download_key(req.tmdb_id, req.type, req.season, req.episode)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            return {
                "ok":      True,
                "links":   cached.get("links", []),
                "cached":  True,
                "took_ms": int((time.monotonic() - t0) * 1000),
            }

    meta = await enrich(req.tmdb_id, req.type, req.title if hasattr(req, "title") else "")
    data = LinkData(
        tmdb_id      = req.tmdb_id,
        type         = req.type,
        title        = getattr(req, "title", ""),
        imdb_id      = getattr(req, "imdb_id", None),
        year         = getattr(req, "year", None),
        season       = req.season,
        episode      = req.episode,
        is_anime     = meta["is_anime"],
        is_asian     = meta["is_asian"],
        is_bollywood = meta["is_bollywood"],
        org_title    = meta["org_title"],
    )

    # Fan-out both in parallel
    download_links, stream_links = await asyncio.gather(
        _collect_from_download_providers(data),
        _collect_from_stream_providers(data),
    )

    links = _merge_and_rank(download_links, stream_links)

    if links:
        await cache_set(key, {"links": links})

    return {
        "ok":      bool(links),
        "links":   links,
        "cached":  False,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "error":   None if links else "No download links found",
    }
