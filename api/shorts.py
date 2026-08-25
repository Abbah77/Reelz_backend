"""
api/shorts.py — Shorts.
cache_ttl_ms: None — video URLs from providers expire fast
"""
from __future__ import annotations
import base64, hashlib, json
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Shorts"])

def _decode_page(cursor):
    if not cursor: return 1
    try: return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except: return 1

def _encode_cursor(page):
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()

def _make_id(url, idx):
    return hashlib.md5(f"{url}:{idx}".encode()).hexdigest()[:16]


@router.get("/shorts")
async def get_shorts(
    response: Response,
    cursor: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    page = _decode_page(cursor)
    from ENGINE.manager.shorts import get_shorts as engine_shorts
    result = await engine_shorts(tmdb_id=0, media_type="movie", page=page, fresh=bool(fresh))
    raw = result.get("shorts", [])
    items = [
        {"id": _make_id(s.get("url",""), i), "title": s.get("title",""),
         "source": s.get("provider") or s.get("source") or "original",
         "url": s.get("url",""), "thumbnail": s.get("thumbnail") or None}
        for i, s in enumerate(raw[:limit]) if s.get("url")
    ]
    has_more = len(items) >= limit
    set_cache(response, None)
    return ok({
        "items": items,
        "has_more": has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }, cache_ttl_ms=None)
