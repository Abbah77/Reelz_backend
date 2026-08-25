"""
api/subtitle.py — Subtitles.
cache_ttl_ms: None
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Subtitles"])


class SubtitleRequestBody(BaseModel):
    id:        str       = Field(...)
    type:      str
    season:    int       = Field(0, ge=0)
    episode:   int       = Field(0, ge=0)
    languages: list[str] = Field(default_factory=lambda: ["en"])


def _parse_tmdb_id(media_id):
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try: return int(parts[1])
        except ValueError: pass
    return None


class _EngineReq:
    def __init__(self, body, tmdb_id):
        self.tmdb_id   = tmdb_id
        self.type      = body.type
        self.imdb_id   = None
        self.season    = body.season or None
        self.episode   = body.episode or None
        self.languages = body.languages


@router.post("/subtitles")
async def get_subtitles(
    req: SubtitleRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    tmdb_id = _parse_tmdb_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format.")

    from ENGINE.manager.subtitle import get_subtitles as engine_subtitles
    result = await engine_subtitles(_EngineReq(req, tmdb_id), fresh=bool(fresh))
    subs = [
        {"url": s.get("url",""), "language": s.get("language","en"), "enabled": s.get("language","en") == "en"}
        for s in result.get("subtitles", []) if s.get("url")
    ]
    set_cache(response, None)
    return ok({"subtitles": subs}, cache_ttl_ms=None)
