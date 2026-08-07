"""
api/downloads.py — POST /api/v1/download + GET /api/v1/download-proxy

Rate-limited to 10 requests/minute per client IP (same as streams).
Auth enforced by verify_token middleware in main.py.
Routes are intentionally thin — all business logic lives in managers/download.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.main import limiter
from app.managers.download import get_downloads
from app.schemas.request import DownloadRequest
from app.utils.ssrf import guard_url, guard_resolved_url

router = APIRouter(prefix="/api/v1")


@router.post("/download")
@limiter.limit("10/minute")
async def post_download(
    request: Request,          # Required by slowapi for rate-limit key extraction
    req: DownloadRequest,
    fresh: int = Query(0),
    warp: Optional[str] = Query(None),
):
    base_url = str(request.base_url)
    return await get_downloads(req, base_url, fresh=bool(fresh), warp_mode=warp or "off")


@router.get("/download-proxy")
async def download_proxy(
    url:      str           = Query(..., description="Direct media URL to proxy as download"),
    filename: Optional[str] = Query(None),
    referer:  Optional[str] = Query(None),
    origin:   Optional[str] = Query(None),
):
    """
    Proxy a direct mp4/mkv URL and force browser download via Content-Disposition.
    Needed for URLs that require Referer/Origin headers the client can't set directly.
    """
    import posixpath
    from urllib.parse import unquote

    blocked = guard_url(url)
    if blocked:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked}")

    blocked_resolved = await guard_resolved_url(url)
    if blocked_resolved:
        raise HTTPException(status_code=403, detail=f"Blocked: {blocked_resolved}")

    allowed = (".m3u8", ".ts", ".mp4", ".mkv", ".webm", ".vtt", ".srt")
    if not any(ext in url.lower() for ext in allowed):
        raise HTTPException(status_code=400, detail="Only media file URLs may be proxied")

    req_headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/137 Safari/537.36"
        )
    }
    if referer:
        req_headers["Referer"] = referer
    if origin:
        req_headers["Origin"] = origin

    from app.clients.http import get_client
    client = await get_client()
    try:
        upstream = await client.get(url, headers=req_headers, follow_redirects=True, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="Upstream returned error")

    if not filename:
        path = posixpath.basename(str(upstream.url).split("?")[0])
        filename = unquote(path) if path else "download.mp4"
    filename = filename.replace('"', "'").replace("\n", "").replace("\r", "")

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    resp_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=3600",
    }
    cl = upstream.headers.get("content-length")
    if cl:
        resp_headers["Content-Length"] = cl

    return Response(content=upstream.content, media_type=content_type, headers=resp_headers)
