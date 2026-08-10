"""
ENGINE/cache/cache.py — Cache headquarters.

This is the only file managers talk to for caching.
It reads config and routes to the correct backend:

    cache_backend = "memory"     → Memory/backend.py
    cache_backend = "redis"      → Redis/backend.py
    cache_backend = "cloudflare" → Cloudflare/backend.py

Managers call:
    from ENGINE.cache.cache import get, set, get_stats

Switching backend: change CACHE_BACKEND in .env. Nothing else changes.
"""
from __future__ import annotations

from typing import Any, Optional
from config import get_settings

_s = get_settings()

# ── Load backend based on config ──────────────────────────────────────────────

_backend_name = (_s.cache_backend or "memory").lower()

if _backend_name == "redis":
    from ENGINE.cache.Redis.backend import RedisBackend as _Backend
    _store = _Backend()
elif _backend_name == "cloudflare":
    from ENGINE.cache.Cloudflare.backend import CloudflareBackend as _Backend
    _store = _Backend()
else:
    from ENGINE.cache.Memory.backend import MemoryBackend as _Backend
    _store = _Backend()


# ── Public interface — managers use these directly ────────────────────────────

async def get(key: str) -> Optional[Any]:
    """Instantly returns cached value or None. Never blocks."""
    return await _store.get(key)


async def set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Store value. ttl in seconds. Uses default TTL if not specified."""
    await _store.set(key, value, ttl=ttl or _s.cache_ttl_seconds)


async def delete(key: str) -> None:
    await _store.delete(key)


async def get_stats() -> dict:
    return await _store.stats()


# ── Cache key builders — one place, one format ────────────────────────────────

def stream_key(tmdb_id: int, media_type: str, season, episode) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "stream:" + ":".join(parts)


def download_key(tmdb_id: int, media_type: str, season, episode) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "download:" + ":".join(parts)


def subtitle_key(tmdb_id: int, media_type: str, season, episode, langs: list) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    parts.append(",".join(sorted(langs)))
    return "subtitle:" + ":".join(parts)


def shorts_key(tmdb_id: int, media_type: str, page: int) -> str:
    return f"shorts:{tmdb_id}:{media_type}:{page}"
