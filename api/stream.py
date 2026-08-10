"""
api/stream.py — Gate route for stream requests.

Responsibility: receive request, verify, forward to ENGINE/manager/stream.py
Nothing else. Zero scraping. Zero business logic.

Later: to move ENGINE to a different VPS, just change the forward target here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.auth import verify

router = APIRouter(prefix="/stream", tags=["Stream"])


class StreamRequest(BaseModel):
    tmdb_id: int
    type: str           # "movie" | "tv"
    title: str
    imdb_id: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None


@router.post("")
async def stream(
    req: StreamRequest,
    fresh: int = Query(0, description="1 = skip cache"),
    warp: str = Query("off"),
    _: None = Depends(verify),
):
    from ENGINE.manager.stream import get_streams
    return await get_streams(req, fresh=bool(fresh), warp_mode=warp)
