"""
Internal provider-layer types.

These flow between providers → managers.
They are NOT the API response shapes (see schemas/response.py).
Providers speak in Stream / Subtitle / DownloadItem.
Managers translate those into StreamEntry / SubtitleEntry / DownloadLink.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── What every provider returns ────────────────────────────────────────────────

class Stream(BaseModel):
    """A single playable stream URL from a provider."""
    server: str                            # human label, e.g. "RiveStream Vidsrc"
    link: str
    type: Literal["m3u8", "mp4", "iframe"]
    quality: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    audio_name: Optional[str] = None       # HLS audio rendition hint
    debrid: bool = False


class Subtitle(BaseModel):
    """A single subtitle track from a provider."""
    language: str
    url: str
    label: Optional[str] = None
    format: Optional[str] = None           # "srt" | "vtt" | "ass"


class DownloadItem(BaseModel):
    """A single downloadable link from a download provider."""
    server: str
    link: str
    type: str                              # "mp4" | "mkv" | "m3u8"
    quality: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)


class ProviderResult(BaseModel):
    """Unified result envelope returned by every provider."""
    streams: list[Stream] = Field(default_factory=list)
    subtitles: list[Subtitle] = Field(default_factory=list)
    downloads: list[DownloadItem] = Field(default_factory=list)


# ── What managers pass to providers ───────────────────────────────────────────

ContentKind = Literal["movie", "series", "anime", "asian"]


class LinkData(BaseModel):
    """
    Everything a provider needs to fetch streams for one piece of content.
    Built once by the manager from the API request + optional TMDB enrichment.
    Passed read-only to every provider.
    """
    id: int                                         # TMDB numeric id
    imdb_id: Optional[str] = None                   # e.g. "tt0137523"
    tvdb_id: Optional[int] = None
    type: Literal["movie", "tv"]
    season: Optional[int] = None
    episode: Optional[int] = None
    absolute_episode: Optional[int] = None
    title: str
    year: Optional[int] = None
    org_title: Optional[str] = None
    is_anime: bool = False
    is_cartoon: bool = False
    is_asian: bool = False
    is_bollywood: bool = False
    jp_title: Optional[str] = None
    season_year: Optional[int] = None
    anime_season_titles: list[str] = Field(default_factory=list)
    mal_id: Optional[int] = None
    anilist_id: Optional[int] = None
    anime_titles: list[str] = Field(default_factory=list)
