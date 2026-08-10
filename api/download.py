"""
api/download.py — Gate route for download requests.

Responsibility: receive request, verify, forward to ENGINE/manager/download.py
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.auth import verify

router = APIRouter(prefix="/download", tags=["Download"])


class DownloadRequest(BaseModel):
    tmdb_id: int
    type: str
    title: str
    imdb_id: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None


@router.post("")
async def download(
    req: DownloadRequest,
    request: Request,
    fresh: int = Query(0),
    _: None = Depends(verify),
):
    from ENGINE.manager.download import get_downloads
    return await get_downloads(req, base_url=str(request.base_url), fresh=bool(fresh))


@router.get("/proxy")
async def download_proxy(
    url: str = Query(...),
    filename: Optional[str] = Query(None),
    referer: Optional[str] = Query(None),
    _: None = Depends(verify),
):
    """Proxy a direct media URL as a browser download."""
    from ENGINE.manager.download import proxy_download
    return await proxy_download(url, filename=filename, referer=referer)
