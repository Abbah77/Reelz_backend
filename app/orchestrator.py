"""
Stream orchestrator — the Python equivalent of the Node extractors/index.ts
runProviders() / fan-out loop.

Upgraded features over original:
- Circuit breaker (via provider_stats): skips providers with 4+ consecutive
  failures for a 10-minute cooldown, then half-open probe to auto-recover.
- provider_stats recording: every outcome (found/empty/failed) is tracked and
  persisted to disk for the /api/v1/providers stats endpoint.
- asyncio.gather with per-provider timeouts (no global race to first).
- Result deduplication by URL (normalised).
- Priority scoring: direct m3u8 > packed player > iframe > mp4.
- Language-aware label enrichment.
- Provider SSE status emission (used by /events endpoint).
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import AsyncGenerator, Callable, Optional
from urllib.parse import urlparse

import orjson

from app.models import (
    ContentKind,
    ExtractorResult,
    LinkData,
    Stream,
    Subtitle,
    StreamEntry,
    SubtitleEntry,
    DownloadLink,
)
from app.providers.base import Provider, safe_invoke, get_providers_for_kind, _TimedOutResult
from app.provider_stats import provider_stats
from app.config import get_settings

_settings = get_settings()


# ── Priority scoring ──────────────────────────────────────────────────────────

_QUALITY_SCORES: dict[str, int] = {
    "2160p": 100, "4k": 100,
    "1080p": 80,
    "720p":  60,
    "480p":  40,
    "360p":  20,
}

_HOST_BONUS: dict[str, int] = {
    "rivestream":     5,
    "vidfast":        4,
    "allmovieland":   3,
    "hdrezka":        3,
    "castle":         2,
    "vegamovies":     1,
    "hdhub4u":        1,
}

_TYPE_SCORES: dict[str, int] = {
    "m3u8":   10,
    "mp4":    6,
    "iframe": 2,
}


def _score_stream(stream: Stream, provider_id: str) -> int:
    score = _TYPE_SCORES.get(stream.type, 0)
    if stream.quality:
        q_norm = stream.quality.lower().replace(" ", "")
        for k, v in _QUALITY_SCORES.items():
            if k in q_norm:
                score += v
                break
    score += _HOST_BONUS.get(provider_id.lower(), 0)
    return score


def _norm_url(url: str) -> str:
    """Normalise a URL for deduplication (strip session-token query params)."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return url


def _lang_label(server: str) -> str:
    """Infer a language tag from a stream server name."""
    s = server.lower()
    if "hindi" in s:    return "Hindi"
    if "tamil" in s:    return "Tamil"
    if "telugu" in s:   return "Telugu"
    if "malayalam" in s: return "Malayalam"
    if "kannada" in s:  return "Kannada"
    if "bengali" in s:  return "Bengali"
    if "marathi" in s:  return "Marathi"
    if "punjabi" in s:  return "Punjabi"
    if "korean" in s or "kor" in s:     return "Korean"
    if "japanese" in s or "jpn" in s or "sub" in s: return "Japanese"
    if "chinese" in s or "chi" in s or "mandarin" in s: return "Chinese"
    if "french" in s or "fra" in s:     return "French"
    if "spanish" in s or "esp" in s:    return "Spanish"
    if "arabic" in s or "ara" in s:     return "Arabic"
    if "dubbed" in s or "dub" in s:     return "Dubbed"
    return "English"


# ── Stream deduplication ──────────────────────────────────────────────────────

def _dedup_streams(entries: list[tuple[StreamEntry, int]]) -> list[StreamEntry]:
    seen: set[str] = set()
    out: list[StreamEntry] = []
    for entry, _score in entries:
        key = _norm_url(entry.url)
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


# ── Main fan-out ──────────────────────────────────────────────────────────────

