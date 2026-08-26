"""
api/download.py — Download route.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
"""
from __future__ import annotations

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.auth import verify
from api.envelope import ok, err
from api.cache_headers import set_cache
from api.media_request import parse_tmdb_id, EngineRequest
from USERS.queries import is_premium_user

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    id:      str = Field(...)
    type:    str = Field(...)
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _res_height(q: str) -> int:
    m = re.search(r"(\d{3,4})p", (q or "").lower())
    return int(m.group(1)) if m else 0


@router.post("/download")
async def get_download_links(
    req: StreamRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    from config import get_settings
    if not get_settings().downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads not available")

    tmdb_id = parse_tmdb_id(req.id)
    engine_req = EngineRequest(
        tmdb_id = tmdb_id,
        type    = req.type,
        season  = req.season or None,
        episode = req.episode or None,
    )

    is_premium = await is_premium_user(user_id)

    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, fresh=bool(fresh))

    links = []
    for link in result.get("links", []):
        url   = link.get("url", "")
        label = link.get("label", "").strip()
        if not url:
            continue
        if not label:
            # Infer from URL; fall back to "1080p" — never synthesise "Auto"
            import re as _re
            m = _re.search(r"(2160|1080|720|480|360|240)p?", url, _re.I)
            label = (m.group(1) + "p") if m else "1080p"
        res = _res_height(label)
        links.append({
            "label":      label,
            "type":       link.get("type") or "mp4",
            "url":        url,
            "language":   link.get("language") or "English",
            "size_bytes": int(link.get("size_bytes") or 0),
            "premium":    res >= 720 and not is_premium,  # TEST: 720p+1080p locked
        })

    if not links:
        set_cache(response, None)
        return err("No download links available")

    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    now_ms = int(time.time() * 1000)
    link_expiries = [lnk.get("expires_at_ms") for lnk in result.get("links", []) if lnk.get("expires_at_ms")]
    if link_expiries:
        expires_at_ms = min(link_expiries)
    elif cache_ttl_ms:
        expires_at_ms = now_ms + cache_ttl_ms
    else:
        expires_at_ms = now_ms + 3_600_000

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    return ok({
        "links":         links,
        "expires_at_ms": expires_at_ms,
    }, cache_ttl_ms=cache_ttl_ms)
