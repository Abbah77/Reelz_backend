"""
ENGINE/cache/Memory/backend.py — In-memory LRU + TTL cache.

Default backend. Zero dependencies.
Evicts oldest entries when full (LRU).
Self-contained — nothing depends on this file existing.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional

import orjson
from config import get_settings

_s = get_settings()
_MAX = 2048


class MemoryBackend:

    def __init__(self, max_size: int = _MAX) -> None:
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
            self._store.move_to_end(key)
            return orjson.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = _s.cache_ttl_seconds) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + ttl, orjson.dumps(value))
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def stats(self) -> dict:
        async with self._lock:
            now = time.monotonic()
            live = sum(1 for exp, _ in self._store.values() if now < exp)
            return {
                "backend": "memory",
                "total": len(self._store),
                "live": live,
                "max": self._max,
            }
