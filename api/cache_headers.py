"""
api/cache_headers.py — Cache-Control header helper.

Converts cache_ttl_ms to a matching Cache-Control header so Cloudflare
caches for exactly the same duration the app caches in Room DB.

One source of truth: the backend decides the TTL.
Both Cloudflare and the app obey it.

  cache_ttl_ms=None / 0  → Cache-Control: no-store  (never cache)
  cache_ttl_ms=N         → Cache-Control: public, max-age=N_seconds
"""
from __future__ import annotations
from fastapi import Response


def set_cache(response: Response, cache_ttl_ms: int | None) -> None:
    """Attach the correct Cache-Control header to a FastAPI response."""
    if not cache_ttl_ms:
        response.headers["Cache-Control"] = "no-store"
    else:
        seconds = cache_ttl_ms // 1000
        response.headers["Cache-Control"] = f"public, max-age={seconds}"
