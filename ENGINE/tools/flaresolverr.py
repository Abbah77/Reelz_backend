"""
ENGINE/tools/flaresolverr.py — Cloudflare bypass plugin.

Providers call solve_cloudflare() when they hit a CF challenge page.
Supports both plain FlareSolverr and WARP-backed FlareSolverr.

Configure in .env:
    FLARESOLVERR_URL=http://localhost:8191
    WARP_FLARESOLVERR_URL=http://warp-fs:8191

Usage:
    from ENGINE.tools.flaresolverr import solve_cloudflare

    html, cookies, ua = await solve_cloudflare("https://example.com/page")
    if html:
        # parse html normally
        ...
"""
from __future__ import annotations

from typing import Optional
import httpx
from config import get_settings

_s = get_settings()
_TIMEOUT = 60


async def solve_cloudflare(
    url: str,
    *,
    use_warp: bool = False,
) -> tuple[Optional[str], dict, str]:
    """
    Returns (html, cookies_dict, user_agent).
    html is None on failure.
    """
    from ENGINE.tools.warp import warp_flaresolverr

    if use_warp:
        endpoint = warp_flaresolverr()
    else:
        endpoint = f"{_s.flaresolverr_url}/v1" if _s.flaresolverr_url else None

    if not endpoint:
        return None, {}, ""

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT + 5, http2=True) as client:
            res = await client.post(endpoint, json={
                "cmd": "request.get",
                "url": url,
                "maxTimeout": _TIMEOUT * 1000,
            })

        if res.status_code != 200:
            return None, {}, ""

        solution = res.json().get("solution", {})
        html = solution.get("response") or None
        ua = solution.get("userAgent", "")
        cookies = {c["name"]: c["value"] for c in solution.get("cookies", []) if "name" in c}
        return html, cookies, ua
    except Exception:
        return None, {}, ""
