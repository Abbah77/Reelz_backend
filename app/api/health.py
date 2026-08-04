"""
api/health.py — health, stats, provider management, and stream proxy endpoints.

Routes:
  GET  /api/v1/health                   → liveness check
  GET  /api/v1/health/stats             → cache stats + provider counts
  GET  /api/v1/health/providers         → per-provider health + circuit breaker
  POST /api/v1/health/providers/{id}/reset → reset a provider's circuit breaker
  GET  /api/v1/proxy                    → SSRF-guarded m3u8/media proxy
  GET  /api/v1/flaresolverr/status      → FlareSolverr availability
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.managers.provider_stats import provider_stats
from app.utils.ssrf import guard_url, guard_resolved_url
from app.utils.warp import warp_configured

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health():
    return {"ok": True, "status": "running"}


@router.get("/health/stats")
async def stats():
    from app.providers.stream.registry import get_stream_providers, DISABLED as stream_disabled
    from app.providers.download.registry import get_download_providers, DISABLED as dl_disabled
    from app.cache import cache
    cache_stats = await cache.stats()
    return {
        "cache": cache_stats,
        "stream_providers": len(get_stream_providers()),
        "download_providers": len(get_download_providers()),
        "disabled_providers": len(stream_disabled) + len(dl_disabled),
        "warp_configured": warp_configured(),
    }


@router.get("/health/providers")
async def providers_health():
    from app.providers.stream.registry import get_stream_providers, DISABLED as stream_disabled
    from app.providers.download.registry import get_download_providers, DISABLED as dl_disabled

    all_stats = await provider_stats.get_all_stats()
    result = []

    for p in get_stream_providers() + get_download_providers():
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

    for p in stream_disabled + dl_disabled:
        result.append({
            "id": p.id, "name": p.name, "enabled": False,
            "circuit_broken": False, "success_rate": None, "avg_time_ms": None,
        })

    return {"providers": result}


@router.post("/health/providers/{provider_id}/reset")
async def reset_provider(provider_id: str):
    await provider_stats.reset(provider_id)
    return {"ok": True, "provider_id": provider_id, "message": "Circuit breaker reset"}


# ── Stream proxy ───────────────────────────────────────────────────────────────

@router.get("/proxy")
async def proxy_stream(
    url:     str           = Query(...),
    referer: Optional[str] = Query(None),
    origin:  Optional[str] = Query(None),
):
    blocked = guard_url(url)
    if blocked:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked}")

    blocked_resolved = await guard_resolved_url(url)
    if blocked_resolved:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked_resolved}")

    allowed = (".m3u8", ".ts", ".mp4", ".mkv", ".vtt", ".srt", ".ass")
    if not any(ext in url.lower() for ext in allowed):
        raise HTTPException(status_code=400, detail="Only media file URLs may be proxied")

    headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36"
    }
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin

    from app.clients.http import get_client
    client = await get_client()
    try:
        upstream = await client.get(url, headers=headers, follow_redirects=True, timeout=20)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    content_type = upstream.headers.get("content-type", "application/octet-stream")

    # Rewrite m3u8 playlists so segment URLs route through our proxy
    if ".m3u8" in url.lower() or "application/vnd" in content_type or "x-mpegurl" in content_type:
        path_base = url.rsplit("/", 1)[0] + "/"
        rewritten = []
        for line in upstream.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                rewritten.append(line)
                continue
            seg_url = stripped if stripped.startswith("http") else urljoin(path_base, stripped)
            if guard_url(seg_url):
                rewritten.append(f"# BLOCKED: {stripped}")
                continue
            proxy_params: dict[str, str] = {"url": seg_url}
            if referer:
                proxy_params["referer"] = referer
            if origin:
                proxy_params["origin"] = origin
            rewritten.append(f"/api/v1/proxy?{urlencode(proxy_params)}")
        return Response(
            content="\n".join(rewritten).encode(),
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
    )


@router.get("/flaresolverr/status")
async def flare_status():
    from app.clients.http import _is_flaresolverr_configured, _flare_endpoints, _init_flare
    _init_flare()
    return {"configured": _is_flaresolverr_configured(), "endpoints": _flare_endpoints}
