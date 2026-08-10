"""
ENGINE/cache/Redis/backend.py — Redis cache backend.

Activated when CACHE_BACKEND=redis in .env.
Same interface as MemoryBackend — swap with zero code changes.

Graceful: any Redis error returns None / silently passes.
The engine never crashes due to cache failures.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import orjson
import redis.asyncio as aioredis
from config import get_settings

_s = get_settings()


class RedisBackend:

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

    async def _get(self) -> aioredis.Redis:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = aioredis.from_url(
                        _s.redis_url,
                        max_connections=20,
                        decode_responses=False,
                    )
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        try:
            client = await self._get()
            raw = await client.get(key)
            return orjson.loads(raw) if raw else None
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = _s.cache_ttl_seconds) -> None:
        try:
            client = await self._get()
            await client.setex(key, ttl, orjson.dumps(value))
        except Exception:
            pass

    async def delete(self, key: str) -> None:
        try:
            client = await self._get()
            await client.delete(key)
        except Exception:
            pass

    async def stats(self) -> dict:
        try:
            client = await self._get()
            info = await client.info("server")
            mem = await client.info("memory")
            ks = await client.info("keyspace")
            total = sum(v.get("keys", 0) for v in ks.values() if isinstance(v, dict))
            return {
                "backend": "redis",
                "connected": True,
                "keys": total,
                "memory": mem.get("used_memory_human", "?"),
                "version": info.get("redis_version", "?"),
            }
        except Exception as e:
            return {"backend": "redis", "connected": False, "error": str(e)}
