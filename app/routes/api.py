"""
FastAPI routers — Reelz backend (POST edition).

Endpoints:
  POST /api/v1/streams    → { ok, stream, streams, subtitles, cached, took_ms }
  POST /api/v1/download   → { ok, links, took_ms }
  POST /api/v1/subtitles  → { ok, subtitles, took_ms }
  GET  /api/v1/proxy      → HTTP proxy (SSRF-guarded, M3U8 rewrite)
  GET  /api/v1/health     → liveness check
  GET  /api/v1/stats      → cache stats + provider count
  GET  /api/v1/providers  → per-provider health + circuit breaker status
  POST /api/v1/providers/{id}/reset → manually reset a provider's circuit breaker

  (Legacy SSE kept at GET /api/v1/events for backward compat)

Speed design:
  /streams  uses run_providers_first_wins() — returns as soon as the FIRST valid
            m3u8 (or mp4) arrives from any provider. Typically 300-800 ms vs
            the old SSE first-event time of 1-3 s. The full fallback ladder is
            included in the response so the Android client has alternatives
            without needing a second request.

  /download uses run_download_providers_deduped() — expands m3u8 masters into
            per-resolution download links and deduplicates mp4 by quality label.
            No double 1080p or double 720p entries.

  Both endpoints are cached: same (type, tmdb_id, season, episode) within 8
  minutes replays instantly from the source_cache.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, Optional

import orjson
from fastapi import APIRouter, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.models import (
    DownloadLink,
    StreamEntry,
    StreamRequest,
    DownloadRequest,
    SubtitleRequest,
)
from app.orchestrator import (
    run_providers,
    run_providers_first_wins,
    run_download_providers,
)
from app.providers.subtitles import download_opensubtitles, search_opensubtitles
from app.resolver import build_enriched_link_data
from app.utils.ssrf import guard_url, guard_resolved_url
from app.provider_stats import provider_stats
from app.source_cache import source_cache, SourceCache
from app.utils.warp import normalize_warp_mode, warp_configured, run_with_warp

router = APIRouter(prefix="/api/v1")
_settings = get_settings()


# ── SSE helper (legacy) ───────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> bytes:
    payload = orjson.dumps(data).decode()
    return f"event: {event}\ndata: {payload}\n\n".encode()


# ── /health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True, "status": "running"}


# ── /stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def stats():
    from app.providers.base import get_providers, _disabled
    cache_stats = await source_cache.stats()
    return {
        "cache": cache_stats,
        "active_providers": len(get_providers()),
        "disabled_providers": len(_disabled),
        "warp_configured": warp_configured(),
    }


# ── /providers ────────────────────────────────────────────────────────────────

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
            "requires_warp": getattr(p, "requires_warp", False),
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


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/streams
# ══════════════════════════════════════════════════════════════════════════════
#
# Returns:
#   {
#     "ok": true,
#     "stream": { ...best_stream },       ← first valid URL (m3u8 preferred)
#     "streams": [ ...all_streams ],      ← full fallback ladder, sorted by priority
#     "subtitles": [],
#     "cached": false,
#     "took_ms": 420
#   }
#
# Speed: uses run_providers_first_wins() which breaks out of the provider fan-out
# the moment ANY provider returns a valid m3u8. Other providers' results are
# collected concurrently and included in "streams" for the fallback ladder.

@router.post("/streams")
async def post_streams(
    req: StreamRequest,
    fresh: int = Query(0),
    warp: Optional[str] = Query(None),
):
    t0 = time.monotonic()
    warp_mode = normalize_warp_mode(warp)
    cache_key = SourceCache.make_key(req.type, req.tmdb_id, req.season, req.episode)

    # ── Cache fast path ───────────────────────────────────────────────────────
    if not fresh:
        cached = await source_cache.get(cache_key)
        if cached:
            # Reconstruct response from cached events
            streams   = [d for name, d in cached.events if name == "stream"]
            subtitles = [d for name, d in cached.events if name == "subtitle"]
            best = next(
                (s for s in streams if s.get("type") == "m3u8"),
                streams[0] if streams else None,
            )
            took_ms = int((time.monotonic() - t0) * 1000)
            return {
                "ok": bool(best),
                "stream": best,
                "streams": streams,
                "subtitles": subtitles,
                "cached": True,
                "took_ms": took_ms,
            }

    # ── Live resolve ──────────────────────────────────────────────────────────
    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)

    winner, all_entries = await run_with_warp(
        lambda: run_providers_first_wins(data, kind),
        mode=warp_mode,
    )

    took_ms = int((time.monotonic() - t0) * 1000)

    streams_out = [
        {
            "provider":    e.provider,
            "provider_id": e.provider_id,
            "name":        e.name,
            "url":         e.url,
            "type":        e.type,
            "quality":     e.quality,
            "language":    e.language,
            "headers":     e.headers,
            "playable":    e.playable,
            "priority":    e.priority,
        }
        for e in all_entries
    ]

    winner_out = None
    if winner:
        winner_out = {
            "provider":    winner.provider,
            "provider_id": winner.provider_id,
            "name":        winner.name,
            "url":         winner.url,
            "type":        winner.type,
            "quality":     winner.quality,
            "language":    winner.language,
            "headers":     winner.headers,
            "playable":    winner.playable,
            "priority":    winner.priority,
        }

    # Cache if we got at least one stream
    if streams_out:
        events = [("stream", s) for s in streams_out]
        await source_cache.set(cache_key, events)

    return {
        "ok": winner_out is not None,
        "stream": winner_out,
        "streams": streams_out,
        "subtitles": [],
        "cached": False,
        "took_ms": took_ms,
        "error": None if winner_out else "No streams resolved",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/download
# ══════════════════════════════════════════════════════════════════════════════
#
# Returns:
#   {
#     "ok": true,
#     "links": [
#       { "provider", "provider_id", "url", "type", "quality", "language",
#         "size_bytes", "size_label", "headers" },
#       ...
#     ],
#     "took_ms": 1240
#   }
#
# Quality dedup rules:
#   - m3u8 master → expanded into per-resolution variant stream URLs
#     (1080p, 720p, 480p, 360p, 240p) via playlist parsing
#   - mp4/mkv → deduplicated by normalised quality label PER language
#   - No duplicate qualities (e.g. two 1080p English entries)
#   - Sorted: best quality first within each language group

@router.post("/download")
async def post_download(
    req: DownloadRequest,
    request: Request,
    fresh: int = Query(0),
    warp: Optional[str] = Query(None),
):
    t0 = time.monotonic()
    warp_mode = normalize_warp_mode(warp)
    cache_key = "dl:" + SourceCache.make_key(req.type, req.tmdb_id, req.season, req.episode)

    # ── Cache fast path ───────────────────────────────────────────────────────
    if not fresh:
        cached = await source_cache.get(cache_key)
        if cached:
            links = [d for name, d in cached.events if name == "download"]
            # Quick liveness spot-check: if any link has a raw `url` field,
            # HEAD it to confirm it isn't a dead CDN URL before serving stale data.
            # We only check the first link to keep latency low — if it's dead,
            # bust the cache and re-resolve everything.
            if links:
                sample_url = links[0].get("url") if isinstance(links[0], dict) else getattr(links[0], "url", None)
                if sample_url:
                    try:
                        from app.utils.http import get_client as _get_client
                        _cl = await _get_client()
                        _r = await _cl.head(sample_url, timeout=4, follow_redirects=True)
                        _alive = _r.status_code < 400
                    except Exception:
                        _alive = False
                    if not _alive:
                        # Evict stale cache entry and fall through to live resolve
                        await source_cache.delete(cache_key) if hasattr(source_cache, "delete") else None
                        links = []

            if links:
                took_ms = int((time.monotonic() - t0) * 1000)
                return {
                    "ok": True,
                    "links": links,
                    "cached": True,
                    "took_ms": took_ms,
                }

    # ── Live resolve ──────────────────────────────────────────────────────────
    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)

    links: list[DownloadLink] = await run_with_warp(
        lambda: run_download_providers(data, kind),
        mode=warp_mode,
    )

    took_ms = int((time.monotonic() - t0) * 1000)

    def _fmt_size(b: int | None) -> str | None:
        if not b:
            return None
        for unit, div in (("GB", 1_073_741_824), ("MB", 1_048_576), ("KB", 1_024)):
            if b >= div:
                return f"{b/div:.1f} {unit}"
        return f"{b} B"

    def _build_download_url(lnk) -> str | None:
        """
        Build the best download URL for a link:

        - mp4 / mkv  → always route through /api/v1/download-proxy so the
                        browser receives Content-Disposition: attachment and any
                        required Referer/Origin headers are injected server-side.
        - m3u8       → route through /api/v1/proxy so the playlist is rewritten
                        with proxied segment URLs (required for Referer-gated
                        HLS streams).  The client (ExoPlayer / AVPlayer) handles
                        HLS natively — there is no single file to download.
        """
        from urllib.parse import urlencode
        base_url = str(request.base_url).rstrip("/")

        if lnk.type == "m3u8":
            # Proxy the HLS playlist so segment requests include required headers
            params: dict[str, str] = {"url": lnk.url}
            if lnk.headers.get("Referer"):
                params["referer"] = lnk.headers["Referer"]
            if lnk.headers.get("Origin"):
                params["origin"] = lnk.headers["Origin"]
            return f"{base_url}/api/v1/proxy?{urlencode(params)}"

        # mp4 / mkv — force download via proxy
        params = {"url": lnk.url}
        if lnk.headers.get("Referer"):
            params["referer"] = lnk.headers["Referer"]
        if lnk.headers.get("Origin"):
            params["origin"] = lnk.headers["Origin"]
        quality = lnk.quality or "video"
        ext = "mkv" if lnk.type == "mkv" else "mp4"
        params["filename"] = f"{req.title} {quality}.{ext}"
        return f"{base_url}/api/v1/download-proxy?{urlencode(params)}"

    links_out = [
        {
            "provider":      lnk.provider,
            "provider_id":   lnk.provider_id,
            "url":           lnk.url,           # original URL (may need headers)
            "download_url":  _build_download_url(lnk),  # ready-to-use download link
            "type":          lnk.type,
            "quality":       lnk.quality,
            "language":      lnk.language,
            "size_bytes":    lnk.size_bytes,
            "size_label":    _fmt_size(lnk.size_bytes),
            "headers":       lnk.headers,
        }
        for lnk in links
    ]

    # Cache if we got results
    if links_out:
        events = [("download", l) for l in links_out]
        await source_cache.set(cache_key, events)

    return {
        "ok": bool(links_out),
        "links": links_out,
        "cached": False,
        "took_ms": took_ms,
        "error": None if links_out else "No download links resolved",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/subtitles
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/subtitles")
async def post_subtitles(req: SubtitleRequest):
    t0 = time.monotonic()
    lang_list = req.languages or ["en"]

    try:
        hits = await search_opensubtitles(
            tmdb_id=req.tmdb_id,
            media_type=req.type,
            season=req.season,
            episode=req.episode,
            languages=lang_list,
        )
        seen: set[int] = set()
        subtitles_out = []

        # Fetch download URLs concurrently
        async def resolve_sub(hit: dict):
            attrs     = hit.get("attributes", {})
            file_info = (attrs.get("files") or [{}])[0]
            file_id   = file_info.get("file_id")
            if not file_id or file_id in seen:
                return None
            seen.add(file_id)
            dl_url = await download_opensubtitles(file_id)
            if not dl_url:
                return None
            return {
                "provider":  "opensubtitles",
                "language":  attrs.get("language", "en"),
                "label":     attrs.get("language", "en").capitalize(),
                "url":       dl_url,
                "format":    (attrs.get("format") or "srt").lower(),
                "rating":    attrs.get("ratings"),
                "downloads": attrs.get("download_count"),
            }

        results = await asyncio.gather(*[resolve_sub(h) for h in hits], return_exceptions=True)
        subtitles_out = [r for r in results if r and not isinstance(r, Exception)]

    except Exception as e:
        took_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "subtitles": [], "error": str(e), "took_ms": took_ms}

    took_ms = int((time.monotonic() - t0) * 1000)
    return {
        "ok": True,
        "subtitles": subtitles_out,
        "took_ms": took_ms,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/events  — LEGACY SSE (kept for backward compat)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/events")
async def unified_events(
    request: Request,
    tmdb_id:  int            = Query(...),
    type:     str            = Query(...),
    title:    str            = Query(...),
    imdb_id:  Optional[str]  = Query(None),
    year:     Optional[int]  = Query(None),
    season:   Optional[int]  = Query(None),
    episode:  Optional[int]  = Query(None),
    languages: str           = Query("en"),
    fresh:    int            = Query(0),
    warp:     Optional[str]  = Query(None),
):
    req = StreamRequest(
        tmdb_id=tmdb_id, type=type, title=title,
        imdb_id=imdb_id, year=year, season=season, episode=episode,
    )
    lang_list = [l.strip() for l in languages.split(",") if l.strip()] or ["en"]
    warp_mode = normalize_warp_mode(warp)
    cache_key = SourceCache.make_key(type, tmdb_id, season, episode)

    if not fresh:
        cached = await source_cache.get(cache_key)
        if cached:
            async def _replay() -> AsyncGenerator[bytes, None]:
                yield _sse("log", {"msg": "Loaded cached sources."})
                for event_name, data in cached.events:
                    yield _sse(event_name, data)
            return StreamingResponse(
                _replay(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
            )

    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    abort_event = asyncio.Event()
    recorded: list[tuple[str, dict]] = []
    counters = {"streams": 0, "downloads": 0, "subtitles": 0, "done": 0}
    lock = asyncio.Lock()

    def _record(event_name: str, data: dict) -> bytes:
        if event_name != "log":
            recorded.append((event_name, data))
        return _sse(event_name, data)

    async def _task_done() -> None:
        async with lock:
            counters["done"] += 1
            if counters["done"] == 3:
                frame = _record("done", {
                    "streams_total":   counters["streams"],
                    "downloads_total": counters["downloads"],
                    "subtitles_total": counters["subtitles"],
                })
                await queue.put(frame)
                await queue.put(None)

    async def stream_task() -> None:
        def on_provider_done(pid, state, entries, dur):
            if abort_event.is_set():
                return
            async def _push():
                if abort_event.is_set():
                    return
                await queue.put(_record("provider", {"id": pid, "state": state, "duration_ms": dur}))
                for e in entries:
                    if not e.playable or abort_event.is_set():
                        continue
                    await queue.put(_record("stream", {
                        "provider_id": e.provider_id, "name": e.name,
                        "url": e.url, "type": e.type, "quality": e.quality,
                        "headers": e.headers, "playable": e.playable,
                        "language": e.language, "priority": e.priority,
                    }))
                    counters["streams"] += 1
            asyncio.ensure_future(_push())
        try:
            await run_with_warp(
                lambda: run_providers(data, kind, on_provider_done=on_provider_done),
                mode=warp_mode,
            )
        except Exception:
            pass
        finally:
            await _task_done()

    async def download_task() -> None:
        try:
            links = await run_with_warp(lambda: run_download_providers(data, kind), mode=warp_mode)
            for lnk in links:
                if abort_event.is_set():
                    break
                await queue.put(_record("download", {
                    "provider_id": lnk.provider_id, "name": lnk.provider,
                    "url": lnk.url, "type": lnk.type, "quality": lnk.quality,
                    "language": lnk.language, "size_bytes": lnk.size_bytes,
                }))
                counters["downloads"] += 1
        except Exception:
            pass
        finally:
            await _task_done()

    async def subtitle_task() -> None:
        try:
            hits = await search_opensubtitles(
                tmdb_id=req.tmdb_id, media_type=req.type,
                season=req.season, episode=req.episode, languages=lang_list,
            )
            seen: set[int] = set()
            for hit in hits:
                if abort_event.is_set():
                    break
                attrs     = hit.get("attributes", {})
                file_info = (attrs.get("files") or [{}])[0]
                file_id   = file_info.get("file_id")
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)
                dl_url = await download_opensubtitles(file_id)
                if not dl_url:
                    continue
                await queue.put(_record("subtitle", {
                    "provider":  "opensubtitles", "language": attrs.get("language", "en"),
                    "label":     attrs.get("language", "en").capitalize(),
                    "url":       dl_url, "format": (attrs.get("format") or "srt").lower(),
                    "rating":    attrs.get("ratings"), "downloads": attrs.get("download_count"),
                }))
                counters["subtitles"] += 1
        except Exception:
            pass
        finally:
            await _task_done()

    async def event_generator() -> AsyncGenerator[bytes, None]:
        tasks = [
            asyncio.ensure_future(stream_task()),
            asyncio.ensure_future(download_task()),
            asyncio.ensure_future(subtitle_task()),
        ]
        try:
            while True:
                disconnected = await request.is_disconnected()
                if disconnected:
                    abort_event.set()
                    for t in tasks:
                        t.cancel()
                    return
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if chunk is None:
                    disconnected = await request.is_disconnected()
                    if not disconnected and any(e == "stream" for e, _ in recorded):
                        await source_cache.set(cache_key, recorded)
                    return
                yield chunk
        except asyncio.CancelledError:
            abort_event.set()
            for t in tasks:
                t.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── GET /proxy ────────────────────────────────────────────────────────────────

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
    if referer: headers["Referer"] = referer
    if origin:  headers["Origin"]  = origin

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
            if referer: proxy_params["referer"] = referer
            if origin:  proxy_params["origin"]  = origin
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


@router.get("/download-proxy")
async def download_proxy(
    request:  Request,
    url:      str           = Query(..., description="Direct media URL to proxy as a download"),
    filename: Optional[str] = Query(None, description="Override filename for Content-Disposition"),
    referer:  Optional[str] = Query(None),
    origin:   Optional[str] = Query(None),
):
    """
    Proxy a direct mp4/mkv URL and force browser/app file download via
    Content-Disposition: attachment.

    Key behaviours:
      - Streams the response body in chunks — never loads the whole file into
        memory, so it works correctly for multi-GB files.
      - Forwards the client's Range header to the upstream CDN, enabling
        resumable downloads and seeking in video players.
      - Injects required Referer/Origin headers that the client can't send
        directly (CORS / hotlink protection).
      - Returns 404/502 if the upstream URL is dead so the client knows
        immediately rather than getting a silent empty file.

    Usage: GET /api/v1/download-proxy?url=<encoded_url>&filename=movie.mp4
    """
    from urllib.parse import unquote
    import posixpath
    import httpx

    blocked = guard_url(url)
    if blocked:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked}")

    blocked_resolved = await guard_resolved_url(url)
    if blocked_resolved:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked_resolved}")

    allowed_extensions = (".m3u8", ".ts", ".mp4", ".mkv", ".webm", ".vtt", ".srt")
    if not any(ext in url.lower() for ext in allowed_extensions):
        raise HTTPException(status_code=400, detail="Only media file URLs may be proxied for download")

    req_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
    }
    if referer: req_headers["Referer"] = referer
    if origin:  req_headers["Origin"]  = origin

    # Forward Range header from client so resumable downloads / partial content work
    client_range = request.headers.get("Range")
    if client_range:
        req_headers["Range"] = client_range

    from app.utils.http import get_client
    client = await get_client()

    # Use stream=True so we never buffer the whole file
    try:
        upstream_request = client.build_request(
            "GET", url, headers=req_headers
        )
        upstream = await client.send(upstream_request, follow_redirects=True, stream=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream connection failed: {exc}")

    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(
            status_code=upstream.status_code,
            detail=f"Upstream returned {upstream.status_code} — URL may be dead or expired",
        )

    content_type = upstream.headers.get("content-type", "application/octet-stream")

    # Derive a sensible filename
    if not filename:
        final_url = str(upstream.url)
        path = posixpath.basename(final_url.split("?")[0])
        filename = unquote(path) if path else "download.mp4"

    # Sanitise filename (no quotes or newlines in Content-Disposition)
    filename = filename.replace('"', "'").replace("\n", "").replace("\r", "")

    resp_headers: dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=3600",
    }

    # Pass through Content-Length and Content-Range so clients can show progress
    # and resume interrupted downloads
    for hdr in ("content-length", "content-range", "accept-ranges"):
        val = upstream.headers.get(hdr)
        if val:
            resp_headers[hdr.title()] = val

    # Determine correct HTTP status: 206 Partial Content when upstream honours Range
    status_code = upstream.status_code  # typically 200 or 206

    async def _stream_body():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _stream_body(),
        status_code=status_code,
        media_type=content_type,
        headers=resp_headers,
    )


@router.get("/flaresolverr/status")
async def flare_status():
    from app.utils.http import _is_flaresolverr_configured, _flare_endpoints, _init_flare
    _init_flare()
    return {"configured": _is_flaresolverr_configured(), "endpoints": _flare_endpoints}
