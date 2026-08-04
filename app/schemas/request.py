"""
Inbound API request schemas.
These are the shapes the Android client (or any caller) sends us.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class StreamRequest(BaseModel):
    tmdb_id: int
    type: Literal["movie", "tv"]
    imdb_id: Optional[str] = None
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None


class DownloadRequest(BaseModel):
    tmdb_id: int
    type: Literal["movie", "tv"]
    imdb_id: Optional[str] = None
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None


class SubtitleRequest(BaseModel):
    tmdb_id: int
    imdb_id: Optional[str] = None
    type: Literal["movie", "tv"]
    season: Optional[int] = None
    episode: Optional[int] = None
    languages: list[str] = Field(default_factory=lambda: ["en"])
