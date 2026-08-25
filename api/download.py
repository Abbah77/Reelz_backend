"""
api/download.py — Download.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
"""
from __future__ import annotations
import re, time
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from api.auth import verify
from api.envelope import ok, err
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    id:      str = Field(...)
    type:    str = Field(...)
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_tmdb_id(media_id):
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try: return int(parts[1])
        except ValueError: pass
    try: return int(media_id)
    except ValueError: return None

def _res_height(q):
    m = re.search(r"(\d{3,4})p", (q or "").lower())
    return int(m.group(1)) if m else 0

async def _is_premium(user_id):
    if not user_id: return False
    try:
        from USERS.db import SessionLocal
        from USERS.models import User
        from sqlalchemy import select
        async with SessionLocal() as s:
            r = await s.execute(select(User).where(User.id == user_id))
            u = r.scalar_one_or_none()
        return bool(u and u.is_premium_active())
    except: return False


class _EngineReq:
    def __init__(self, body, tmdb_id):
        self.tmdb_id = tmdb_id
        self.type    = body.type
        self.title   = ""
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


@router.post("/download")
async def get_download_links(
    req: StreamRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    from config import get_settings
    from fastapi import HTTPException
    if not get_settings().downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads not available")

    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        raise HTTPException(status_code=400, detail="Invalid id format.")

    is_premium = await _is_premium(user_id)
    from ENGINE.manager.download import get_downloads
    result = await get_downloads(_EngineReq(req, tmdb_id), fresh=bool(fresh))

    links = []
    for link in result.get("links", []):
        url = link.get("url", "")
        if not url: continue
        res = _res_height(link.get("label", ""))
        links.append({
            "label":      link.get("label") or "Auto",
            "type":       link.get("type") or "mp4",
            "url":        url,
            "language":   link.get("language") or "English",
            "size_bytes": int(link.get("size_bytes") or 0),
            "premium":    res >= 1080 and not is_premium,
        })

    if not links:
        set_cache(response, None)
        return err("No download links available")

    # Use smart TTL derived from provider policy (not a hardcoded value).
    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    # expires_at_ms: when the download links themselves die (for the client).
    now_ms = int(time.time() * 1000)
    link_expiries = [
        lnk.get("expires_at_ms") for lnk in result.get("links", []) if lnk.get("expires_at_ms")
    ]
    if link_expiries:
        expires_at_ms = min(link_expiries)
    elif cache_ttl_ms:
        expires_at_ms = now_ms + cache_ttl_ms
    else:
        expires_at_ms = now_ms + 3_600_000  # fallback 1h

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    return ok({
        "links":         links,
        "expires_at_ms": expires_at_ms,
    }, cache_ttl_ms=cache_ttl_ms)
