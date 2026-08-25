"""
ENGINE/manager/stream.py — Stream manager.

Flow:
    1. Check cache → return instantly if hit
    2. Enrich with TMDB metadata (anime/asian detection)
    3. Fan-out to all eligible providers concurrently
    4. First valid m3u8 wins and breaks the race early
    5. Score + sort all results
    6. Deduplicate by URL
    7. Cache results for next users
    8. Record health stats per provider

Rules:
    - Manager NEVER scrapes
    - Manager NEVER touches provider internals
    - Providers NEVER coordinate with each other
    - Registry NEVER runs provider logic
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

from ENGINE.cache.cache import get as cache_get, set as cache_set, stream_key
from ENGINE.cache.ttl_policy import pick_best_ttl, ttl_to_ms
from ENGINE.manager.health import record, should_run
from ENGINE.manager.tmdb import enrich
from ENGINE.providers.base import safe_run, TimedOut, LinkData, Result, Stream
from ENGINE.providers.Stream.registry import get_all
from ENGINE.tools.warp import run_with_warp, normalize_mode
from config import get_settings

_s = get_settings()

_TYPE_SCORE  = {"m3u8": 10, "mp4": 6, "iframe": 2}
_QUAL_SCORE  = {"2160p": 100, "4k": 100, "1080p": 80, "720p": 60, "480p": 40, "360p": 20}
_PROV_BONUS  = {"R-009": 5, "R-002": 4, "R-005": 3, "R-013": 3, "R-012": 2}


def _score(stream: Stream, pid: str) -> int:
    s = _TYPE_SCORE.get(stream.type, 0)
    if stream.quality:
        q = stream.quality.lower().replace(" ", "")
        s += next((v for k, v in _QUAL_SCORE.items() if k in q), 0)
    return s + _PROV_BONUS.get(pid, 0)


def _norm(url: str) -> str:
    try:
        p = urlparse(url)
        return p.netloc + p.path
    except Exception:
        return url


async def _fan_out(data: LinkData) -> tuple[Optional[dict], list[dict]]:
    providers = [p for p in get_all() if await should_run(p.id)]
    if not providers:
        return None, []

    scored: list[tuple[dict, int]] = []
    lock = asyncio.Lock()

    async def invoke(p):
        t0 = time.monotonic()
        result: Result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)

        local = []
        for s in result.streams:
            if not s.url:
                continue
            entry = {
                "provider": p.name,
                "provider_id": p.id,
                "server": s.server or p.name,
                "url": s.url,
                "type": s.type,
                "quality": s.quality,
                "headers": s.headers,
                "playable": s.type != "iframe",
                # Carry expiry from provider if it was set.
                "expires_at_ms": s.expires_at_ms,
            }
            local.append((entry, _score(s, p.id)))

        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)

        async with lock:
            scored.extend(local)

        return [e for e, _ in local]

    tasks = {asyncio.ensure_future(invoke(p)): p for p in providers}
    best_m3u8: Optional[dict] = None
    best_mp4: Optional[dict]  = None
    pending = set(tasks)

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            for entry in (fut.result() or []):
                if entry["type"] == "m3u8" and best_m3u8 is None:
                    best_m3u8 = entry
                elif entry["type"] == "mp4" and best_mp4 is None:
                    best_mp4 = entry
        if best_m3u8:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            break

    scored.sort(key=lambda x: x[1], reverse=True)
    seen: set[str] = set()
    deduped = []
    for entry, _ in scored:
        k = _norm(entry["url"])
        if k not in seen:
            seen.add(k)
            deduped.append(entry)
    for i, e in enumerate(deduped):
        e["priority"] = i

    winner = best_m3u8 or best_mp4
    return winner, deduped


async def get_streams(req, *, fresh: bool = False, warp_mode: str = "off") -> dict:
    t0 = time.monotonic()
    key = stream_key(req.tmdb_id, req.type, req.season, req.episode)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            streams = cached.get("streams", [])
            best = next((s for s in streams if s.get("type") == "m3u8"), streams[0] if streams else None)
            # Re-derive TTL from cached stream entries so the HTTP response
            # reflects remaining lifetime rather than a static hardcoded value.
            app_ttl_s, cf_max_age_s = pick_best_ttl(streams) if streams else (0, 0)
            return {
                "ok": bool(best),
                "stream": best,
                "streams": streams,
                "cached": True,
                "took_ms": int((time.monotonic() - t0) * 1000),
                "cache_ttl_ms": ttl_to_ms(app_ttl_s),
                "cf_max_age_s": cf_max_age_s,
            }

    meta = await enrich(req.tmdb_id, req.type, req.title)
    data = LinkData(
        tmdb_id=req.tmdb_id,
        type=req.type,
        title=req.title,
        imdb_id=req.imdb_id,
        year=req.year,
        season=req.season,
        episode=req.episode,
        is_anime=meta["is_anime"],
        is_asian=meta["is_asian"],
        is_bollywood=meta["is_bollywood"],
        org_title=meta["org_title"],
    )

    mode = normalize_mode(warp_mode)
    winner, streams = await run_with_warp(lambda: _fan_out(data), mode=mode)

    # ── Compute smart TTL before caching ─────────────────────────────────────
    # pick_best_ttl inspects each stream's provider_id and expires_at_ms.
    # It chooses the weakest-link TTL so the batch stays valid for all streams.
    app_ttl_s, cf_max_age_s = pick_best_ttl(streams) if streams else (0, 0)
    cache_ttl_ms = ttl_to_ms(app_ttl_s)

    if streams and app_ttl_s > 0:
        await cache_set(key, {"streams": streams}, ttl=app_ttl_s)

    return {
        "ok": winner is not None,
        "stream": winner,
        "streams": streams,
        "cached": False,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "error": None if winner else "No streams found",
        # These are consumed by the API route to set Cache-Control + envelope TTL.
        "cache_ttl_ms": cache_ttl_ms,
        "cf_max_age_s": cf_max_age_s,
    }
