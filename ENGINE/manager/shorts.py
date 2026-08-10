"""
ENGINE/manager/shorts.py — Shorts manager.

Trailers/clips. Cached 24 hours — they never change.
Paginated: page 1 = first 20, page 2 = next 20, etc.
"""
from __future__ import annotations

import asyncio
import time

from ENGINE.cache.cache import get as cache_get, set as cache_set, shorts_key
from ENGINE.manager.health import record, should_run
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Shorts.registry import get_all
from config import get_settings

_s = get_settings()
_SHORTS_TTL = 86_400  # 24 hours


async def get_shorts(*, tmdb_id: int, media_type: str, page: int = 1, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    key = shorts_key(tmdb_id, media_type, page)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            return {"ok": True, "shorts": cached.get("shorts", []), "page": page,
                    "cached": True, "took_ms": int((time.monotonic() - t0) * 1000)}

    data = LinkData(tmdb_id=tmdb_id, type=media_type, title="")
    providers = [p for p in get_all() if await should_run(p.id)]
    all_shorts = []

    async def invoke(p):
        t_start = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t_start) * 1000)
        local = []
        for s in result.shorts:
            if not s.url:
                continue
            local.append({
                "provider": p.name,
                "title": s.title,
                "url": s.url,
                "thumbnail": s.thumbnail,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            all_shorts.extend(r)

    page_size = 20
    page_items = all_shorts[(page - 1) * page_size: page * page_size]

    if page_items:
        await cache_set(key, {"shorts": page_items}, ttl=_SHORTS_TTL)

    return {
        "ok": bool(page_items),
        "shorts": page_items,
        "page": page,
        "cached": False,
        "took_ms": int((time.monotonic() - t0) * 1000),
    }
