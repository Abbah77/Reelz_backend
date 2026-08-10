"""
ENGINE/tools/debrid.py — Debrid link unrestrictor plugin.

Converts restricted filehost links into direct download URLs.
Supports Real-Debrid, AllDebrid, TorBox.

Configure in .env:
    REALDEBRID_KEY=your_key
    ALLDEBRID_KEY=your_key
    TORBOX_KEY=your_key

Usage:
    from ENGINE.tools.debrid import unrestrict_link

    direct_url = await unrestrict_link("https://filehoster.com/file123")
"""
from __future__ import annotations

from typing import Optional
import httpx
from config import get_settings

_s = get_settings()


async def unrestrict_link(url: str) -> Optional[str]:
    """Unrestrict a filehost URL. Auto-selects service from available keys."""
    if _s.realdebrid_key:
        return await _rd(url)
    if _s.alldebrid_key:
        return await _ad(url)
    if _s.torbox_key:
        return await _torbox(url)
    return None


async def _rd(url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15, http2=True) as c:
            r = await c.post(
                "https://api.real-debrid.com/rest/1.0/unrestrict/link",
                headers={"Authorization": f"Bearer {_s.realdebrid_key}"},
                data={"link": url},
            )
        return r.json().get("download") if r.status_code == 200 else None
    except Exception:
        return None


async def _ad(url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15, http2=True) as c:
            r = await c.get(
                "https://api.alldebrid.com/v4/link/unlock",
                params={"agent": "Reelz", "apikey": _s.alldebrid_key, "link": url},
            )
        return r.json().get("data", {}).get("link") if r.status_code == 200 else None
    except Exception:
        return None


async def _torbox(url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15, http2=True) as c:
            r = await c.post(
                "https://api.torbox.app/v1/api/webdl/createwebdownload",
                headers={"Authorization": f"Bearer {_s.torbox_key}"},
                json={"link": url},
            )
        return r.json().get("data", {}).get("auth_id") if r.status_code in (200, 201) else None
    except Exception:
        return None
