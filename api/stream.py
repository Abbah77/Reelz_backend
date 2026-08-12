"""
api/stream.py — POST /api/v1/stream

Accepts the StreamRequestBody shape the Android app sends:
    { id, type, title, season, episode }

where id = "movie:550" or "tv:1396"

Translates to ENGINE's StreamRequest and returns StreamResponseDto shape.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Stream"])


class StreamRequestBody(BaseModel):
    id:      str = Field(..., description="'movie:<tmdb_id>' or 'tv:<tmdb_id>'")
    type:    str = Field(..., description="movie | tv")
    title:   str
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_id(media_id: str) -> tuple[Optional[str], Optional[int]]:
    """Extract imdb_id (None) and tmdb_id from our ID format."""
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try:
            return None, int(parts[1])
        except ValueError:
            pass
    return None, None


class _EngineRequest:
    """Adapter: StreamRequestBody → ENGINE StreamRequest shape."""
    def __init__(self, body: StreamRequestBody, tmdb_id: int):
        self.tmdb_id = tmdb_id
        self.type    = body.type
        self.title   = body.title
        self.imdb_id = None
        self.year    = None
        self.season  = body.season or None
        self.episode = body.episode or None


@router.post("/stream")
async def resolve_stream(
    req: StreamRequestBody,
    fresh: int = Query(0, description="1 = skip cache"),
    warp:  str = Query("off"),
    _: None = Depends(verify),
):
    _, tmdb_id = _parse_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format. Use 'movie:<tmdb_id>' or 'tv:<tmdb_id>'")

    engine_req = _EngineRequest(req, tmdb_id)

    from ENGINE.manager.stream import get_streams
    result = await get_streams(engine_req, fresh=bool(fresh), warp_mode=warp)

    # Translate ENGINE response → StreamResponseDto shape expected by the app
    streams = result.get("streams", [])
    best    = result.get("stream")

    if not best and streams:
        best = streams[0]

    # Build quality tracks from all streams
    qualities = [
        {
            "label":      s.get("quality") or "Auto",
            "url":        s["url"],
            "bandwidth":  0,
            "size_bytes": 0,
        }
        for s in streams
        if s.get("type") in ("m3u8", "mp4")
    ]

    return {
        "ok":         result.get("ok", False),
        "stream_url": best["url"] if best else "",
        "is_hls":     (best["type"] == "m3u8") if best else True,
        "quality":    best.get("quality") or "Auto" if best else "Auto",
        "headers":    best.get("headers") or {} if best else {},
        "source_name": best.get("provider") or "" if best else "",
        "qualities":  qualities,
        "subtitles":  [],
        "cache_ttl_ms": 240_000,
        # Pass-through metadata useful for debugging
        "_cached":    result.get("cached", False),
        "_took_ms":   result.get("took_ms", 0),
    }
