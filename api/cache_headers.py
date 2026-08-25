"""
api/cache_headers.py — Cache-Control header helper.

Translates backend TTL values into proper HTTP Cache-Control headers that
tell both Cloudflare and app clients how long to cache responses.

TWO TTL INPUTS:
  cache_ttl_ms   — how long the app/client should cache (from envelope).
  cf_max_age_s   — how long Cloudflare's edge should cache (always <= cache_ttl_ms/1000).
                   When omitted, defaults to 90% of cache_ttl_ms.

HEADER OUTPUT:
  no cache:
    Cache-Control: no-store

  with cache:
    Cache-Control: public, max-age=<client_s>, s-maxage=<cf_s>, stale-while-revalidate=<swr_s>

    max-age      → client browser cache lifetime (same as app_ttl)
    s-maxage     → Cloudflare edge cache lifetime (shorter than max-age so CF
                   evicts before the app cache expires, preventing stale hits)
    stale-while-revalidate → allows CF to serve a stale response for a short
                   window while it fetches a fresh one in the background.
                   Set to 10% of cf_max_age so there's a brief grace window
                   without extending the stale-serving window too long.
"""
from __future__ import annotations
from fastapi import Response


def set_cache(
    response: Response,
    cache_ttl_ms: int | None,
    cf_max_age_s: int | None = None,
) -> None:
    """
    Attach the correct Cache-Control header to a FastAPI response.

    Args:
        cache_ttl_ms:  App/client cache lifetime in milliseconds.
                       None or 0 → Cache-Control: no-store.
        cf_max_age_s:  Cloudflare edge cache lifetime in seconds.
                       None → auto-computed as 90% of cache_ttl_ms.
                       0    → same as no-store.
    """
    if not cache_ttl_ms:
        response.headers["Cache-Control"] = "no-store"
        return

    client_s = max(1, cache_ttl_ms // 1000)

    if cf_max_age_s is None:
        # Default: Cloudflare caches 90% of the client window so it evicts
        # slightly before the app's cached value expires.
        cf_s = max(1, int(client_s * 0.90))
    elif cf_max_age_s <= 0:
        response.headers["Cache-Control"] = "no-store"
        return
    else:
        # Never let CF cache longer than the client would.
        cf_s = min(cf_max_age_s, client_s)

    # stale-while-revalidate: a grace window (10% of CF lifetime, min 30s, max 300s)
    # where CF can serve a stale response while fetching fresh data in the background.
    swr_s = max(30, min(300, int(cf_s * 0.10)))

    response.headers["Cache-Control"] = (
        f"public, max-age={client_s}, s-maxage={cf_s}, stale-while-revalidate={swr_s}"
    )
