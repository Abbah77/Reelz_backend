"""
Ultra-fast in-memory cache — orjson serialised, LRU eviction, per-key TTL.
No Redis needed; single-process FastAPI with uvicorn workers shares nothing,
so we keep it simple and fast.  For multi-worker deploys a Redis backend can
be swapped in by implementing the same interface.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional

import orjson

from app.config import get_settings

_settings = get_settings()
_DEFAULT_TTL = _settings.cache_ttl_seconds      # 300 s (5 min) — matches Node warm-cache
_MAX_ENTRIES = 2048


class _TTLCache:
    def __init__(self, max_size: int = _MAX_ENTRIES) -> None:
        self._store: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._max = max_size
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            exp, raw = entry
            if time.monotonic() > exp:
                del self._store[key]
                return None
            # LRU refresh
            self._store.move_to_end(key)
            return orjson.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        async with self._lock:
            raw = orjson.dumps(value)
            self._store[key] = (time.monotonic() + ttl, raw)
            self._store.move_to_end(key)
            # Evict oldest if over capacity
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def size(self) -> int:
        return len(self._store)


# ── Singleton ─────────────────────────────────────────────────────────────────
cache = _TTLCache()


# ── Key helpers ───────────────────────────────────────────────────────────────

def stream_key(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int]) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "streams:" + ":".join(parts)


def download_key(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int]) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "downloads:" + ":".join(parts)


def subtitle_key(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int], langs: list[str]) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    parts.append(",".join(sorted(langs)))
    return "subtitles:" + ":".join(parts)
