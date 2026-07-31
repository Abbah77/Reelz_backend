"""
FastAPI routers — upgraded from original Reelz backend.

Endpoints:
  POST /api/v1/streams          → batch provider fan-out (cached)
  GET  /api/v1/streams/events   → SSE real-time stream as providers complete
  POST /api/v1/download         → download-link fan-out (cached)
  POST /api/v1/subtitles        → OpenSubtitles subtitle search
  GET  /api/v1/proxy            → HTTP proxy (SSRF-guarded, M3U8 rewrite)
  GET  /api/v1/health           → liveness check
  GET  /api/v1/stats            → cache stats + provider count
  GET  /api/v1/providers        → per-provider health + circuit breaker status
  POST /api/v1/providers/{id}/reset → manually reset a provider's circuit breaker

SSRF upgrade: /proxy now validates caller-supplied URLs through guard_url()
which normalises sneaky IPv4 encodings and blocks private/loopback IPs.
Also validates every redirect hop (via httpx follow_redirects + address check).
"""
from __future__ import annotations

import time
from typing import AsyncGenerator, Optional

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.cache import cache, download_key, stream_key, subtitle_key
from app.config import get_settings
from app.models import (
    DownloadRequest,
    DownloadResponse,
    StreamRequest,
    StreamResponse,
    SubtitleEntry,
    SubtitleRequest,
    SubtitleResponse,
)
from app.orchestrator import run_download_providers, run_providers, stream_sse_results
from app.providers.subtitles import download_opensubtitles, search_opensubtitles
from app.resolver import build_enriched_link_data
from app.utils.ssrf import guard_url, guard_resolved_url, is_private_address
from app.provider_stats import provider_stats

router = APIRouter(prefix="/api/v1")
_settings = get_settings()


# ── /health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True, "status": "running"}


# ── /stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def stats():
    from app.providers.base import get_providers, _disabled
    return {
        "cache_entries": cache.size(),
        "active_providers": len(get_providers()),
        "disabled_providers": len(_disabled),
    }


# ── /providers — per-provider health + circuit breaker ───────────────────────

@router.get("/providers")
async def providers_health():
    """
    Returns per-provider health stats including circuit breaker status.
    Useful for monitoring and debugging which providers are broken.
    """
    from app.providers.base import get_providers, _disabled
    all_stats = await provider_stats.get_all_stats()
    active = get_providers()
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
    """Manually reset a provider's circuit breaker (e.g. after fixing an issue)."""
    await provider_stats.reset(provider_id)
    return {"ok": True, "provider_id": provider_id, "message": "Circuit breaker reset"}


# ── POST /streams ─────────────────────────────────────────────────────────────

@router.post("/streams", response_model=StreamResponse)
async def get_streams(req: StreamRequest):
    t0 = time.monotonic()

    ckey = stream_key(req.tmdb_id, req.type, req.season, req.episode)
    cached = await cache.get(ckey)
    if cached:
        resp = StreamResponse(**cached)
        resp.cached = True
        resp.took_ms = int((time.monotonic() - t0) * 1000)
        return resp

    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)
    streams, subtitles = await run_providers(data, kind)

    took = int((time.monotonic() - t0) * 1000)

    if not streams:
        return StreamResponse(
            ok=False,
            streams=[],
            subtitles=[],
            error="No streams resolved",
            took_ms=took,
        )

    resp = StreamResponse(
        ok=True,
        streams=streams,
        subtitles=subtitles,
        cached=False,
        took_ms=took,
    )

    await cache.set(ckey, resp.model_dump(), ttl=_settings.cache_ttl_seconds)
    return resp


# ── GET /streams/events (SSE) ─────────────────────────────────────────────────

@router.get("/streams/events")
async def stream_events(
    tmdb_id: int = Query(...),
    type: str = Query(...),
    title: str = Query(...),
    imdb_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    season: Optional[int] = Query(None),
    episode: Optional[int] = Query(None),
):
    req = StreamRequest(
        tmdb_id=tmdb_id,
        type=type,
        title=title,
        imdb_id=imdb_id,
        year=year,
        season=season,
        episode=episode,
    )
    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)

    async def event_generator() -> AsyncGenerator[bytes, None]:
        async for chunk in stream_sse_results(data, kind):
            yield chunk.encode()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── POST /download ────────────────────────────────────────────────────────────

@router.post("/download", response_model=DownloadResponse)
async def get_downloads(req: DownloadRequest):
    ckey = download_key(req.tmdb_id, req.type, req.season, req.episode)
    cached = await cache.get(ckey)
    if cached:
        return DownloadResponse(**cached)

    data, kind = await build_enriched_link_data(req, _settings.tmdb_api_key)
    links = await run_download_providers(data, kind)

    resp = DownloadResponse(
        ok=bool(links),
        links=links,
        error=None if links else "No download links found",
    )

    if links:
        await cache.set(ckey, resp.model_dump(), ttl=_settings.cache_ttl_seconds * 2)

    return resp


