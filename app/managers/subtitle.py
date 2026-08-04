"""
managers/subtitle.py — subtitle manager.

Responsibilities:
  - Cache lookup / write
  - Fan out to subtitle providers concurrently
  - Deduplication by file_id
  - Provider statistics recording
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.cache import cache
from app.cache.keys import subtitle_key
from app.schemas.request import SubtitleRequest
from app.config import get_settings

_settings = get_settings()


async def get_subtitles(req: SubtitleRequest, *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    langs = req.languages or ["en"]
    key = subtitle_key(req.tmdb_id, req.type, req.season, req.episode, langs)

    # ── Cache fast path ────────────────────────────────────────────────────────
    if not fresh:
        cached = await cache.get(key)
        if cached:
            return {
                "ok": True,
                "subtitles": cached.get("subtitles", []),
                "cached": True,
                "took_ms": int((time.monotonic() - t0) * 1000),
            }

    subtitles_out = []

    # ── OpenSubtitles ──────────────────────────────────────────────────────────
    try:
        from app.providers.subtitle.opensubtitles.provider import (
            search_opensubtitles,
            download_opensubtitles,
        )
        hits = await search_opensubtitles(
            tmdb_id=req.tmdb_id,
            media_type=req.type,
            season=req.season,
            episode=req.episode,
            languages=langs,
        )
        seen: set[int] = set()

        async def resolve_sub(hit: dict) -> Optional[dict]:
            attrs = hit.get("attributes", {})
            file_info = (attrs.get("files") or [{}])[0]
            file_id = file_info.get("file_id")
            if not file_id or file_id in seen:
                return None
            seen.add(file_id)
            dl_url = await download_opensubtitles(file_id)
            if not dl_url:
                return None
            return {
                "provider": "opensubtitles",
                "language": attrs.get("language", "en"),
                "label": attrs.get("language", "en").capitalize(),
                "url": dl_url,
                "format": (attrs.get("format") or "srt").lower(),
                "rating": attrs.get("ratings"),
                "downloads": attrs.get("download_count"),
            }

        results = await asyncio.gather(
            *[resolve_sub(h) for h in hits], return_exceptions=True
        )
        subtitles_out.extend(r for r in results if r and not isinstance(r, Exception))
    except Exception:
        pass

    # ── Wyzie Subs ────────────────────────────────────────────────────────────
    try:
        from app.providers.subtitle.wyzie.provider import search_wyzie
        wyzie_hits = await search_wyzie(
            imdb_id=req.imdb_id,
            tmdb_id=req.tmdb_id,
            media_type=req.type,
            season=req.season,
            episode=req.episode,
            languages=langs,
        )
        for hit in wyzie_hits:
            url = hit.get("url") or hit.get("download_url", "")
            if url:
                subtitles_out.append({
                    "provider": "wyzie",
                    "language": hit.get("language", "en"),
                    "label": hit.get("language", "en").capitalize(),
                    "url": url,
                    "format": (hit.get("format") or "srt").lower(),
                    "rating": hit.get("rating"),
                    "downloads": None,
                })
    except Exception:
        pass

    took_ms = int((time.monotonic() - t0) * 1000)

    if subtitles_out:
        await cache.set(key, {"subtitles": subtitles_out})

    return {
        "ok": True,
        "subtitles": subtitles_out,
        "cached": False,
        "took_ms": took_ms,
    }
