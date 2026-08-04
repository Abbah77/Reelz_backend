"""
api/streams.py — POST /api/v1/streams

Route: tiny. No business logic.
Flow:  HTTP → manager → return.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.managers.stream import get_streams
from app.schemas.request import StreamRequest

router = APIRouter(prefix="/api/v1")


@router.post("/streams")
async def post_streams(
    req: StreamRequest,
    fresh: int = Query(0, description="Bypass cache and force live resolve"),
    warp: Optional[str] = Query(None, description="WARP mode override"),
):
    return await get_streams(req, fresh=bool(fresh), warp_mode=warp or "off")
