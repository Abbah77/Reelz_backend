"""
api/subtitle.py — Subtitles route.
cache_ttl_ms: None (subtitles are not cached)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache
from api.media_request import parse_tmdb_id, EngineRequest

router = APIRouter(prefix="/api/v1", tags=["Subtitles"])


class SubtitleRequestBody(BaseModel):
    id:        str       = Field(...)
    type:      str
    season:    int       = Field(0, ge=0)
    episode:   int       = Field(0, ge=0)
    languages: list[str] = Field(default_factory=lambda: ["en"])


@router.post("/subtitles")
async def get_subtitles(
    req: SubtitleRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    tmdb_id = parse_tmdb_id(req.id)
    engine_req = EngineRequest(
        tmdb_id = tmdb_id,
        type    = req.type,
        season  = req.season or None,
        episode = req.episode or None,
    )
    # Attach languages so the subtitle manager can pass them to providers
    engine_req.languages = req.languages  # type: ignore[attr-defined]

    from ENGINE.manager.subtitle import get_subtitles as engine_subtitles
    result = await engine_subtitles(engine_req, fresh=bool(fresh))
    subs = [
        {
            "url":      s.get("url", ""),
            "language": s.get("language", "en"),
            "enabled":  s.get("language", "en") == "en",
        }
        for s in result.get("subtitles", []) if s.get("url")
    ]
    set_cache(response, None)
    return ok({"subtitles": subs}, cache_ttl_ms=None)
