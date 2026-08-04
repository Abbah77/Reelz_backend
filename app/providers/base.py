"""
providers/base.py — Provider base class + safe invocation.

Every provider (stream, download, subtitle) inherits from Provider.
This file has zero knowledge of registries, managers, or routes.

Rules enforced here:
  - Providers never raise — safe_invoke() swallows all exceptions.
  - Timeout is applied per-provider by safe_invoke().
  - _TimedOutResult sentinel lets managers distinguish "timed out"
    from "ran fine but found nothing" for circuit-breaker purposes.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional

from app.schemas.provider import LinkData, ProviderResult, ContentKind


class Provider(ABC):
    """Base class for every provider (stream, download, subtitle)."""

    id: str = ""
    name: str = ""
    kinds: Optional[list[ContentKind]] = None   # None = serves all kinds
    requires_warp: bool = False

    @abstractmethod
    async def invoke(self, data: LinkData) -> ProviderResult:
        """Fetch streams / downloads / subtitles for `data`. Never raises."""
        ...

    def serves_kind(self, kind: ContentKind) -> bool:
        return self.kinds is None or kind in self.kinds


# ── Timeout sentinel ───────────────────────────────────────────────────────────

class _TimedOutResult(ProviderResult):
    """
    Returned by safe_invoke() on timeout.
    Managers check isinstance(result, _TimedOutResult) to record "failed"
    in the circuit breaker rather than "empty".
    """
    pass


# ── Safe invocation ────────────────────────────────────────────────────────────

async def safe_invoke(
    provider: Provider,
    data: LinkData,
    timeout_ms: int = 45_000,
) -> ProviderResult:
    """
    Run provider.invoke(data) with a hard timeout.
    Never raises. Returns _TimedOutResult on timeout or unhandled exception.
    """
    try:
        result = await asyncio.wait_for(
            provider.invoke(data),
            timeout=timeout_ms / 1000,
        )
        return result or ProviderResult()
    except asyncio.TimeoutError:
        return _TimedOutResult()
    except Exception:
        return _TimedOutResult()
