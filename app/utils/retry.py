"""
utils/retry.py — retry with exponential backoff.
Providers import this; they never implement their own retry loops.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


async def retry(
    times: int,
    coro_fn: Callable[[], Any],
    backoff: float = 0.3,
) -> Optional[Any]:
    """
    Call coro_fn() up to `times` times.
    Returns the first non-None result, or None if all attempts fail.
    Backoff: backoff * attempt_number seconds between retries.
    """
    for i in range(times):
        try:
            result = await coro_fn()
            if result is not None:
                return result
        except Exception:
            pass
        if i < times - 1:
            await asyncio.sleep(backoff * (i + 1))
    return None


async def retry_bool(
    times: int,
    coro_fn: Callable[[], Any],
    backoff: float = 0.3,
) -> bool:
    """Like retry() but returns True/False instead of the value."""
    result = await retry(times, coro_fn, backoff)
    return result is not None
