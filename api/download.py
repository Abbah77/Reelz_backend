"""
api/download.py — POST /api/v1/download + GET /api/v1/download/proxy

POST /api/v1/download       — resolve download links (LOGIN REQUIRED; config controls enablement)
GET  /api/v1/download/proxy — byte-serve a proxied file as a download (LOGIN REQUIRED)

Per the access matrix:
  GUEST        → downloads not available (401 from verify_engine)
  FREE_USER    → limited downloads (config: downloads_enabled)
  PREMIUM_USER → full downloads (config: downloads_enabled, premium unlocks all qualities)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from api.auth import verify_engine

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


async def _is_premium(user_id: str) -> bool:
    """Check if the authenticated user has an active premium subscription."""
    from USERS.db import SessionLocal
    from USERS.models import User
    from sqlalchemy import select
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user: Optional[User] = result.scalar_one_or_none()
    return bool(user and user.is_premium_active())


@router.post("/download")
async def get_download_links(
    req: StreamRequestBody,
    request: Request,
    fresh: int = Query(0),
    user_id: str = Depends(verify_engine),   # LOGIN REQUIRED
):
    from config import get_settings
    from fastapi import HTTPException

    _s = get_settings()
    if not _s.downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads are not available")

    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    premium = await _is_premium(user_id)

    engine_req = _EngineReq(req, tmdb_id)
    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, base_url=str(request.base_url), fresh=bool(fresh))

    all_links = result.get("links", [])

    # Premium users get all qualities; free users get standard quality only
    # Convention: treat any link without explicit quality tag as "standard"
    PREMIUM_QUALITIES = {"1080p", "4k", "2160p", "uhd", "fhd"}
    if premium:
        links = all_links
    else:
        links = [
            link for link in all_links
            if (link.get("quality") or "").lower() not in PREMIUM_QUALITIES
        ] or all_links[:1]   # always give at least one link

    return {
        "ok":       result.get("ok", False),
        "premium":  premium,
        "links": [
            {
                "label":      link.get("quality") or "Auto",
                "url":        link.get("download_url") or link.get("url"),
                "language":   "English",
                "size_bytes":  0,
            }
            for link in links
        ],
    }


@router.get("/download/proxy")
async def download_proxy(
    url:      str           = Query(...),
    filename: Optional[str] = Query(None),
    referer:  Optional[str] = Query(None),
    user_id: str = Depends(verify_engine),   # LOGIN REQUIRED
):
    """Proxy a direct media URL as a browser download with correct headers."""
    from ENGINE.manager.download import proxy_download
    return await proxy_download(url, filename=filename, referer=referer)
