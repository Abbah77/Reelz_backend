"""
ENGINE/manager/health.py — Provider health tracking + circuit breaker.

Records outcomes per provider:
    found   — had results (success)
    empty   — reachable but no match (NOT a health problem)
    failed  — timeout or crash (trips circuit breaker)

Circuit breaker:
    N consecutive failures → skip provider for COOLDOWN seconds
    After cooldown: one probe allowed through to test recovery

Persists to disk so stats survive restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Literal, Optional

from config import get_settings

_s = get_settings()
Outcome = Literal["found", "empty", "failed"]


@dataclass
class _Stat:
    success: int = 0
    failure: int = 0
    empty: int = 0
    consecutive_failures: int = 0
    total_ms: float = 0.0
    runs: int = 0
    circuit_until: float = 0.0
    last: Optional[str] = None


class HealthTracker:

    def __init__(self) -> None:
        self._stats: dict[str, _Stat] = {}
        self._lock = asyncio.Lock()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            with open(_s.provider_stats_file, "r") as f:
                for pid, d in json.load(f).items():
                    self._stats[pid] = _Stat(**{k: v for k, v in d.items() if k in _Stat.__dataclass_fields__})
        except Exception:
            pass

    async def _save(self) -> None:
        async with self._lock:
            data = {pid: asdict(s) for pid, s in self._stats.items()}
        try:
            tmp = _s.provider_stats_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _s.provider_stats_file)
        except Exception:
            pass

    def _stat(self, pid: str) -> _Stat:
        if pid not in self._stats:
            self._stats[pid] = _Stat()
        return self._stats[pid]

    async def record(self, pid: str, outcome: Outcome, ms: float) -> None:
        async with self._lock:
            s = self._stat(pid)
            s.last = outcome
            if outcome == "found":
                s.success += 1
                s.consecutive_failures = 0
                s.circuit_until = 0.0
                s.total_ms += ms
                s.runs += 1
            elif outcome == "failed":
                s.failure += 1
                s.consecutive_failures += 1
                if s.consecutive_failures >= _s.cb_fail_threshold:
                    s.circuit_until = time.monotonic() + _s.cb_cooldown_seconds
            else:
                s.empty += 1
                s.consecutive_failures = 0
        asyncio.ensure_future(self._save())

    async def should_run(self, pid: str) -> bool:
        async with self._lock:
            s = self._stats.get(pid)
            if not s or s.circuit_until == 0.0:
                return True
            now = time.monotonic()
            if now < s.circuit_until:
                return False
            # half-open probe
            s.circuit_until = now + _s.cb_cooldown_seconds
            return True

    async def get_stats(self, pid: str) -> dict:
        async with self._lock:
            s = self._stats.get(pid)
            if not s:
                return {"circuit_broken": False, "success_rate": 1.0, "avg_ms": 0,
                        "success": 0, "failure": 0, "last": None}
            total = s.success + s.failure
            return {
                "circuit_broken": s.circuit_until > time.monotonic(),
                "success_rate": round(s.success / total, 2) if total else 1.0,
                "avg_ms": round(s.total_ms / s.runs) if s.runs else 0,
                "success": s.success,
                "failure": s.failure,
                "consecutive_failures": s.consecutive_failures,
                "last": s.last,
            }

    async def reset(self, pid: str) -> None:
        async with self._lock:
            self._stats.pop(pid, None)
        await self._save()


# ── Singleton ─────────────────────────────────────────────────────────────────
_tracker = HealthTracker()


async def get_stats(pid: str) -> dict:
    return await _tracker.get_stats(pid)


async def record(pid: str, outcome: Outcome, ms: float) -> None:
    await _tracker.record(pid, outcome, ms)


async def should_run(pid: str) -> bool:
    return await _tracker.should_run(pid)


async def reset(pid: str) -> None:
    await _tracker.reset(pid)
