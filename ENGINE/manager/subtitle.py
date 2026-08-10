"""
ENGINE/manager/subtitle.py — Subtitle manager.

Flow:
    1. Cache → return instantly if hit (1hr TTL)
    2. Fan-out to all subtitle providers concurrently
    3. Deduplicate by URL
    4. Cache + return
"""
from __future__ import annotations

import asyncio
import time

from ENGINE.cache.cache import get as cache_get, set as cache_set, subtitle_key
from ENGINE.manager.health import record, should_run
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Subtitle.registry import get_all
from config import get_settings

_s = get_settings()
_SUB_TTL = 3600  # subtitles cached 1 hour


async def get_subtitles(req, *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    langs = req.languages or ["en"]
    key = subtitle_key(req.tmdb_id, req.type, req.season, req.episode, langs)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            return {"ok": True, "subtitles": cached.get("subtitles", []),
                    "cached": True, "took_ms": int((time.monotonic() - t0) * 1000)}

    data = LinkData(
        tmdb_id=req.tmdb_id, type=req.type, title="",
        imdb_id=req.imdb_id, season=req.season, episode=req.episode,
    )

    providers = [p for p in get_all() if await should_run(p.id)]
    subs = []
    seen: set[str] = set()

    async def invoke(p):
        t_start = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t_start) * 1000)
        local = []
        for sub in result.subtitles:
            if not sub.url or sub.url in seen:
                continue
            seen.add(sub.url)
            local.append({
                "provider": p.name,
                "language": sub.language,
                "label": sub.label or p.name,
                "url": sub.url,
                "format": sub.format,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            subs.extend(r)

    if subs:
        await cache_set(key, {"subtitles": subs}, ttl=_SUB_TTL)

    return {
        "ok": bool(subs),
        "subtitles": subs,
        "cached": False,
        "took_ms": int((time.monotonic() - t0) * 1000),
    }
