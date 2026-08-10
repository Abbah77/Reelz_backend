"""
ENGINE/cache/Cloudflare/backend.py — Cloudflare KV cache backend.

Activated when CACHE_BACKEND=cloudflare in .env.
Best for shorts/trailers — long TTL, global edge cache.
Same interface as all other backends.

Configure in .env:
    CACHE_BACKEND=cloudflare
    CF_KV_ACCOUNT_ID=your_account_id
    CF_KV_NAMESPACE_ID=your_namespace_id
    CF_KV_API_TOKEN=your_api_token
"""
from __future__ import annotations

import os
from typing import Any, Optional

import orjson
import httpx
from config import get_settings

_s = get_settings()

_CF_ACCOUNT  = os.getenv("CF_KV_ACCOUNT_ID", "")
_CF_NS       = os.getenv("CF_KV_NAMESPACE_ID", "")
_CF_TOKEN    = os.getenv("CF_KV_API_TOKEN", "")
_CF_BASE     = f"https://api.cloudflare.com/client/v4/accounts/{_CF_ACCOUNT}/storage/kv/namespaces/{_CF_NS}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {_CF_TOKEN}", "Content-Type": "application/json"}


class CloudflareBackend:

    async def get(self, key: str) -> Optional[Any]:
        if not _CF_TOKEN:
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                res = await client.get(f"{_CF_BASE}/values/{key}", headers=_headers())
            if res.status_code == 404:
                return None
            if res.status_code >= 400:
                return None
            return orjson.loads(res.content)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = _s.cache_ttl_seconds) -> None:
        if not _CF_TOKEN:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.put(
                    f"{_CF_BASE}/values/{key}",
                    headers=_headers(),
                    params={"expiration_ttl": ttl},
                    content=orjson.dumps(value),
                )
        except Exception:
            pass

    async def delete(self, key: str) -> None:
        if not _CF_TOKEN:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.delete(f"{_CF_BASE}/values/{key}", headers=_headers())
        except Exception:
            pass

    async def stats(self) -> dict:
        return {
            "backend": "cloudflare",
            "configured": bool(_CF_TOKEN),
            "namespace": _CF_NS or "not set",
        }
