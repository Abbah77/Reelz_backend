"""
ENGINE/manager/download.py — Download manager.

Flow:
    1. Cache → return all quality links instantly if hit
    2. Fan-out to all download providers concurrently
    3. Collect every quality link (360p, 480p, 720p, 1080p, 4K)
    4. Build proxied download URLs
    5. Cache + return
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode
from typing import Optional

from ENGINE.cache.cache import get as cache_get, set as cache_set, download_key
from ENGINE.manager.health import record, should_run
from ENGINE.manager.tmdb import enrich
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Download.registry import get_all
from config import get_settings

_s = get_settings()


def _proxy_url(base: str, url: str, filename: Optional[str], headers: dict) -> str:
    params = {"url": url}
    if filename:
        params["filename"] = filename
    if headers.get("Referer"):
        params["referer"] = headers["Referer"]
    return f"{base.rstrip('/')}/download/proxy?{urlencode(params)}"


async def get_downloads(req, base_url: str, *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    key = download_key(req.tmdb_id, req.type, req.season, req.episode)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            return {"ok": True, "links": cached.get("links", []),
                    "cached": True, "took_ms": int((time.monotonic() - t0) * 1000)}

    meta = await enrich(req.tmdb_id, req.type, req.title)
    data = LinkData(
        tmdb_id=req.tmdb_id, type=req.type, title=req.title,
        imdb_id=req.imdb_id, year=req.year,
        season=req.season, episode=req.episode,
        is_anime=meta["is_anime"], is_asian=meta["is_asian"],
        is_bollywood=meta["is_bollywood"], org_title=meta["org_title"],
    )

    providers = [p for p in get_all() if await should_run(p.id)]
    links = []

    async def invoke(p):
        t_start = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t_start) * 1000)
        local = []
        for item in result.downloads:
            if not item.url:
                continue
            fname = f"{req.title} {item.quality or ''}.{item.type}".strip()
            local.append({
                "provider": p.name,
                "provider_id": p.id,
                "url": item.url,
                "download_url": _proxy_url(base_url, item.url, fname, item.headers),
                "type": item.type,
                "quality": item.quality,
                "size_label": item.size_label,
                "headers": item.headers,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            links.extend(r)

    if links:
        await cache_set(key, {"links": links})

    return {
        "ok": bool(links),
        "links": links,
        "cached": False,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "error": None if links else "No download links found",
    }


async def proxy_download(url: str, *, filename: Optional[str] = None, referer: Optional[str] = None):
    """Proxy a direct URL as a browser download with filename + headers."""
    from fastapi.responses import Response
    from ENGINE.tools.http import get_client, UA

    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer

    client = await get_client()
    try:
        upstream = await client.get(url, headers=headers, timeout=30)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail=str(e))

    if not filename:
        import posixpath
        from urllib.parse import unquote
        filename = unquote(posixpath.basename(str(upstream.url).split("?")[0])) or "download.mp4"

    filename = filename.replace('"', "'").replace("\n", "").replace("\r", "")
    ct = upstream.headers.get("content-type", "application/octet-stream")
    resp_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*",
    }
    if cl := upstream.headers.get("content-length"):
        resp_headers["Content-Length"] = cl

    return Response(content=upstream.content, media_type=ct, headers=resp_headers)
