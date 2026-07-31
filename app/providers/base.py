"""
Base provider class + provider registry.
Mirrors Node's Provider interface and providers array.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Optional

from app.models import LinkData, ExtractorResult, ContentKind


class Provider(ABC):
    id: str = ""
    name: str = ""
    kinds: Optional[list[ContentKind]] = None   # None = all kinds
    requires_warp: bool = False

    @abstractmethod
    async def invoke(self, data: LinkData) -> ExtractorResult:
        ...

    def serves_kind(self, kind: ContentKind) -> bool:
        return self.kinds is None or kind in self.kinds


# ── Provider registry ─────────────────────────────────────────────────────────
# Populated at import time by each provider module.

_providers: list[Provider] = []
_disabled: list[Provider] = []


def register(p: Provider) -> Provider:
    _providers.append(p)
    return p


def register_disabled(p: Provider) -> Provider:
    _disabled.append(p)
    return p


def get_providers() -> list[Provider]:
    return list(_providers)


def get_providers_for_kind(kind: ContentKind) -> list[Provider]:
    return [p for p in _providers if p.serves_kind(kind)]


# ── Safe invocation with per-provider timeout ─────────────────────────────────

class _TimedOutResult(ExtractorResult):
    """Sentinel subclass so the orchestrator can distinguish timeout from empty."""
    _timed_out: bool = True


async def safe_invoke(provider: Provider, data: LinkData, timeout_ms: int = 45_000) -> ExtractorResult:
    """
    Invoke a provider with a per-provider timeout.
    Never raises — returns empty ExtractorResult on any error/timeout.
    Returns a _TimedOutResult sentinel on timeout so callers can record it
    as 'failed' in the circuit breaker rather than 'empty'.
    """
    empty = ExtractorResult()
    timed_out = _TimedOutResult()
    try:
        result = await asyncio.wait_for(
            provider.invoke(data),
            timeout=timeout_ms / 1000,
        )
        return result or empty
    except asyncio.TimeoutError:
        return timed_out
    except Exception:
        return timed_out
