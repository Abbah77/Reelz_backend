"""
cache/keys.py — deterministic cache key builders.

One place, one format. Change cache key schema here only.
"""
from __future__ import annotations

from typing import Optional


def stream_key(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int]) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "streams:" + ":".join(parts)


def download_key(tmdb_id: int, media_type: str, season: Optional[int], episode: Optional[int]) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    return "downloads:" + ":".join(parts)


def subtitle_key(
    tmdb_id: int,
    media_type: str,
    season: Optional[int],
    episode: Optional[int],
    langs: list[str],
) -> str:
    parts = [str(tmdb_id), media_type]
    if season is not None:
        parts += [str(season), str(episode or 1)]
    parts.append(",".join(sorted(langs)))
    return "subtitles:" + ":".join(parts)
