"""
api/subtitle.py — POST /api/v1/subtitles

Auth is OPTIONAL. Guests get subtitles the same as free users.

Request:  { id, type, season, episode, languages }
Response: { ok, subtitles[] }
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import verify  # guest-ok

router = APIRouter(prefix="/api/v1", tags=["Subtitles"])


class SubtitleRequestBody(BaseModel):
    id:        str       = Field(..., description="Media ID")
    type:      str
    season:    int       = Field(0, ge=0)
    episode:   int       = Field(0, ge=0)
    languages: list[str] = Field(default_factory=lambda: ["en"])


def _parse_tmdb_id(media_id: str) -> Optional[int]:
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


class _EngineReq:
    def __init__(self, body: SubtitleRequestBody, tmdb_id: int):
        self.tmdb_id   = tmdb_id
        self.type      = body.type
        self.imdb_id   = None
        self.season    = body.season or None
        self.episode   = body.episode or None
        self.languages = body.languages


@router.post("/subtitles")
async def get_subtitles(
    req: SubtitleRequestBody,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),  # GUEST-OK
):
    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    engine_req = _EngineReq(req, tmdb_id)
    from ENGINE.manager.subtitle import get_subtitles as engine_subtitles
    result = await engine_subtitles(engine_req, fresh=bool(fresh))

    subs = [
        {
            "url":      s.get("url", ""),
            "language": s.get("language", "en"),
            "enabled":  s.get("language", "en") == "en",  # default: enable English
        }
        for s in result.get("subtitles", [])
        if s.get("url")
    ]

    return {
        "ok":        result.get("ok", False),
        "subtitles": subs,
    }
