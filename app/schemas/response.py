"""
Outbound API response schemas.
What the Android client receives.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class StreamEntry(BaseModel):
    provider: str
    provider_id: str
    name: str
    url: str
    type: Literal["m3u8", "mp4", "iframe"]
    quality: Optional[str] = None
    language: str = "English"
    headers: dict[str, str] = Field(default_factory=dict)
    playable: bool = True
    priority: int = 0


class SubtitleEntry(BaseModel):
    provider: str
    language: str
    label: str
    url: str
    format: str = "srt"
    rating: Optional[float] = None
    downloads: Optional[int] = None


class DownloadLink(BaseModel):
    provider: str
    provider_id: str
    url: str
    download_url: Optional[str] = None
    type: str
    quality: Optional[str] = None
    size_bytes: Optional[int] = None
    size_label: Optional[str] = None
    language: str = "English"
    headers: dict[str, str] = Field(default_factory=dict)


class StreamResponse(BaseModel):
    ok: bool
    stream: Optional[StreamEntry] = None
    streams: list[StreamEntry] = Field(default_factory=list)
    subtitles: list[SubtitleEntry] = Field(default_factory=list)
    cached: bool = False
    took_ms: int = 0
    error: Optional[str] = None


class DownloadResponse(BaseModel):
    ok: bool
    links: list[DownloadLink] = Field(default_factory=list)
    cached: bool = False
    took_ms: int = 0
    error: Optional[str] = None


class SubtitleResponse(BaseModel):
    ok: bool
    subtitles: list[SubtitleEntry] = Field(default_factory=list)
    took_ms: int = 0
    error: Optional[str] = None
