"""
FastAPI routers — Reelz backend.

Endpoints:
  GET  /api/v1/events           → UNIFIED SSE: streams + downloads + subtitles in one connection
  GET  /api/v1/proxy            → HTTP proxy (SSRF-guarded, M3U8 rewrite)
  GET  /api/v1/health           → liveness check
  GET  /api/v1/stats            → cache stats + provider count
  GET  /api/v1/providers        → per-provider health + circuit breaker status
  POST /api/v1/providers/{id}/reset → manually reset a provider's circuit breaker

SSE event types emitted by /api/v1/events:
  stream    → { provider_id, name, url, type, quality, headers, playable, language, priority }
  download  → { provider_id, name, url, type, quality, language, size_bytes }
  subtitle  → { provider, language, label, url, format, rating, downloads }
  provider  → { id, state, duration_ms }
  done      → { streams_total, downloads_total, subtitles_total }

The app opens ONE connection. It plays the first 'stream' event immediately.
Downloads and subtitles arrive in the background — no waiting, no polling.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Optional

import orjson
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.models import (
    DownloadLink,
    StreamEntry,
    StreamRequest,
)
from app.orchestrator import run_providers, run_download_providers
from app.providers.subtitles import download_opensubtitles, search_opensubtitles
from app.resolver import build_enriched_link_data
from app.utils.ssrf import guard_url, guard_resolved_url
from app.provider_stats import provider_stats

router = APIRouter(prefix="/api/v1")
_settings = get_settings()


# ── helpers ─────────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> bytes:
    payload = orjson.dumps(data).decode()
    return f"event: {event}\ndata: {payload}\n\n".encode()


# ── /health ─────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True, "status": "running"}


# ── /stats ──────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def stats():
    from app.providers.base import get_providers, _disabled
    return {
        "cache_entries": 0,
        "active_providers": len(get_providers()),
        "disabled_providers": len(_disabled),
    }


# ── /providers ──────────────────────────────────────────────────────────────────

@router.get("/providers")
async def providers_health():
    from app.providers.base import get_providers, _disabled
    all_stats = await provider_stats.get_all_stats()
    active   = get_providers()
    disabled = _disabled

    result = []
    for p in active:
        stat = all_stats.get(p.id, {})
        result.append({
            "id": p.id,
            "name": p.name,
            "enabled": True,
            "circuit_broken": stat.get("is_circuit_broken", False),
            "success_rate": stat.get("success_rate", 1.0),
            "avg_time_ms": stat.get("avg_time_ms", 0),
            "success_count": stat.get("success_count", 0),
            "failure_count": stat.get("failure_count", 0),
            "consecutive_failures": stat.get("consecutive_failures", 0),
            "last_outcome": stat.get("last_outcome"),
        })
    for p in disabled:
        result.append({
            "id": p.id,
            "name": p.name,
            "enabled": False,
            "circuit_broken": False,
            "success_rate": None,
            "avg_time_ms": None,
        })
    return {"providers": result}


@router.post("/providers/{provider_id}/reset")
async def reset_provider(provider_id: str):
    await provider_stats.reset(provider_id)
    return {"ok": True, "provider_id": provider_id, "message": "Circuit breaker reset"}


# ── GET /events — UNIFIED SSE ────────────────────────────────────────────────────
#
# Query params (all GET):
#   tmdb_id   int      required
#   type      str      "movie" | "tv"
#   title     str      required
#   imdb_id   str?
#   year      int?
#   season    int?
#   episode   int?
#   languages str      comma-separated language codes, default "en"
#
# Three async tasks run in parallel from the moment the connection opens:
#
#   Task 1 — stream providers
#     Each provider that returns results fires individual 'stream' events.
#     Provider A finishes in 100 ms → 'stream' emitted at 100 ms.
#     Provider B finishes in 600 ms → 'stream' emitted at 600 ms.
#     The app plays the first one. The rest become the fallback ladder.
#
#   Task 2 — download providers
#     Same fan-out, fires 'download' events per quality link as they arrive.
#
#   Task 3 — subtitle search
#     OpenSubtitles search runs concurrently. Each result fires a 'subtitle' event.
#
#   When all three tasks have finished, a single 'done' event is sent and the
#   connection closes. The app needs no polling, no second request, nothing.

@router.get("/events")
async def unified_events(
    tmdb_id:  int            = Query(...),
    type:     str            = Query(...),
    title:    str            = Query(...),
    imdb_id:  Optional[str]  = Query(None),
    year:     Optional[int]  = Query(None),
    season:   Optional[int]  = Query(None),
    episode:  Optional[int]  = Query(None),
    languages: str           = Query("en"),
):
    req = StreamRequest(
        tmdb_id=tmdb_id, type=type, title=title,
        imdb_id=imdb_id, year=year, season=season, episode=episode,
    )
    lang_list = [l.strip() for l in languages.split(",") if l.strip()] or ["en"]

    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)

    # All three tasks push into this queue. None is the sentinel that closes the stream.
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    counters = {"streams": 0, "downloads": 0, "subtitles": 0, "done": 0}
    lock = asyncio.Lock()

    async def _task_done():
        async with lock:
            counters["done"] += 1
            if counters["done"] == 3:
                await queue.put(_sse("done", {
                    "streams_total":   counters["streams"],
                    "downloads_total": counters["downloads"],
                    "subtitles_total": counters["subtitles"],
                }))
                await queue.put(None)  # close the stream

    # ── Task 1: stream providers ─────────────────────────────────────────────
    async def stream_task():
        def on_provider_done(pid: str, state: str, entries: list[StreamEntry], dur: int) -> None:
            # This callback is called synchronously inside run_providers().
            # We schedule async work with ensure_future so we don't block the fan-out.
            async def _push():
                await queue.put(_sse("provider", {"id": pid, "state": state, "duration_ms": dur}))
                for e in entries:
                    if not e.playable:
                        continue
                    await queue.put(_sse("stream", {
                        "provider_id": e.provider_id,
                        "name":        e.name,
                        "url":         e.url,
                        "type":        e.type,
                        "quality":     e.quality,
                        "headers":     e.headers,
                        "playable":    e.playable,
                        "language":    e.language,
                        "priority":    e.priority,
                    }))
                    counters["streams"] += 1
            asyncio.ensure_future(_push())

        await run_providers(data, kind, on_provider_done=on_provider_done)
        await _task_done()

    # ── Task 2: download providers ───────────────────────────────────────────
    async def download_task():
        links: list[DownloadLink] = await run_download_providers(data, kind)
        for lnk in links:
            await queue.put(_sse("download", {
                "provider_id": lnk.provider_id,
                "name":        lnk.provider,
                "url":         lnk.url,
                "type":        lnk.type,
                "quality":     lnk.quality,
                "language":    lnk.language,
                "size_bytes":  lnk.size_bytes,
            }))
            counters["downloads"] += 1
        await _task_done()

    # ── Task 3: subtitle search ──────────────────────────────────────────────
    async def subtitle_task():
        try:
            hits = await search_opensubtitles(
                tmdb_id=req.tmdb_id, media_type=req.type,
                season=req.season, episode=req.episode, languages=lang_list,
            )
            seen: set[int] = set()
            for hit in hits:
                attrs    = hit.get("attributes", {})
                file_info = (attrs.get("files") or [{}])[0]
                file_id  = file_info.get("file_id")
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)
                dl_url = await download_opensubtitles(file_id)
                if not dl_url:
                    continue
                await queue.put(_sse("subtitle", {
                    "provider":  "opensubtitles",
                    "language":  attrs.get("language", "en"),
                    "label":     attrs.get("language", "en").capitalize(),
                    "url":       dl_url,
                    "format":    (attrs.get("format") or "srt").lower(),
                    "rating":    attrs.get("ratings"),
                    "downloads": attrs.get("download_count"),
                }))
                counters["subtitles"] += 1
        except Exception:
            pass  # subtitles are best-effort; never block streams
        await _task_done()

    # ── Generator ────────────────────────────────────────────────────────────
    async def event_generator() -> AsyncGenerator[bytes, None]:
        asyncio.ensure_future(stream_task())
        asyncio.ensure_future(download_task())
        asyncio.ensure_future(subtitle_task())
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ── GET /proxy ───────────────────────────────────────────────────────────────────

@router.get("/proxy")
async def proxy_stream(
    url:     str           = Query(..., description="Target URL to proxy"),
    referer: Optional[str] = Query(None),
    origin:  Optional[str] = Query(None),
):
    from urllib.parse import urljoin, urlencode

    blocked = guard_url(url)
    if blocked:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked}")

    blocked_resolved = await guard_resolved_url(url)
    if blocked_resolved:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked_resolved}")

    allowed_extensions = (".m3u8", ".ts", ".mp4", ".mkv", ".vtt", ".srt", ".ass")
    if not any(ext in url.lower() for ext in allowed_extensions):
        raise HTTPException(status_code=400, detail="Only media file URLs may be proxied")

    headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
    }
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin

    from app.utils.http import get_client
    client = await get_client()
    try:
        upstream = await client.get(url, headers=headers, follow_redirects=True, timeout=20)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    content_type = upstream.headers.get("content-type", "application/octet-stream")

    if ".m3u8" in url.lower() or "application/vnd" in content_type or "application/x-mpegurl" in content_type:
        path_base = url.rsplit("/", 1)[0] + "/"
        rewritten_lines = []
        for line in upstream.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                rewritten_lines.append(line)
                continue
            seg_url = stripped if stripped.startswith("http") else urljoin(path_base, stripped)
            if guard_url(seg_url):
                rewritten_lines.append(f"# BLOCKED: {stripped}")
                continue
            proxy_params: dict[str, str] = {"url": seg_url}
            if referer:
                proxy_params["referer"] = referer
            if origin:
                proxy_params["origin"] = origin
            rewritten_lines.append(f"/api/v1/proxy?{urlencode(proxy_params)}")
        return Response(
            content="\n".join(rewritten_lines).encode(),
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
    )


# ── GET /flaresolverr/status ─────────────────────────────────────────────────────

@router.get("/flaresolverr/status")
async def flare_status():
    from app.utils.http import _is_flaresolverr_configured, _flare_endpoints, _init_flare
    _init_flare()
    return {"configured": _is_flaresolverr_configured(), "endpoints": _flare_endpoints}
