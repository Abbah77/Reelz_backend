"""
Per-provider health tracking + circuit breaker.

The SSE fan-out records each provider's outcome (found / empty / failed) here.
We derive a real success rate + average latency, and — crucially — a CIRCUIT BREAKER:
a provider that errors/times out repeatedly is skipped for a cooldown so it stops
wasting a FlareSolverr slot and PROVIDER_TIMEOUT_MS on every request.
A single half-open probe after the cooldown lets it recover automatically.

  found  → success (reachable + produced a stream)
  empty  → reachable but no match for this title (NOT a health problem)
  failed → error or timeout (the signal that trips the breaker)

Persists stats to disk (JSON) so health survives a process restart.
Atomic write (temp file → rename) so a crash never corrupts the file.

Mirrors StreamPlay's src/utils/providerStats.ts, adapted for Python.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

_FAIL_THRESHOLD = 4            # consecutive failures to trip the breaker
_COOLDOWN_S = 10 * 60         # stay broken this long (10 minutes), then half-open probe
_SAVE_DEBOUNCE_S = 5           # batch writes within 5 s to avoid I/O churn

_STATS_FILE = os.environ.get("PROVIDER_STATS_FILE", "./provider-stats.json")

Outcome = Literal["found", "empty", "failed"]


@dataclass
class _Stat:
    success_count: int = 0
    failure_count: int = 0
    empty_count: int = 0
    consecutive_failures: int = 0
    total_ms: float = 0.0       # sum of durations of successful runs (for avg latency)
    timed_runs: int = 0
    circuit_open_until: float = 0.0   # monotonic timestamp; >now = breaker open (skipped)
    last_outcome: Optional[str] = None
    last_at: float = 0.0


class ProviderStats:
    """Thread-safe (asyncio) provider health store with circuit breaker."""

    def __init__(self) -> None:
        self._stats: dict[str, _Stat] = {}
        self._lock = asyncio.Lock()
        self._save_task: Optional[asyncio.Task] = None
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for pid, d in raw.items():
                self._stats[pid] = _Stat(**{k: v for k, v in d.items() if k in _Stat.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass  # no file yet — start fresh

    def _schedule_save(self) -> None:
        """Debounced save — at most one write per _SAVE_DEBOUNCE_S seconds."""
        if self._save_task and not self._save_task.done():
            return
        loop = asyncio.get_event_loop()
        self._save_task = loop.create_task(self._delayed_save())

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
            os.replace(tmp, _STATS_FILE)  # atomic rename — crash-safe
        except OSError:
            pass  # best effort; stats loss on crash is acceptable

    # ── Public API ────────────────────────────────────────────────────────────

    def _get_or_create(self, provider_id: str) -> _Stat:
        if provider_id not in self._stats:
            self._stats[provider_id] = _Stat()
        return self._stats[provider_id]

    async def record(self, provider_id: str, outcome: Outcome, duration_ms: float) -> None:
        """Record the outcome of one provider invocation."""
        async with self._lock:
            s = self._get_or_create(provider_id)
            s.last_outcome = outcome
            s.last_at = time.monotonic()

            if outcome == "found":
                s.success_count += 1
                s.consecutive_failures = 0
                s.circuit_open_until = 0.0   # recovered → close the breaker
                s.total_ms += duration_ms
                s.timed_runs += 1
            elif outcome == "failed":
                s.failure_count += 1
                s.consecutive_failures += 1
                if s.consecutive_failures >= _FAIL_THRESHOLD:
                    s.circuit_open_until = time.monotonic() + _COOLDOWN_S
            else:  # empty — reachable but no match; not a health signal
                s.empty_count += 1
                s.consecutive_failures = 0

        self._schedule_save()

    async def should_run(self, provider_id: str) -> bool:
        """
        Return False while the circuit breaker is open (within cooldown).
        Once the cooldown elapses, allow ONE half-open probe (and re-arm
        the cooldown so a still-dead provider doesn't get probed on every
        concurrent request).
        """
        async with self._lock:
            s = self._stats.get(provider_id)
            if s is None or s.circuit_open_until == 0.0:
                return True
            now = time.monotonic()
            if now < s.circuit_open_until:
                return False
            # Half-open: let this one through, re-arm cooldown
            s.circuit_open_until = now + _COOLDOWN_S
            return True

    async def get_stat(self, provider_id: str) -> dict:
        async with self._lock:
            s = self._stats.get(provider_id)
            if s is None:
                return {
                    "success_rate": 1.0,
                    "avg_time_ms": 0,
                    "is_circuit_broken": False,
                    "success_count": 0,
                    "failure_count": 0,
                    "consecutive_failures": 0,
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
        result = {}
        for pid in pids:
            result[pid] = await self.get_stat(pid)
        return result

    async def priority_score(self, provider_id: str) -> int:
        """Success rate (0–100) minus a circuit-broken penalty."""
        stat = await self.get_stat(provider_id)
        return round(stat["success_rate"] * 100) - (1000 if stat["is_circuit_broken"] else 0)

    async def reset(self, provider_id: str) -> None:
        """Manually reset a provider's stats (e.g. after a fix)."""
        async with self._lock:
            self._stats.pop(provider_id, None)
        self._schedule_save()


# ── Singleton ─────────────────────────────────────────────────────────────────
provider_stats = ProviderStats()
