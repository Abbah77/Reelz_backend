"""
api/media_request.py — Shared ENGINE request helpers.

Single source of truth for:
  - parse_tmdb_id()  : extracts integer TMDB id from "type:id" wire format
  - EngineRequest    : canonical object passed to ENGINE managers

All ENGINE-facing API routes (stream, download, subtitle) import from here.
Nothing else should define _parse_id / _EngineReq locally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException


def parse_tmdb_id(media_id: str) -> int:
    """
    Extract the integer TMDB id from the wire format "type:id" (e.g. "movie:550").
    Also accepts a bare integer string ("550") for backwards compat.
    Raises HTTP 400 on bad format.
    """
    parts = media_id.split(":", 1)
    raw = parts[1] if len(parts) == 2 else parts[0]
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid id format — expected 'type:<tmdb_id>'")


@dataclass
class EngineRequest:
    """Canonical request object built by API routes and passed to ENGINE managers."""
    tmdb_id: int
    type:    str
    title:   str          = ""
    imdb_id: Optional[str] = None
    year:    Optional[int] = None
    season:  Optional[int] = None
    episode: Optional[int] = None
