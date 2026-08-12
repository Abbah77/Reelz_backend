"""
api/health.py — Health check routes. No auth required.
"""
from __future__ import annotations

import time
from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}


@router.get("/health/providers")
async def provider_health():
    from ENGINE.providers.Stream.registry import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as download_all
    from ENGINE.providers.Subtitle.registry import get_all as subtitle_all
    from ENGINE.providers.Shorts.registry import get_all as shorts_all
    from ENGINE.manager.health import get_stats
    from ENGINE.cache.cache import get_stats as cache_stats

    providers = []
    for p in stream_all():
        providers.append({**await get_stats(p.id), "id": p.id, "name": p.name, "type": "stream"})
    for p in download_all():
        providers.append({**await get_stats(p.id), "id": p.id, "name": p.name, "type": "download"})
    for p in subtitle_all():
        providers.append({**await get_stats(p.id), "id": p.id, "name": p.name, "type": "subtitle"})
    for p in shorts_all():
        providers.append({**await get_stats(p.id), "id": p.id, "name": p.name, "type": "shorts"})

    return {"providers": providers, "cache": await cache_stats()}


@router.post("/health/providers/{provider_id}/reset")
async def reset_provider(provider_id: str):
    from ENGINE.manager.health import reset
    await reset(provider_id)
    return {"ok": True, "provider_id": provider_id}
