"""
Source cache — mirrors Streamplay's resolved-source cache in src/routes/web.ts

Why: re-resolving all providers on every open of the same title hammers
FlareSolverr and slow scrapers. Cache the full set of SSE events for a
short window and replay them instantly on repeat opens, rewatches, and
SSE reconnects.

TTL: 8 minutes — short enough that stream tokens don't expire, long enough
to absorb burst traffic on trending titles.

Size: 500 entries max — LRU eviction (oldest-access evicted first).

Cache key: (type, tmdb_id, season, episode)
  season/episode are None for movies.

?fresh=1 query param bypasses and forces a live re-resolve.
A subset re-scan (?providers=…) also bypasses and never writes back,
so a partial result never poisons the cache.

Thread safety: pure asyncio, no threads — asyncio.Lock protects the dict.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import asyncio


# ── Config ────────────────────────────────────────────────────────────────────

SOURCE_CACHE_TTL = 8 * 60          # seconds
SOURCE_CACHE_MAX = 500


# ── Entry ─────────────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    # Wall-clock timestamp of when this entry was stored
    at: float
    # Ordered list of (event_name, data_dict) pairs to replay
    events: list[tuple[str, dict]] = field(default_factory=list)
    # Last-access time — used for LRU eviction
    accessed: float = field(default_factory=time.monotonic)


# ── Cache ─────────────────────────────────────────────────────────────────────

class SourceCache:
    def __init__(
        self,
        ttl: float = SOURCE_CACHE_TTL,
        max_size: int = SOURCE_CACHE_MAX,
    ) -> None:
        self._ttl = ttl
        self._max = max_size
        self._store: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def make_key(
        media_type: str,
        tmdb_id: int,
        season: Optional[int],
        episode: Optional[int],
    ) -> str:
        return f"{media_type}:{tmdb_id}:{season or ''}:{episode or ''}"

    async def get(self, key: str) -> Optional[CacheEntry]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.at > self._ttl:
                # Expired — evict eagerly
                del self._store[key]
                return None
            entry.accessed = time.monotonic()
            return entry

    async def set(self, key: str, events: list[tuple[str, dict]]) -> None:
        """
        Store a completed resolve. Only call when:
          - the connection was NOT aborted (client disconnected mid-stream)
          - at least one 'stream' event is present (don't cache all-empty resolves)
          - it's not a subset re-scan
        """
        async with self._lock:
            # LRU eviction: remove oldest-accessed entry when full
            if len(self._store) >= self._max and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k].accessed)
                del self._store[oldest_key]
            self._store[key] = CacheEntry(
                at=time.monotonic(),
                events=events,
            )

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def stats(self) -> dict:
        async with self._lock:
            now = time.monotonic()
            live = sum(1 for e in self._store.values() if now - e.at <= self._ttl)
            return {
                "total_entries": len(self._store),
                "live_entries": live,
                "max_size": self._max,
                "ttl_seconds": self._ttl,
            }


# ── Singleton ─────────────────────────────────────────────────────────────────

source_cache = SourceCache()
