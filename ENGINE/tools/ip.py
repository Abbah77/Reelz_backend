"""
ENGINE/tools/ip.py — Residential IP proxy plugin.

Some providers need a real home IP to bypass geo-blocks or bot detection.

Configure in .env:
    RESIDENTIAL_PROXY_URL=http://user:pass@host:port

Usage:
    from ENGINE.tools.ip import get_residential_proxy
    from ENGINE.tools.http import get_client

    proxy = get_residential_proxy()
    client = await get_client(proxies={"all://": proxy} if proxy else None)
    res = await client.get("https://...")
"""
from __future__ import annotations

from typing import Optional
from config import get_settings

_s = get_settings()


def get_residential_proxy() -> Optional[str]:
    """Returns residential proxy URL or None if not configured."""
    url = _s.residential_proxy_url.strip()
    return url if url else None
