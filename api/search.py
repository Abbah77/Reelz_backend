"""
api/search.py — Search route.

GET /api/v1/search?q=<query>[&type=movie|tv][&cursor=...][&limit=20]
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Search"])


@router.get("/search")
async def search(
    q: str                = Query(..., min_length=1, description="Search query"),
    type: Optional[str]   = Query(None, description="movie | tv | null = both"),
    cursor: Optional[str] = Query(None),
    limit: int            = Query(20, ge=1, le=50),
    _: None = Depends(verify),
):
    if type and type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="type must be 'movie' or 'tv'")

    from CATALOG.search import search as do_search
    return await do_search(query=q, media_type=type, cursor=cursor, limit=limit)