# ── POST /subtitles ───────────────────────────────────────────────────────────

@router.post("/subtitles", response_model=SubtitleResponse)
async def get_subtitles(req: SubtitleRequest):
    ckey = subtitle_key(req.tmdb_id, req.type, req.season, req.episode, req.languages)
    cached = await cache.get(ckey)
    if cached:
        return SubtitleResponse(**cached)

    hits = await search_opensubtitles(
        tmdb_id=req.tmdb_id,
        media_type=req.type,
        season=req.season,
        episode=req.episode,
        languages=req.languages,
    )

    entries: list[SubtitleEntry] = []
    seen_file_ids: set[int] = set()
    for hit in hits:
        attrs = hit.get("attributes", {})
        file_info = (attrs.get("files") or [{}])[0]
        file_id = file_info.get("file_id")
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)

        dl_url = await download_opensubtitles(file_id)
        if not dl_url:
            continue

        entries.append(SubtitleEntry(
            provider="opensubtitles",
            language=attrs.get("language", "en"),
            label=attrs.get("language", "en").capitalize(),
            url=dl_url,
            format=(attrs.get("format") or "srt").lower(),
            rating=attrs.get("ratings"),
            downloads=attrs.get("download_count"),
        ))

    by_lang: dict[str, list[SubtitleEntry]] = {}
    for e in entries:
        by_lang.setdefault(e.language, []).append(e)
    final: list[SubtitleEntry] = []
    for lang in req.languages:
        final.extend(by_lang.get(lang, [])[:3])

    resp = SubtitleResponse(ok=bool(final), subtitles=final)
    if final:
        await cache.set(ckey, resp.model_dump(), ttl=_settings.cache_ttl_seconds * 4)

    return resp


# ── GET /proxy ────────────────────────────────────────────────────────────────
# UPGRADED: now SSRF-guarded via guard_url() + guard_resolved_url().
# Normalises sneaky IPv4 encodings (decimal, hex, octal) and validates that
# the hostname doesn't resolve to a private/loopback IP — stops DNS rebinding.
# Also checks every redirect hop through httpx event hooks.

@router.get("/proxy")
async def proxy_stream(
    url: str = Query(..., description="Target URL to proxy"),
    referer: Optional[str] = Query(None),
    origin: Optional[str] = Query(None),
):
    import httpx
    from urllib.parse import urljoin, urlparse, urlencode

    # ── SSRF layer 1: cheap up-front URL check ────────────────────────────────
    blocked = guard_url(url)
    if blocked:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked}")

    # ── SSRF layer 2: DNS resolution check ───────────────────────────────────
    blocked_resolved = await guard_resolved_url(url)
    if blocked_resolved:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked_resolved}")

    # Extension whitelist
    allowed_extensions = (".m3u8", ".ts", ".mp4", ".mkv", ".vtt", ".srt", ".ass")
    if not any(ext in url.lower() for ext in allowed_extensions):
        raise HTTPException(status_code=400, detail="Only media file URLs may be proxied")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
    }
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin

    from app.utils.http import get_client

    # Redirect guard: re-validate every hop's destination
    async def check_redirect(response: httpx.Response) -> None:
        location = response.headers.get("location", "")
        if location:
            reason = guard_url(location)
            if reason and reason != "bad url":
                raise httpx.RequestError(f"Blocked redirect: {reason}")
            resolved_reason = await guard_resolved_url(location)
            if resolved_reason:
                raise httpx.RequestError(f"Blocked redirect: {resolved_reason}")

    client = await get_client()
    try:
        upstream = await client.get(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=20,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    content_type = upstream.headers.get("content-type", "application/octet-stream")

    # For M3U8 playlists, rewrite relative segment URLs to go through proxy
    if ".m3u8" in url.lower() or "application/vnd" in content_type or "application/x-mpegurl" in content_type:
        text = upstream.text
        base_url_parts = urlparse(url)
        path_base = url.rsplit("/", 1)[0] + "/"

        rewritten_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                rewritten_lines.append(line)
                continue
            # Absolute URL → proxy it; relative → absolutise first
            if stripped.startswith("http"):
                seg_url = stripped
            else:
                seg_url = urljoin(path_base, stripped)

            # SSRF-check each segment URL too
            seg_blocked = guard_url(seg_url)
            if seg_blocked:
                rewritten_lines.append(f"# BLOCKED: {stripped}")
                continue

            proxy_params = {"url": seg_url}
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

    # Binary passthrough (TS segments, MP4, etc.)
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=3600",
        },
    )


# ── GET /flaresolverr/status ───────────────────────────────────────────────────

@router.get("/flaresolverr/status")
async def flare_status():
    from app.utils.http import _is_flaresolverr_configured, _flare_endpoints, _init_flare
    _init_flare()
    return {
        "configured": _is_flaresolverr_configured(),
        "endpoints": _flare_endpoints,
    }
