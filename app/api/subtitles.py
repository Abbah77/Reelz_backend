"""
api/subtitles.py — POST /api/v1/subtitles

Route: tiny. No business logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.managers.subtitle import get_subtitles
from app.schemas.request import SubtitleRequest

router = APIRouter(prefix="/api/v1")


@router.post("/subtitles")
async def post_subtitles(
    req: SubtitleRequest,
    fresh: int = Query(0),
):
    return await get_subtitles(req, fresh=bool(fresh))
