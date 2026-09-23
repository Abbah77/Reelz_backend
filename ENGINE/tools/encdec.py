"""
ENGINE/tools/encdec.py — enc-dec.app URL decryption plugin.

Used by providers that serve obfuscated embed URLs (VidFast, HexaSU,
VidLink, VidEasy, etc.).

All calls go through the shared HTTP pool (get_client()) — no per-call
AsyncClient creation. A concurrency gate (semaphore=4) + retry prevents
rate-limit hammering when several providers call this service in parallel.

Usage:
    from ENGINE.tools.encdec import enc_dec_get, enc_dec_post

    data = await enc_dec_get("enc-vidlink?text=12345")
    if data:
        token = data.get("result")
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ENGINE.tools.http import get_client, UA

_API = "https://enc-dec.app/api"
_SEM = asyncio.Semaphore(4)
_RETRIES = 2


async def enc_dec_get(path: str, headers: Optional[dict] = None) -> Optional[Any]:
    """GET {_API}/{path} — returns parsed JSON or None on any failure."""
    async with _SEM:
        for attempt in range(_RETRIES + 1):
            try:
                client = await get_client()
                r = await client.get(
                    f"{_API}/{path}",
                    headers={**(headers or {}), "User-Agent": UA},
                    timeout=15,
                )
                return r.json()
            except Exception:
                if attempt < _RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
    return None


async def enc_dec_post(path: str, body: Any, headers: Optional[dict] = None) -> Optional[Any]:
    """POST to {_API}/{path} — returns parsed JSON or None on any failure."""
    async with _SEM:
        for attempt in range(_RETRIES + 1):
            try:
                client = await get_client()
                r = await client.post(
                    f"{_API}/{path}",
                    json=body,
                    headers={**(headers or {}), "User-Agent": UA},
                    timeout=15,
                )
                return r.json()
            except Exception:
                if attempt < _RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
    return None
