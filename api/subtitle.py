"""
api/subtitle.py — Gate route for subtitle requests.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.auth import verify

router = APIRouter(prefix="/subtitle", tags=["Subtitle"])


class SubtitleRequest(BaseModel):
    tmdb_id: int
    type: str
    imdb_id: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    languages: list[str] = ["en"]


@router.post("")
async def subtitle(
    req: SubtitleRequest,
    fresh: int = Query(0),
    _: None = Depends(verify),
):
    from ENGINE.manager.subtitle import get_subtitles
    return await get_subtitles(req, fresh=bool(fresh))
