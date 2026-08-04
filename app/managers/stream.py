"""
managers/stream.py — stream manager.

Responsibilities (ALL coordination lives here):
  - Cache lookup / cache write
  - Timeout enforcement
  - Concurrent provider fan-out
  - First-wins race (fastest m3u8 breaks out early)
  - Deduplication by URL
  - Priority scoring + sorting
  - Provider statistics (circuit breaker recording)

Managers NEVER scrape websites.
Providers NEVER coordinate with each other.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

from app.cache import cache
from app.cache.keys import stream_key
from app.clients.tmdb import enrich_link_data
from app.managers.provider_stats import provider_stats
from app.providers.base import safe_invoke, _TimedOutResult
from app.providers.stream.registry import get_stream_providers_for_kind
from app.schemas.provider import LinkData, ProviderResult, ContentKind
from app.schemas.request import StreamRequest
from app.schemas.response import StreamEntry, SubtitleEntry
from app.utils.helpers import lang_label, norm_url
from app.config import get_settings

_settings = get_settings()

# ── Scoring tables ─────────────────────────────────────────────────────────────

_TYPE_SCORES = {"m3u8": 10, "mp4": 6, "iframe": 2}
_QUALITY_SCORES = {
    "2160p": 100, "4k": 100,
    "1080p": 80, "720p": 60, "480p": 40, "360p": 20,
}
_PROVIDER_BONUS = {
    "rivestream": 5, "vidfast": 4, "allmovieland": 3,
    "hdrezka": 3, "castle": 2, "vegamovies": 1, "hdhub4u": 1,
}


def _score(stream, provider_id: str) -> int:
    score = _TYPE_SCORES.get(stream.type, 0)
    if stream.quality:
        q = stream.quality.lower().replace(" ", "")
        for k, v in _QUALITY_SCORES.items():
            if k in q:
                score += v
                break
    score += _PROVIDER_BONUS.get(provider_id.lower(), 0)
    return score


def _make_entry(stream, provider) -> StreamEntry:
    return StreamEntry(
        provider=provider.name,
        provider_id=provider.id,
        name=stream.server,
        url=stream.link,
        type=stream.type,
        quality=stream.quality,
        language=lang_label(stream.server),
        headers=stream.headers,
        playable=stream.type != "iframe",
        priority=0,
    )


def _dedup(scored: list[tuple[StreamEntry, int]]) -> list[StreamEntry]:
    seen: set[str] = set()
    out: list[StreamEntry] = []
    for entry, _ in scored:
        key = norm_url(entry.url)
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


# ── Core fan-out: first-wins race ──────────────────────────────────────────────

async def _fan_out_first_wins(
    data: LinkData,
    kind: ContentKind,
    timeout_ms: int,
) -> tuple[Optional[StreamEntry], list[StreamEntry]]:
    """
    Fan out to all eligible providers concurrently.
    Returns as soon as the first valid m3u8 arrives.
    Falls back to mp4 if no m3u8 found before all providers finish.
    """
    all_providers = get_stream_providers_for_kind(kind)
    eligible = [p for p in all_providers if await provider_stats.should_run(p.id)]
    if not eligible:
        return None, []

    scored: list[tuple[StreamEntry, int]] = []
    lock = asyncio.Lock()

    async def invoke_one(p) -> list[StreamEntry]:
        t0 = time.monotonic()
        result: ProviderResult = await safe_invoke(p, data, timeout_ms)
        dur_ms = int((time.monotonic() - t0) * 1000)

        local: list[tuple[StreamEntry, int]] = []
        for stream in result.streams:
            if not stream.link or stream.type == "iframe":
                continue
            entry = _make_entry(stream, p)
            local.append((entry, _score(stream, p.id)))

        outcome = (
            "found" if local
            else "failed" if isinstance(result, _TimedOutResult)
            else "empty"
        )
        await provider_stats.record(p.id, outcome, dur_ms)

        async with lock:
            scored.extend(local)

        return [e for e, _ in local]

    tasks = {asyncio.ensure_future(invoke_one(p)): p for p in eligible}
    best_m3u8: Optional[StreamEntry] = None
    best_mp4: Optional[StreamEntry] = None
    pending = set(tasks.keys())

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            try:
                entries = fut.result()
            except Exception:
                entries = []
            for entry in entries:
                if entry.type == "m3u8" and best_m3u8 is None:
                    best_m3u8 = entry
                elif entry.type == "mp4" and best_mp4 is None:
                    best_mp4 = entry

        if best_m3u8 is not None:
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            break

    scored.sort(key=lambda x: x[1], reverse=True)
    all_entries = _dedup(scored)
    for i, entry in enumerate(all_entries):
        entry.priority = i

    return (best_m3u8 or best_mp4), all_entries


# ── Public manager API ─────────────────────────────────────────────────────────

async def get_streams(
    req: StreamRequest,
    *,
    fresh: bool = False,
    warp_mode: str = "off",
) -> dict:
    """
    Entry point called by api/streams.py.
    Returns a dict ready to send as JSON — no business logic in the route.
    """
    t0 = time.monotonic()
    key = stream_key(req.tmdb_id, req.type, req.season, req.episode)

    # ── Cache fast path ────────────────────────────────────────────────────────
    if not fresh:
        cached = await cache.get(key)
        if cached:
            streams = cached.get("streams", [])
            best = next((s for s in streams if s.get("type") == "m3u8"), streams[0] if streams else None)
            return {
                "ok": bool(best),
                "stream": best,
                "streams": streams,
                "subtitles": [],
                "cached": True,
                "took_ms": int((time.monotonic() - t0) * 1000),
            }

    # ── TMDB enrichment → kind detection ──────────────────────────────────────
    link_data = LinkData(
        id=req.tmdb_id,
        imdb_id=req.imdb_id,
        type=req.type,
        season=req.season,
        episode=req.episode,
        title=req.title,
        year=req.year,
    )
    link_data, kind = await enrich_link_data(link_data, req.type)

    # ── Provider fan-out ───────────────────────────────────────────────────────
    from app.utils.warp import run_with_warp, normalize_warp_mode
    mode = normalize_warp_mode(warp_mode)

    winner, all_entries = await run_with_warp(
        lambda: _fan_out_first_wins(link_data, kind, _settings.provider_timeout_ms),
        mode=mode,
    )

    took_ms = int((time.monotonic() - t0) * 1000)

    streams_out = [e.model_dump() for e in all_entries]
    winner_out = winner.model_dump() if winner else None

    # ── Cache if results ───────────────────────────────────────────────────────
    if streams_out:
        await cache.set(key, {"streams": streams_out})

    return {
        "ok": winner_out is not None,
        "stream": winner_out,
        "streams": streams_out,
        "subtitles": [],
        "cached": False,
        "took_ms": took_ms,
        "error": None if winner_out else "No streams resolved",
    }
