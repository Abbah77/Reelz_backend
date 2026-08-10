"""
ENGINE/tools/http.py — Shared HTTP client plugin.

Single pooled HTTPX client. HTTP/2. All providers use this.
Never create httpx.AsyncClient directly in a provider.

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

_client: Optional[httpx.AsyncClient] = None


async def get_client(proxies: Optional[dict] = None) -> httpx.AsyncClient:
    """
    Returns the shared client.
    Pass proxies= only when you need a one-off proxied request (WARP/residential).
    In that case a new client is returned — not the shared pool.
    """
    global _client

    if proxies:
        return httpx.AsyncClient(
            proxies=proxies,
            http2=True,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": UA},
        )

    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=200,
                max_keepalive_connections=40,
                keepalive_expiry=30,
            ),
            headers={"User-Agent": UA},
        )
    return _client
