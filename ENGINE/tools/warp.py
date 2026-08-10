"""
ENGINE/tools/warp.py — Cloudflare WARP proxy plugin.

Providers that need WARP call warp_proxy() to get the socks5 URL.
Managers wrap fan-out calls in run_with_warp().

Modes (WARP_MODE in .env):
    off       — never use WARP (default)
    required  — only for providers that call warp_proxy() explicitly
    fallback  — try plain first, retry via WARP if empty
    all       — all requests go through WARP

Usage in a provider:
    from ENGINE.tools.warp import warp_proxy
    from ENGINE.tools.http import get_client

    proxy = warp_proxy()
    client = await get_client(proxies={"all://": proxy} if proxy else None)
    res = await client.get("https://...")

Usage in manager:
    from ENGINE.tools.warp import run_with_warp
    result = await run_with_warp(lambda: _fan_out(...), mode=warp_mode)
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable, Literal, Optional

from config import get_settings

_s = get_settings()

WarpMode = Literal["off", "required", "fallback", "all"]

_active: ContextVar[bool] = ContextVar("warp_active", default=False)


def normalize_mode(m: Optional[str]) -> WarpMode:
    valid = {"off", "required", "fallback", "all"}
    if m and m in valid:
        return m  # type: ignore[return-value]
    default = _s.warp_mode
    return default if default in valid else "off"  # type: ignore[return-value]


def warp_proxy() -> Optional[str]:
    """
    Returns the WARP socks5 URL if WARP is currently active, else None.
    Providers call this — they never check mode themselves.
    """
    if not _active.get():
        return None
    url = _s.warp_proxy_url.strip()
    return url if url else None


def warp_flaresolverr() -> Optional[str]:
    """Returns WARP-backed FlareSolverr endpoint if WARP is active, else None."""
    if not _active.get():
        return None
    url = _s.warp_flaresolverr_url.strip()
    return f"{url}/v1" if url else None


async def run_with_warp(coro_fn: Callable, *, mode: WarpMode):
    """
    Run coro_fn() with WARP context set.
    Providers inside coro_fn call warp_proxy() — it returns the URL if mode warrants it.
    """
    should_activate = mode in ("all", "required")
    token = _active.set(should_activate)
    try:
        result = await coro_fn()
    finally:
        _active.reset(token)

    # fallback: retry with WARP forced if first run was empty
    if mode == "fallback" and _s.warp_proxy_url:
        from ENGINE.providers.base import Result
        is_empty = (
            isinstance(result, Result)
            and not result.streams
            and not result.downloads
            and not result.subtitles
            and not result.shorts
        )
        if is_empty:
            token2 = _active.set(True)
            try:
                result = await coro_fn()
            finally:
                _active.reset(token2)

    return result
