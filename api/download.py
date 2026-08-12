"""
api/download.py — POST /api/v1/download + GET /api/v1/download/proxy

POST /api/v1/download       — resolve download links for a media item
GET  /api/v1/download/proxy — byte-serve a proxied file as a download
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    """Same request body the app sends for both stream and download."""
    id:      str = Field(..., description="'movie:<tmdb_id>' or 'tv:<tmdb_id>'")
    type:    str
    title:   str
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_tmdb_id(media_id: str) -> Optional[int]:
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


class _EngineReq:
    def __init__(self, body: StreamRequestBody, tmdb_id: int):
        self.tmdb_id = tmdb_id
        self.type    = body.type
        self.title   = body.title
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


@router.post("/download")
async def get_download_links(
    req: StreamRequestBody,
    request: Request,
    fresh: int = Query(0),
    _: None = Depends(verify),
):
    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    engine_req = _EngineReq(req, tmdb_id)
    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, base_url=str(request.base_url), fresh=bool(fresh))

    # Map ENGINE links → DownloadLinksResponseDto shape
    links = [
        {
            "label":      link.get("quality") or "Auto",
            "url":        link.get("download_url") or link.get("url"),
            "language":   "English",
            "size_bytes":  0,
        }
        for link in result.get("links", [])
    ]

    return {
        "ok":    result.get("ok", False),
        "links": links,
    }


@router.get("/download/proxy")
async def download_proxy(
    url:      str           = Query(...),
    filename: Optional[str] = Query(None),
    referer:  Optional[str] = Query(None),
    _: None = Depends(verify),
):
    """Proxy a direct media URL as a browser download with correct headers."""
    from ENGINE.manager.download import proxy_download
    return await proxy_download(url, filename=filename, referer=referer)
