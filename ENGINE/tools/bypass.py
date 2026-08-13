"""
ENGINE/tools/bypass.py — URL bypass / redirect-resolver plugin.

Resolves href.li shortlinks and driveleech/driveseed JS-redirect pages
to their final direct-download URLs.

Usage:
    from ENGINE.tools.bypass import bypass_hrefli, get_redirect_link

    direct = await bypass_hrefli("https://href.li/?https://example.com/file.mp4")
    direct = await get_redirect_link("https://redirect.page/?url=https://...")
"""
from __future__ import annotations

import re
from typing import Optional

from ENGINE.tools.http import get_client, UA

_HREFLI_RE = re.compile(r"href\.li/\?(.+)$")
_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\([\"'](.*?)[\"']\)")


async def bypass_hrefli(url: str) -> Optional[str]:
    """
    Resolve an href.li shortlink to the destination URL.
    Falls back to a plain HTTP follow-redirect for non-hrefli links.
    """
    try:
        m = _HREFLI_RE.search(url)
        if m:
            # href.li embeds the destination directly in the path
            return m.group(1)

        client = await get_client()
        res = await client.get(url, headers={"User-Agent": UA, "Referer": "https://href.li/"})
        # If the server redirected, httpx follows and gives us the final URL
        if str(res.url) != url:
            return str(res.url)
        # Look for a JS window.location.replace redirect in the body
        m2 = _JS_REDIRECT_RE.search(res.text)
        if m2:
            return m2.group(1)
        # Look for a meta-refresh
        m3 = re.search(r'http-equiv=["\']refresh["\'][^>]*url=([^"\'>\s]+)', res.text, re.I)
        if m3:
            return m3.group(1)
    except Exception:
        pass
    return None


async def get_redirect_link(url: str) -> Optional[str]:
    """
    Follow a redirect page and return the final destination.
    Handles ?id=... pages that respond with window.location.replace().
    """
    try:
        client = await get_client()
        res = await client.get(url, headers={"User-Agent": UA, "Referer": url})
        m = _JS_REDIRECT_RE.search(res.text)
        if m:
            return m.group(1)
        if str(res.url) != url:
            return str(res.url)
    except Exception:
        pass
    return None


def get_base_url(url: str) -> str:
    """Return scheme + host of a URL (no path)."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
