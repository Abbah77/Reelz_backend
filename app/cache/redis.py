"""
cache/redis.py — Redis cache backend.

To switch from memory to Redis:
  1. Set REDIS_URL=redis://localhost:6379 in .env
  2. In cache/__init__.py change: from app.cache.memory import MemoryCache as _Backend
                              to: from app.cache.redis import RedisCache as _Backend

That is the ONLY change required. No provider, manager, or API code changes.
Same interface as MemoryCache.

Requires: pip install redis[asyncio]
"""
from __future__ import annotations

from typing import Any, Optional

import orjson

from app.config import get_settings

_settings = get_settings()
_DEFAULT_TTL = _settings.cache_ttl_seconds


class RedisCache:
    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis  # type: ignore[import]
            self._redis = await aioredis.from_url(
                _settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
            )
        return self._redis

    async def get(self, key: str) -> Optional[Any]:
        r = await self._get_redis()
        raw = await r.get(key)
        if raw is None:
            return None
        return orjson.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        r = await self._get_redis()
        raw = orjson.dumps(value)
        await r.setex(key, ttl, raw)

    async def delete(self, key: str) -> None:
        r = await self._get_redis()
        await r.delete(key)

    async def clear(self) -> None:
        r = await self._get_redis()
        await r.flushdb()

    async def stats(self) -> dict:
        r = await self._get_redis()
        info = await r.info("keyspace")
        return {
            "backend": "redis",
            "url": _settings.redis_url,
            "keyspace": info,
        }
