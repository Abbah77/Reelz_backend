"""
cache/memory.py — in-memory LRU+TTL cache.

This is the default cache backend.
To switch to Redis tomorrow: only edit cache/ — nothing in managers or providers changes.
Implements the same interface as cache/redis.py so they are drop-in swappable.

Interface:
    get(key) -> Any | None
    set(key, value, ttl?) -> None
    delete(key) -> None
    clear() -> None
    stats() -> dict
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional

import orjson

from app.config import get_settings

_settings = get_settings()
_DEFAULT_TTL = _settings.cache_ttl_seconds
_MAX_ENTRIES = 2048


class MemoryCache:
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
            self._store.move_to_end(key)     # LRU refresh
            return orjson.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        async with self._lock:
            raw = orjson.dumps(value)
            self._store[key] = (time.monotonic() + ttl, raw)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)   # evict oldest

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def stats(self) -> dict:
        async with self._lock:
            now = time.monotonic()
            live = sum(1 for exp, _ in self._store.values() if now < exp)
            return {
                "backend": "memory",
                "total_entries": len(self._store),
                "live_entries": live,
                "max_size": self._max,
                "ttl_seconds": _DEFAULT_TTL,
            }
