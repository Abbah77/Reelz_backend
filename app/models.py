"""
Core data models — mirrors the Node StreamPlay types exactly,
plus the Reelz API contract from the integration spec.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Input ──────────────────────────────────────────────────────────────────

class LinkData(BaseModel):
    """Passed to every provider's invoke() method."""
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
    season_resolved: bool = False
    anime_season_titles: list[str] = Field(default_factory=list)
    # AniList / MAL / AnimePahe / Zoro IDs
    mal_id: Optional[int] = None
    anilist_id: Optional[int] = None
    anime_titles: list[str] = Field(default_factory=list)  # romaji + synonyms


# ── Stream / Subtitle ───────────────────────────────────────────────────────

class Stream(BaseModel):
    server: str
    link: str
    type: Literal["m3u8", "mp4", "iframe"]
    quality: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    audio_name: Optional[str] = None   # force HLS audio rendition
    debrid: bool = False               # debrid-cached direct file


class Subtitle(BaseModel):
    language: str
    url: str
    label: Optional[str] = None
    format: Optional[str] = None       # "srt", "vtt", "ass"


class ExtractorResult(BaseModel):
    streams: list[Stream] = Field(default_factory=list)
    subtitles: list[Subtitle] = Field(default_factory=list)


# ── API request shapes (Reelz spec §3.1 / §3.2) ────────────────────────────

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


# ── API response shapes ─────────────────────────────────────────────────────

class StreamEntry(BaseModel):
    """One item in the /api/v1/streams response."""
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


class StreamResponse(BaseModel):
    ok: bool
    streams: list[StreamEntry] = Field(default_factory=list)
    subtitles: list[SubtitleEntry] = Field(default_factory=list)
    cached: bool = False
    took_ms: int = 0
    error: Optional[str] = None


class DownloadLink(BaseModel):
    provider: str
    provider_id: str
    url: str
    type: str
    quality: Optional[str] = None
    size_bytes: Optional[int] = None
    size_label: Optional[str] = None
    language: str = "English"
    headers: dict[str, str] = Field(default_factory=dict)


class DownloadResponse(BaseModel):
    ok: bool
    links: list[DownloadLink] = Field(default_factory=list)
    error: Optional[str] = None


class SubtitleResponse(BaseModel):
    ok: bool
    subtitles: list[SubtitleEntry] = Field(default_factory=list)
    error: Optional[str] = None


# ── SSE event payloads (Reelz spec §3.1) ────────────────────────────────────

class SSELink(BaseModel):
    provider_id: str
    name: str
    url: str           # proxied relative path
    type: str
    quality: Optional[int] = None
    playable: bool = True
    host: str = ""


class SSESubtitle(BaseModel):
    provider_id: str
    lang: str
    url: str           # proxied relative path


class SSEProviderStatus(BaseModel):
    id: str
    name: str
    state: Literal["running", "found", "empty", "failed"]
    duration_ms: int = 0
    links: list[SSELink] = Field(default_factory=list)
    priority_score: int = 0
    note: Optional[str] = None


class SSELog(BaseModel):
    msg: str


# ── Content kind ─────────────────────────────────────────────────────────────

ContentKind = Literal["movie", "series", "anime", "asian"]
