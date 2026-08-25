"""
api/download.py — POST /api/v1/download

Auth is OPTIONAL — same as Stream. Guests get full download access.
premium: true on a link shows a lock badge in the app (upsell only).
Backend enforces premium-only access server-side.

Request:
    { "id": "movie:12345", "type": "movie", "season": 0, "episode": 0 }

Response envelope:
  {
    "ok": true,
    "data": {
      "links": [...],
      "expires_at_ms": 1724000000000
    },
    "error": null,
    "cache_ttl_ms": null
  }

expires_at_ms is content-level (when download URLs expire) → inside data.
cache_ttl_ms is null — download URLs must always be freshly resolved.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import verify  # GUEST-OK: auth optional
from api.envelope import ok, err

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    """Request body for download endpoint."""
    id:      str = Field(..., description="Media ID (e.g. 'movie:12345' or '12345')")
    type:    str = Field(..., description="'movie' or 'tv'")
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_tmdb_id(media_id: str) -> Optional[int]:
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    try:
        return int(media_id)
    except ValueError:
        return None


def _res_height(quality_str: str) -> int:
    m = re.search(r"(\d{3,4})p", (quality_str or "").lower())
    return int(m.group(1)) if m else 0


async def _is_premium_user(user_id: Optional[str]) -> bool:
    if not user_id:
        return False
    try:
        from USERS.db import SessionLocal
        from USERS.models import User
        from sqlalchemy import select
        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            user: Optional[User] = result.scalar_one_or_none()
        return bool(user and user.is_premium_active())
    except Exception:
        return False


class _EngineReq:
    def __init__(self, body: StreamRequestBody, tmdb_id: int):
        self.tmdb_id = tmdb_id
        self.type    = body.type
        self.title   = ""
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


@router.post("/download")
async def get_download_links(
    req:     StreamRequestBody,
    fresh:   int = Query(0, description="Set to 1 to bypass cache"),
    user_id: Optional[str] = Depends(verify),
):
    from config import get_settings
    from fastapi import HTTPException

    _s = get_settings()
    if not _s.downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads are not available")

    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid id format. Use 'movie:<tmdb_id>', 'tv:<tmdb_id>', or bare numeric id.",
        )

    is_premium = await _is_premium_user(user_id)
    engine_req = _EngineReq(req, tmdb_id)

    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, fresh=bool(fresh))

    # Build schema-compliant links array
    links = []
    for link in result.get("links", []):
        label     = link.get("label") or "Auto"
        link_type = link.get("type") or "mp4"   # "mp4" | "hls"
        url       = link.get("url", "")
        if not url:
            continue

        res = _res_height(label)
        # 1080p+ is premium-locked for free users; backend enforces on actual download start
        is_locked = (res >= 1080 and not is_premium)

        links.append({
            "label":      label,
            "type":       link_type,
            "url":        url,
            "language":   link.get("language") or "English",
            "size_bytes": int(link.get("size_bytes") or 0),
            "premium":    is_locked,
        })

    if not links:
        return err("No download links available for this title")

    expires_at_ms = int((time.time() + 3600) * 1000)

    # cache_ttl_ms=None — download URLs must never be cached by the app
    return ok(
        {
            "links":         links,
            "expires_at_ms": expires_at_ms,
        },
        cache_ttl_ms=None,
    )
