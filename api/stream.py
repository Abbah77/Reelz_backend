"""
api/stream.py — Stream.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
              Short for rotating-token providers (R-009: 5min), longer for stable CDNs (R-008: 4h).
"""
from __future__ import annotations
import time
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from api.auth import verify
from api.envelope import ok, err
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Stream"])


class StreamRequestBody(BaseModel):
    id:      str = Field(...)
    type:    str = Field(...)
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _parse_id(media_id):
    parts = media_id.split(":", 1)
    if len(parts) == 2:
        try: return None, int(parts[1])
        except ValueError: pass
    return None, None


class _EngineRequest:
    def __init__(self, body, tmdb_id):
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
    response: Response,
    fresh: int = Query(0),
    warp: str = Query("off"),
    user_id: Optional[str] = Depends(verify),
):
    _, tmdb_id = _parse_id(req.id)
    if tmdb_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid id format.")

    from ENGINE.manager.stream import get_streams
    result = await get_streams(_EngineRequest(req, tmdb_id), fresh=bool(fresh), warp_mode=warp)

    raw_streams = result.get("streams", [])
    best = result.get("stream")
    if not best and raw_streams:
        best = raw_streams[0]

    streams = []
    seen = set()
    for s in raw_streams:
        url = s.get("url", "")
        if not url: continue
        name = s.get("language") or s.get("quality") or "English"
        if name in seen: continue
        seen.add(name)
        streams.append({
            "name":      name,
            "url":       url,
            "type":      "hls" if s.get("type") in ("m3u8", "hls") else "mp4",
            "headers":   s.get("headers") or {},
            "subtitles": [],
        })

    if not streams and best:
        streams = [{
            "name":      best.get("language") or "English",
            "url":       best.get("url", ""),
            "type":      "hls" if best.get("type") in ("m3u8", "hls") else "mp4",
            "headers":   best.get("headers") or {},
            "subtitles": [],
        }]

    if not streams:
        set_cache(response, None)
        return err("No streams available for this title")

    # Use the smart TTL computed by the stream manager from per-provider policy.
    # cache_ttl_ms → app/client cache duration (in the envelope + max-age).
    # cf_max_age_s → Cloudflare s-maxage (always shorter, CF evicts first).
    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    # expires_at_ms in data = when the *links themselves* die (for the client player).
    # Use the shortest per-stream expiry if available, else fall back to TTL window.
    import time as _time
    now_ms = int(_time.time() * 1000)
    stream_expiries = [
        s.get("expires_at_ms") for s in raw_streams if s.get("expires_at_ms")
    ]
    if stream_expiries:
        expires_at_ms = min(stream_expiries)
    elif cache_ttl_ms:
        expires_at_ms = now_ms + cache_ttl_ms
    else:
        expires_at_ms = now_ms + 3_600_000  # fallback 1h

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    return ok({
        "streams":       streams,
        "expires_at_ms": expires_at_ms,
    }, cache_ttl_ms=cache_ttl_ms)
