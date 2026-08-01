"""
WARP IP rotation — Python port of Streamplay's src/utils/warp.ts

Cloudflare WARP gives your server a clean residential-ish egress IP.
Many providers (AnimePahe, Kwik, some Indian CDNs) hard-block datacenter IPs.
Routing through WARP unblocks them.

Four modes (set via WARP_MODE env or per-request via ?warp=):
  off       — never use WARP  (default)
  required  — only providers that declare requires_warp=True
  fallback  — try plain first; if provider returns nothing, retry via WARP
  all       — every provider goes through WARP

Two independent transports (either/both can be configured):
  WARP_PROXY_URL          socks5://host:port  (comma-separated for multiple pairs)
  WARP_FLARESOLVERR_URL   FlareSolverr instance whose container egresses via WARP

Multiple pairs are round-robin'd so different providers use different WARP IPs —
important because some CDNs block even WARP IPs that get hammered.

Usage in a provider:
    from app.utils.warp import run_with_warp, warp_proxy

    async with run_with_warp(mode="fallback", requires_warp=False):
        result = await provider.invoke(data)

Usage in orchestrator:
    proxy = warp_proxy()   # returns active socks5 URL or None
    resp  = await app.get(url, proxy=proxy)
"""
from __future__ import annotations

import asyncio
import itertools
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Literal, Optional, TypeVar

from app.config import get_settings

WarpMode = Literal["off", "required", "fallback", "all"]
_VALID_MODES: set[str] = {"off", "required", "fallback", "all"}

_settings = get_settings()

# ── Parse comma-separated lists from env ─────────────────────────────────────

def _parse_list(raw: str) -> list[str]:
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


_WARP_SOCKS_URLS:  list[str] = _parse_list(_settings.warp_proxy_url)
_WARP_FS_ENDPOINTS: list[str] = [
    f"{u}/v1" for u in _parse_list(_settings.warp_flaresolverr_url)
]

_PAIR_COUNT = max(len(_WARP_SOCKS_URLS), len(_WARP_FS_ENDPOINTS), 1)

# Round-robin counter shared across requests (thread-safe via atomic int trick)
_rr_counter = itertools.count()


def warp_configured() -> bool:
    return bool(_WARP_SOCKS_URLS or _WARP_FS_ENDPOINTS)


def normalize_warp_mode(m: Optional[str]) -> WarpMode:
    if m and m in _VALID_MODES:
        return m  # type: ignore[return-value]
    default = _settings.warp_mode
    return default if default in _VALID_MODES else "off"  # type: ignore[return-value]


# ── Per-request WARP context (via contextvars — asyncio-safe) ─────────────────
# Equivalent to Node's AsyncLocalStorage<WarpCtx>

@dataclass
class _WarpCtx:
    mode: WarpMode
    requires_warp: bool = False
    force_warp: bool = False          # set during a fallback retry
    pair_index: Optional[int] = None  # pinned lazily on first WARP use


_warp_ctx: ContextVar[Optional[_WarpCtx]] = ContextVar("_warp_ctx", default=None)


def _get_ctx() -> Optional[_WarpCtx]:
    return _warp_ctx.get()


def _active_pair_index(ctx: _WarpCtx) -> int:
    """Pin a WARP pair to this invocation on first use (round-robin across requests)."""
    if ctx.pair_index is None:
        ctx.pair_index = next(_rr_counter) % _PAIR_COUNT
    return ctx.pair_index


def warp_active() -> bool:
    """Should the HTTP call currently in flight be routed through WARP?"""
    ctx = _get_ctx()
    if not ctx:
        return False
    if ctx.mode == "off":
        return False
    if ctx.mode == "all" or ctx.force_warp:
        return True
    if ctx.mode == "required":
        return ctx.requires_warp
    return False  # fallback — active only during a forced retry


def warp_proxy() -> Optional[str]:
    """
    Returns the SOCKS5 proxy URL to use for the current request, or None.
    Call from app.get / app.post to inject the proxy.
    """
    if not warp_active() or not _WARP_SOCKS_URLS:
        return None
    ctx = _get_ctx()
    if not ctx:
        return None
    idx = _active_pair_index(ctx) % len(_WARP_SOCKS_URLS)
    return _WARP_SOCKS_URLS[idx]


def warp_flaresolverr() -> Optional[str]:
    """
    Returns the WARP-backed FlareSolverr endpoint for the current request, or None.
    Call from the FlareSolverr helper to pick the right endpoint.
    """
    if not warp_active() or not _WARP_FS_ENDPOINTS:
        return None
    ctx = _get_ctx()
    if not ctx:
        return None
    idx = _active_pair_index(ctx) % len(_WARP_FS_ENDPOINTS)
    return _WARP_FS_ENDPOINTS[idx]


# ── Public API ────────────────────────────────────────────────────────────────

T = TypeVar("T")


async def run_with_warp(
    coro_fn: Callable[[], asyncio.coroutines.Coroutine],
    *,
    mode: WarpMode,
    requires_warp: bool = False,
    force_warp: bool = False,
) -> any:
    """
    Run coro_fn() inside a WARP context.

    Sets the ContextVar so nested app.get / app.post / safe_get calls
    automatically pick up the right proxy without any threading.

    Example:
        result = await run_with_warp(
            lambda: provider.invoke(data),
            mode=warp_mode,
            requires_warp=provider.requires_warp,
        )
    """
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
    empty_check: Callable[[any], bool],
    log_fn: Optional[Callable[[str], None]] = None,
) -> any:
    """
    Run coro_fn() normally first. If mode=='fallback' and result is empty,
    retry via WARP automatically.

    empty_check(result) → True means the result was empty (no streams found).
    log_fn is called with a status message for SSE log events.
    """
    result = await run_with_warp(coro_fn, mode=mode, requires_warp=requires_warp)

    if (
        mode == "fallback"
        and warp_configured()
        and empty_check(result)
    ):
        if log_fn:
            log_fn("retrying via WARP…")
        result = await run_with_warp(
            coro_fn,
            mode=mode,
            requires_warp=requires_warp,
            force_warp=True,
        )

    return result
