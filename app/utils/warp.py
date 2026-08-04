"""
utils/warp.py — Cloudflare WARP egress proxy.

Modes (WARP_MODE env or ?warp= query param):
  off       — never use WARP (default)
  required  — only providers that declare requires_warp=True
  fallback  — try plain first; retry via WARP if provider returns nothing
  all       — every request goes through WARP

Usage in managers:
    result = await run_with_warp(lambda: manager_logic(), mode=warp_mode)

The ContextVar means providers get WARP for free — they just call
clients/http.py's app.get() and the proxy is transparently injected.
"""
from __future__ import annotations

import asyncio
import itertools
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from app.config import get_settings

WarpMode = Literal["off", "required", "fallback", "all"]
_VALID_MODES: set[str] = {"off", "required", "fallback", "all"}

_settings = get_settings()


def _parse_list(raw: str) -> list[str]:
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


_WARP_SOCKS_URLS:   list[str] = _parse_list(_settings.warp_proxy_url)
_WARP_FS_ENDPOINTS: list[str] = [
    f"{u}/v1" for u in _parse_list(_settings.warp_flaresolverr_url)
]
_PAIR_COUNT = max(len(_WARP_SOCKS_URLS), len(_WARP_FS_ENDPOINTS), 1)
_rr_counter = itertools.count()


def warp_configured() -> bool:
    return bool(_WARP_SOCKS_URLS or _WARP_FS_ENDPOINTS)


def normalize_warp_mode(m: Optional[str]) -> WarpMode:
    if m and m in _VALID_MODES:
        return m  # type: ignore[return-value]
    default = _settings.warp_mode
    return default if default in _VALID_MODES else "off"  # type: ignore[return-value]


@dataclass
class _WarpCtx:
    mode: WarpMode
    requires_warp: bool = False
    force_warp: bool = False
    pair_index: Optional[int] = None


_warp_ctx: ContextVar[Optional[_WarpCtx]] = ContextVar("_warp_ctx", default=None)


def _get_ctx() -> Optional[_WarpCtx]:
    return _warp_ctx.get()


def _active_pair_index(ctx: _WarpCtx) -> int:
    if ctx.pair_index is None:
        ctx.pair_index = next(_rr_counter) % _PAIR_COUNT
    return ctx.pair_index


def warp_active() -> bool:
    ctx = _get_ctx()
    if not ctx or ctx.mode == "off":
        return False
    if ctx.mode == "all" or ctx.force_warp:
        return True
    if ctx.mode == "required":
        return ctx.requires_warp
    return False


def warp_proxy() -> Optional[str]:
    if not warp_active() or not _WARP_SOCKS_URLS:
        return None
    ctx = _get_ctx()
    if not ctx:
        return None
    idx = _active_pair_index(ctx) % len(_WARP_SOCKS_URLS)
    return _WARP_SOCKS_URLS[idx]


def warp_flaresolverr() -> Optional[str]:
    if not warp_active() or not _WARP_FS_ENDPOINTS:
        return None
    ctx = _get_ctx()
    if not ctx:
        return None
    idx = _active_pair_index(ctx) % len(_WARP_FS_ENDPOINTS)
    return _WARP_FS_ENDPOINTS[idx]


async def run_with_warp(
    coro_fn: Callable[[], asyncio.coroutines.Coroutine],
    *,
    mode: WarpMode,
    requires_warp: bool = False,
    force_warp: bool = False,
):
    ctx = _WarpCtx(mode=mode, requires_warp=requires_warp, force_warp=force_warp)
    token = _warp_ctx.set(ctx)
    try:
        return await coro_fn()
    finally:
        _warp_ctx.reset(token)


async def run_with_warp_fallback(
    coro_fn: Callable[[], asyncio.coroutines.Coroutine],
    *,
    mode: WarpMode,
    requires_warp: bool = False,
    empty_check: Callable,
):
    result = await run_with_warp(coro_fn, mode=mode, requires_warp=requires_warp)
    if mode == "fallback" and warp_configured() and empty_check(result):
        result = await run_with_warp(coro_fn, mode=mode,
                                     requires_warp=requires_warp, force_warp=True)
    return result
