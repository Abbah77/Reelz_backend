"""
api/download.py — POST /api/v1/download

Auth is OPTIONAL — same as Stream. Guests get full download access.
premium: true on a link shows a lock badge in the app (upsell only).
Backend enforces premium-only access server-side when the download starts.

Request:  { id, type, season, episode }
Response: { ok, links[], expires_at_ms }
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import verify  # GUEST-OK: auth optional

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    """Same request body the app sends for both stream and download."""
    id:      str = Field(..., description="Media ID")
    type:    str
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
        self.title   = ""
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


async def _is_premium(user_id: Optional[str]) -> bool:
    """Check if the authenticated user has an active premium subscription."""
    if not user_id:
        return False
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
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),   # GUEST-OK: None for guests
):
    from config import get_settings
    from fastapi import HTTPException

    _s = get_settings()
    if not _s.downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads are not available")

    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    is_premium = await _is_premium(user_id)

    engine_req = _EngineReq(req, tmdb_id)
    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, base_url="", fresh=bool(fresh))

    all_links = result.get("links", [])

    expires_at_ms = int((time.time() + 3600) * 1000)  # 1 hour

    # Build schema-compliant links array.
    # Backend sends ALL quality links for ALL users.
    # premium: true draws a lock badge only — backend enforces on actual download.
    def _res_height(quality_str: str) -> int:
        import re
        m = re.search(r"(\d{3,4})p", (quality_str or "").lower())
        return int(m.group(1)) if m else 0

    links = []
    for link in all_links:
        label     = link.get("quality") or "Auto"
        res       = _res_height(label)
        # Mark as premium if resolution is 1080p+ and user isn't premium
        is_locked = (res >= 1080 and not is_premium)
        links.append({
            "label":      label,
            "url":        link.get("download_url") or link.get("url", ""),
            "language":   link.get("language") or "English",
            "size_bytes": link.get("size_bytes") or link.get("size") or 0,
            "premium":    is_locked,
        })

    return {
        "ok":            result.get("ok", False),
        "links":         links,
        "expires_at_ms": expires_at_ms,
    }
