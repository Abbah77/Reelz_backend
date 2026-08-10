"""
ENGINE/tools/encdec.py — enc-dec.app URL decryption plugin.

Used by providers that serve obfuscated embed URLs (VidFast, HexaSU, etc.).

Usage:
    from ENGINE.tools.encdec import enc_dec_get, enc_dec_post

    data = await enc_dec_get("decrypt?token=abc123")
    if data:
        url = data.get("url")
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
import httpx
from ENGINE.tools.http import UA

_API = "https://enc-dec.app/api"
_SEM = asyncio.Semaphore(4)
_RETRIES = 2


async def enc_dec_get(path: str, headers: Optional[dict] = None) -> Optional[Any]:
    """GET {_API}/{path} — returns parsed JSON or None."""
    async with _SEM:
        for attempt in range(_RETRIES + 1):
            try:
                async with httpx.AsyncClient(http2=True, timeout=15) as c:
                    r = await c.get(f"{_API}/{path}", headers={**(headers or {}), "User-Agent": UA})
                return r.json()
            except Exception:
                if attempt < _RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
    return None


async def enc_dec_post(path: str, body: Any, headers: Optional[dict] = None) -> Optional[Any]:
    """POST to {_API}/{path} — returns parsed JSON or None."""
    async with _SEM:
        for attempt in range(_RETRIES + 1):
            try:
                async with httpx.AsyncClient(http2=True, timeout=15) as c:
                    r = await c.post(f"{_API}/{path}", json=body, headers={**(headers or {}), "User-Agent": UA})
                return r.json()
            except Exception:
                if attempt < _RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
    return None
