"""
utils/encdec.py — enc-dec.app API helper.

Several providers (VidFast, Hexa, etc.) call this service.
Funnelled here with a Semaphore to avoid hammering the endpoint.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.clients.http import UA

ENC_DEC_API = "https://enc-dec.app/api"
_MAX_CONCURRENT = 4
_MAX_RETRIES = 2
_sem = asyncio.Semaphore(_MAX_CONCURRENT)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": UA},
        )
    return _client


async def _gated(coro_fn) -> Optional[Any]:
    async with _sem:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await coro_fn()
            except Exception:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
    return None


async def enc_dec_get(path: str, headers: Optional[dict] = None) -> Optional[Any]:
    async def _call():
        resp = await _get_client().get(
            f"{ENC_DEC_API}/{path}",
            headers={**(headers or {}), "User-Agent": UA},
        )
        return resp.json()
    return await _gated(_call)


async def enc_dec_post(path: str, body: Any, headers: Optional[dict] = None) -> Optional[Any]:
    async def _call():
        resp = await _get_client().post(
            f"{ENC_DEC_API}/{path}",
            json=body,
            headers={**(headers or {}), "User-Agent": UA},
        )
        return resp.json()
    return await _gated(_call)
