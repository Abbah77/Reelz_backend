"""
api/streams.py — POST /api/v1/streams

Rate-limited to 10 requests/minute per client IP.
Auth enforced by verify_token middleware in main.py.
Route is intentionally thin — all business logic lives in managers/stream.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from app.limiter import limiter
from app.managers.stream import get_streams
from app.schemas.request import StreamRequest

router = APIRouter(prefix="/api/v1")


@router.post("/streams")
@limiter.limit("10/minute")
async def post_streams(
    request: Request,          # Required by slowapi for rate-limit key extraction
    req: StreamRequest,
    fresh: int = Query(0, description="Bypass cache and force live resolve"),
    warp: Optional[str] = Query(None, description="WARP mode override"),
):
    return await get_streams(req, fresh=bool(fresh), warp_mode=warp or "off")
