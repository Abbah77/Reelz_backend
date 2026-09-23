"""
ENGINE/tools/http.py — Shared HTTP client plugin.

Single pooled HTTPX client. HTTP/2. All providers use this.
Never create httpx.AsyncClient directly in a provider.

Performance tuning:
  - 200 total connections, 50 keepalive (was 40) — more parallel provider fan-outs
  - keepalive_expiry=60s (was 30s) — reuse connections across provider calls
  - connect_timeout=8s, read_timeout=25s, write/pool_timeout=10s
    (separate timeouts replace the single 30s blanket — avoids slow connect
     burning the whole budget before the read even starts)
  - follow_redirects=True globally
  - http2=True for CDN servers that support it (faster on HLS sources)

Usage:
    from ENGINE.tools.http import get_client, UA
    client = await get_client()
    res = await client.get("https://...", headers={"User-Agent": UA})
"""
from __future__ import annotations

from typing import Optional
import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

# Tuned timeouts: fast connect detection, generous read window for slow CDNs
_TIMEOUTS = httpx.Timeout(
    connect=8.0,    # fail fast on unreachable hosts
    read=25.0,      # enough for slow HLS manifests / encrypted blobs
    write=10.0,
    pool=10.0,
)

_LIMITS = httpx.Limits(
    max_connections=200,
    max_keepalive_connections=50,  # was 40 — supports more concurrent providers
    keepalive_expiry=60.0,         # was 30s — reuse across provider fan-out window
)

_client: Optional[httpx.AsyncClient] = None


async def get_client(proxies: Optional[dict] = None) -> httpx.AsyncClient:
    """
    Returns the shared pooled client.
    Pass proxies= only for one-off proxied requests (WARP/residential).
    In that case a fresh client is returned — not the shared pool.
    """
    global _client

    if proxies:
        return httpx.AsyncClient(
            proxies=proxies,
            http2=True,
            follow_redirects=True,
            timeout=_TIMEOUTS,
            headers={"User-Agent": UA},
        )

    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=_TIMEOUTS,
            limits=_LIMITS,
            headers={"User-Agent": UA},
        )
    return _client


async def close_client() -> None:
    """Gracefully close the shared client on app shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
