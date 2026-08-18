"""
api/stream.py — POST /api/v1/stream

Auth is OPTIONAL. Guests and free users get the same streams.
If a Bearer token is present, backend logs play history server-side.
If absent, backend serves the same content without logging — must never return 401.

Request:  { id, type, season, episode }
Response: { ok, streams[], expires_at_ms }
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import verify  # guest-ok: accepts JWT, legacy token, or no credentials

router = APIRouter(prefix="/api/v1", tags=["Stream"])


class StreamRequestBody(BaseModel):
    id:      str = Field(..., description="Media ID")
    type:    str = Field(..., description="movie | tv")
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_id(media_id: str) -> tuple[Optional[str], Optional[int]]:
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try:
            return None, int(parts[1])
        except ValueError:
            pass
    return None, None


class _EngineRequest:
    def __init__(self, body: StreamRequestBody, tmdb_id: int):
        self.tmdb_id = tmdb_id
        self.type    = body.type
        self.title   = ""
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


@router.post("/stream")
async def resolve_stream(
    req: StreamRequestBody,
    fresh: int = Query(0, description="1 = skip cache"),
    warp:  str = Query("off"),
    user_id: Optional[str] = Depends(verify),  # GUEST-OK
):
    _, tmdb_id = _parse_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    engine_req = _EngineRequest(req, tmdb_id)

    from ENGINE.manager.stream import get_streams
    result = await get_streams(engine_req, fresh=bool(fresh), warp_mode=warp)

    raw_streams = result.get("streams", [])
    best        = result.get("stream")
    if not best and raw_streams:
        best = raw_streams[0]

    # Build schema-compliant streams array: one entry per language track
    streams = []
    for s in raw_streams:
        url = s.get("url", "")
        if not url:
            continue
        stream_type = "hls" if s.get("type") in ("m3u8", "hls") else "mp4"
        streams.append({
            "name":      s.get("language") or s.get("quality") or "English",
            "url":       url,
            "type":      stream_type,
            "headers":   s.get("headers") or {},
            "subtitles": [],   # subtitles resolved via POST /api/v1/subtitles
        })

    # Deduplicate by name — keep first occurrence
    seen = set()
    unique_streams = []
    for s in streams:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique_streams.append(s)

    if not unique_streams and best:
        stream_type = "hls" if best.get("type") in ("m3u8", "hls") else "mp4"
        unique_streams = [{
            "name":      best.get("language") or best.get("quality") or "English",
            "url":       best.get("url", ""),
            "type":      stream_type,
            "headers":   best.get("headers") or {},
            "subtitles": [],
        }]

    ok = bool(unique_streams)
    expires_at_ms = int((time.time() + 3600) * 1000)  # 1 hour from now

    return {
        "ok":            ok,
        "streams":       unique_streams,
        "expires_at_ms": expires_at_ms,
    }
