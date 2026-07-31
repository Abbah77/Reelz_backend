"""
enc-dec.app helper — Python port of Node's utils/encdec.ts.

Several providers (VidFast, Hexa, Vidlink) all call this free service concurrently.
We funnel calls through an asyncio Semaphore to avoid hammering the endpoint,
with retries and backoff mirroring the original concurrency gate.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

ENC_DEC_API = "https://enc-dec.app/api"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

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
    """GET /api/<path> and return parsed JSON."""
    async def _call():
        resp = await _get_client().get(
            f"{ENC_DEC_API}/{path}",
            headers={**(headers or {}), "User-Agent": UA},
        )
        return resp.json()
    return await _gated(_call)


async def enc_dec_post(path: str, body: Any, headers: Optional[dict] = None) -> Optional[Any]:
    """POST JSON to /api/<path> and return parsed JSON."""
    async def _call():
        resp = await _get_client().post(
            f"{ENC_DEC_API}/{path}",
            json=body,
            headers={**(headers or {}), "User-Agent": UA},
        )
        return resp.json()
    return await _gated(_call)
