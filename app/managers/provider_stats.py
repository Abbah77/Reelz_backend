"""
managers/provider_stats.py — per-provider health tracking + circuit breaker.

Tracks: found / empty / failed per provider.
  found  → success (reachable + produced results)
  empty  → reachable but no match (NOT a health problem)
  failed → error or timeout (trips circuit breaker)

Circuit breaker: N consecutive failures → skip provider for COOLDOWN seconds.
Half-open probe: one request allowed after cooldown to check recovery.

Persists to disk (atomic write) so health survives restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

from app.config import get_settings

_settings = get_settings()
_FAIL_THRESHOLD = _settings.circuit_breaker_fail_threshold
_COOLDOWN_S = _settings.circuit_breaker_cooldown_s
_SAVE_DEBOUNCE_S = 5
_STATS_FILE = _settings.provider_stats_file

Outcome = Literal["found", "empty", "failed"]


@dataclass
class _Stat:
    success_count: int = 0
    failure_count: int = 0
    empty_count: int = 0
    consecutive_failures: int = 0
    total_ms: float = 0.0
    timed_runs: int = 0
    circuit_open_until: float = 0.0
    last_outcome: Optional[str] = None
    last_at: float = 0.0


class ProviderStats:
    def __init__(self) -> None:
        self._stats: dict[str, _Stat] = {}
        self._lock = asyncio.Lock()
        self._save_task: Optional[asyncio.Task] = None
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for pid, d in raw.items():
                self._stats[pid] = _Stat(**{
                    k: v for k, v in d.items() if k in _Stat.__dataclass_fields__
                })
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass

    def _schedule_save(self) -> None:
        if self._save_task and not self._save_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
            self._save_task = loop.create_task(self._delayed_save())
        except RuntimeError:
            pass

    async def _delayed_save(self) -> None:
        await asyncio.sleep(_SAVE_DEBOUNCE_S)
        await self._write_to_disk()

    async def _write_to_disk(self) -> None:
        async with self._lock:
            data = {pid: asdict(s) for pid, s in self._stats.items()}
        tmp = _STATS_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, _STATS_FILE)
        except OSError:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def _get_or_create(self, provider_id: str) -> _Stat:
        if provider_id not in self._stats:
            self._stats[provider_id] = _Stat()
        return self._stats[provider_id]

    async def record(self, provider_id: str, outcome: Outcome, duration_ms: float) -> None:
        async with self._lock:
            s = self._get_or_create(provider_id)
            s.last_outcome = outcome
            s.last_at = time.monotonic()

            if outcome == "found":
                s.success_count += 1
                s.consecutive_failures = 0
                s.circuit_open_until = 0.0
                s.total_ms += duration_ms
                s.timed_runs += 1
            elif outcome == "failed":
                s.failure_count += 1
                s.consecutive_failures += 1
                if s.consecutive_failures >= _FAIL_THRESHOLD:
                    s.circuit_open_until = time.monotonic() + _COOLDOWN_S
            else:  # empty
                s.empty_count += 1
                s.consecutive_failures = 0

        self._schedule_save()

    async def should_run(self, provider_id: str) -> bool:
        """
        Returns False while circuit is open.
        After cooldown: one half-open probe, then re-arms cooldown.
        """
        async with self._lock:
            s = self._stats.get(provider_id)
            if s is None or s.circuit_open_until == 0.0:
                return True
            now = time.monotonic()
            if now < s.circuit_open_until:
                return False
            # Half-open probe — re-arm cooldown so concurrent requests don't all get through
            s.circuit_open_until = now + _COOLDOWN_S
            return True

    async def get_stat(self, provider_id: str) -> dict:
        async with self._lock:
            s = self._stats.get(provider_id)
            if s is None:
                return {
                    "success_rate": 1.0, "avg_time_ms": 0,
                    "is_circuit_broken": False, "success_count": 0,
                    "failure_count": 0, "consecutive_failures": 0,
                    "last_outcome": None,
                }
            total = s.success_count + s.failure_count
            return {
                "success_rate": s.success_count / total if total else 1.0,
                "avg_time_ms": round(s.total_ms / s.timed_runs) if s.timed_runs else 0,
                "is_circuit_broken": s.circuit_open_until > time.monotonic(),
                "success_count": s.success_count,
                "failure_count": s.failure_count,
                "consecutive_failures": s.consecutive_failures,
                "last_outcome": s.last_outcome,
            }

    async def get_all_stats(self) -> dict[str, dict]:
        async with self._lock:
            pids = list(self._stats.keys())
        return {pid: await self.get_stat(pid) for pid in pids}

    async def reset(self, provider_id: str) -> None:
        async with self._lock:
            self._stats.pop(provider_id, None)
        self._schedule_save()


# ── Singleton ──────────────────────────────────────────────────────────────────
provider_stats = ProviderStats()