async def run_providers(
    data: LinkData,
    kind: ContentKind,
    *,
    on_provider_done: Optional[Callable[[str, str, list[StreamEntry], int], None]] = None,
    timeout_ms: int = 0,
) -> tuple[list[StreamEntry], list[SubtitleEntry]]:
    """
    Fan out to all providers for the given kind concurrently.
    Returns deduplicated, priority-sorted (StreamEntry list, SubtitleEntry list).

    Circuit breaker: providers with 4+ consecutive failures are skipped for a
    10-minute cooldown, then get one half-open probe to auto-recover.

    on_provider_done(provider_id, state, streams, duration_ms) — called after
    each provider finishes; used for SSE streaming.
    """
    if timeout_ms == 0:
        timeout_ms = _settings.provider_timeout_ms

    all_providers = get_providers_for_kind(kind)
    if not all_providers:
        return [], []

    # Circuit breaker: filter out broken providers before fan-out
    eligible_providers: list[Provider] = []
    skipped_providers: list[Provider] = []
    for p in all_providers:
        if await provider_stats.should_run(p.id):
            eligible_providers.append(p)
        else:
            skipped_providers.append(p)
            # Emit a skipped status so the SSE client knows
            if on_provider_done:
                on_provider_done(p.id, "circuit_open", [], 0)

    if not eligible_providers:
        return [], []

    scored: list[tuple[StreamEntry, int]] = []
    all_subs: list[SubtitleEntry] = []
    subs_seen: set[str] = set()
    lock = asyncio.Lock()

    async def invoke_one(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            result: ExtractorResult = await safe_invoke(p, data, timeout_ms)
            dur_ms = int((time.monotonic() - t0) * 1000)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            result = ExtractorResult()

        local_entries: list[StreamEntry] = []
        for stream in result.streams:
            if not stream.link:
                continue
            lang = _lang_label(stream.server)
            entry = StreamEntry(
                provider=p.name,
                provider_id=p.id,
                name=stream.server,
                url=stream.link,
                type=stream.type,
                quality=stream.quality,
                language=lang,
                headers=stream.headers,
                playable=stream.type != "iframe",
                priority=0,
            )
            score = _score_stream(stream, p.id)
            local_entries.append((entry, score))

        local_subs: list[SubtitleEntry] = []
        for sub in result.subtitles:
            if not sub.url or sub.url in subs_seen:
                continue
            local_subs.append(SubtitleEntry(
                provider=p.id,
                language=sub.language,
                label=sub.label or sub.language,
                url=sub.url,
                format=sub.format or "srt",
            ))

        # Determine outcome for circuit breaker
        if local_entries:
            outcome = "found"
        elif isinstance(result, _TimedOutResult):
            # Timeout or exception → failure signal for the circuit breaker
            outcome = "failed"
        else:
            # Provider responded but found nothing for this title — NOT a health problem
            outcome = "empty"

        # Record to circuit breaker stats
        await provider_stats.record(p.id, outcome, dur_ms)

        state = "found" if local_entries else "empty"

        async with lock:
            scored.extend(local_entries)
            for sub in local_subs:
                if sub.url not in subs_seen:
                    subs_seen.add(sub.url)
                    all_subs.append(sub)

        if on_provider_done:
            on_provider_done(p.id, state, [e for e, _ in local_entries], dur_ms)

    # Separate timeout tracking for failed providers
    async def invoke_with_failure_tracking(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            await invoke_one(p)
        except asyncio.TimeoutError:
            dur_ms = int((time.monotonic() - t0) * 1000)
            await provider_stats.record(p.id, "failed", dur_ms)
            if on_provider_done:
                on_provider_done(p.id, "failed", [], dur_ms)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            await provider_stats.record(p.id, "failed", dur_ms)
            if on_provider_done:
                on_provider_done(p.id, "failed", [], dur_ms)

    await asyncio.gather(*[invoke_with_failure_tracking(p) for p in eligible_providers])

    # Sort by score descending, assign sequential priorities
    scored.sort(key=lambda x: x[1], reverse=True)
    deduped = _dedup_streams(scored)
    for i, entry in enumerate(deduped):
        entry.priority = i

    return deduped, all_subs


# ── Download-link fan-out ─────────────────────────────────────────────────────

_DOWNLOAD_PROVIDER_IDS = {
    "vegamovies", "hdhub4u", "fourkhdhub", "rogmovies",
    "multimovies", "movies4u", "uhdmovies", "moviesmod",
    "topmovies", "bollyflix", "cinemacity",
}


async def run_download_providers(
    data: LinkData,
    kind: ContentKind,
) -> list[DownloadLink]:
    providers = [
        p for p in get_providers_for_kind(kind)
        if p.id in _DOWNLOAD_PROVIDER_IDS
    ]
    if not providers:
        return []

    # Apply circuit breaker here too
    eligible = [p for p in providers if await provider_stats.should_run(p.id)]
    if not eligible:
        return []

    lock = asyncio.Lock()
    out: list[DownloadLink] = []
    seen: set[str] = set()

    async def invoke_one(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            result = await safe_invoke(p, data, _settings.provider_timeout_ms)
            dur_ms = int((time.monotonic() - t0) * 1000)
            outcome = "found" if any(s.link for s in result.streams) else "empty"
            await provider_stats.record(p.id, outcome, dur_ms)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            await provider_stats.record(p.id, "failed", dur_ms)
            return

        async with lock:
            for stream in result.streams:
                if not stream.link or stream.link in seen:
                    continue
                if stream.type == "m3u8":
                    continue
                seen.add(stream.link)
                out.append(DownloadLink(
                    provider=p.name,
                    provider_id=p.id,
                    url=stream.link,
                    type=stream.type or "mp4",
                    quality=stream.quality,
                    language=_lang_label(stream.server),
                    headers=stream.headers,
                ))

    await asyncio.gather(*[invoke_one(p) for p in eligible])
    return out


# ── SSE event helpers ─────────────────────────────────────────────────────────

def _sse_event(event: str, data: dict | list | str) -> str:
    if isinstance(data, (dict, list)):
        payload = orjson.dumps(data).decode()
    else:
        payload = data
    return f"event: {event}\ndata: {payload}\n\n"


async def stream_sse_results(
    data: LinkData,
    kind: ContentKind,
) -> AsyncGenerator[str, None]:
    """
    Yields SSE frames as providers complete, then a final 'done' event.
    Used by GET /api/v1/streams/events.
    """
    queue: asyncio.Queue = asyncio.Queue()
    all_entries: list[StreamEntry] = []
    all_subs: list[SubtitleEntry] = []

    def on_done(pid: str, state: str, entries: list[StreamEntry], dur: int) -> None:
        queue.put_nowait(("provider", pid, state, entries, dur))

    async def run() -> None:
        streams, subs = await run_providers(data, kind, on_provider_done=on_done)
        all_entries.extend(streams)
        all_subs.extend(subs)
        queue.put_nowait(("done",))

    task = asyncio.ensure_future(run())

    while True:
        item = await queue.get()
        if item[0] == "done":
            break
        _, pid, state, entries, dur = item
        yield _sse_event("provider", {
            "id": pid,
            "state": state,
            "duration_ms": dur,
            "links": [
                {
                    "provider_id": e.provider_id,
                    "name": e.name,
                    "url": e.url,
                    "type": e.type,
                    "quality": e.quality,
                    "playable": e.playable,
                }
                for e in entries
            ],
        })

    await task

    yield _sse_event("done", {
        "streams": [e.model_dump() for e in all_entries],
        "subtitles": [s.model_dump() for s in all_subs],
        "total": len(all_entries),
    })
