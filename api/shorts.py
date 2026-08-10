"""
api/shorts.py — Gate route for shorts/trailers requests.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import verify

router = APIRouter(prefix="/shorts", tags=["Shorts"])


@router.get("")
async def shorts(
    tmdb_id: int = Query(...),
    media_type: str = Query("movie"),
    page: int = Query(1, ge=1),
    fresh: int = Query(0),
    _: None = Depends(verify),
):
    from ENGINE.manager.shorts import get_shorts
    return await get_shorts(tmdb_id=tmdb_id, media_type=media_type, page=page, fresh=bool(fresh))
