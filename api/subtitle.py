"""
api/subtitle.py — Subtitles route.

Schema v5 changes:
  - `languages` accepts a single string (ISO 639-1 code) OR a list (backward-compat).
  - `duration_ms` added: helps providers fingerprint the exact file.
  - Response now includes `format` field per subtitle entry.
  - cache_ttl_ms: 3_600_000 (1 hour) — subtitles are stable once resolved.

Supported languages: en | es | fr | pt | de | it | ar
"""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator

from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache
from api.media_request import parse_tmdb_id, EngineRequest

router = APIRouter(prefix="/api/v1", tags=["Subtitles"])

SUPPORTED_LANGUAGES = {"en", "es", "fr", "pt", "de", "it", "ar"}

_SUBTITLE_CACHE_TTL_MS = 3_600_000  # 1 hour


class SubtitleRequestBody(BaseModel):
    id:          str                   = Field(...)
    type:        str
    season:      int                   = Field(0, ge=0)
    episode:     int                   = Field(0, ge=0)
    # Accept single string ("en") or list (["en"]) for backward-compat
    languages:   Union[str, list[str]] = Field(default="en")
    # Duration in ms — helps provider find the best fingerprint match
    duration_ms: int                   = Field(0, ge=0)

    @field_validator("languages", mode="before")
    @classmethod
    def normalise_languages(cls, v):
        if isinstance(v, str):
            return [v.lower().strip()]
        return [lang.lower().strip() for lang in v if lang.strip()]


@router.post("/subtitles")
async def get_subtitles(
    req: SubtitleRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    tmdb_id = parse_tmdb_id(req.id)
    engine_req = EngineRequest(
        tmdb_id    = tmdb_id,
        type       = req.type,
        season     = req.season or None,
        episode    = req.episode or None,
    )
    # Pass normalised language list and duration hint to the engine
    engine_req.languages    = req.languages           # type: ignore[attr-defined]
    engine_req.duration_ms  = req.duration_ms         # type: ignore[attr-defined]

    from ENGINE.manager.subtitle import get_subtitles as engine_subtitles
    result = await engine_subtitles(engine_req, fresh=bool(fresh))

    subs = [
        {
            "url":      s.get("url", ""),
            "language": s.get("language", "en"),
            "label":    s.get("label", ""),
            "format":   s.get("format", "srt"),
            "enabled":  s.get("language", "en") == req.languages[0] if req.languages else False,
        }
        for s in result.get("subtitles", []) if s.get("url")
    ]

    # Only include languages the app requested (filter noise from providers)
    requested = set(req.languages)
    subs = [s for s in subs if s["language"] in requested] or subs  # fallback: return all if none match

    set_cache(response, None)
    return ok({"subtitles": subs}, cache_ttl_ms=_SUBTITLE_CACHE_TTL_MS)
