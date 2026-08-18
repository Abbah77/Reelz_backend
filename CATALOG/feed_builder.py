"""
CATALOG/feed_builder.py — Assembles the home feed from TMDB data.

Each section is fetched concurrently and cached at the whole-feed level (1 hour).
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Optional

from ENGINE.cache.cache import get as cache_get, set as cache_set
from CATALOG.tmdb import (
    trending_movies, trending_tv, popular_movies, popular_tv,
    top_rated_movies, top_rated_tv, now_playing_movies, on_the_air_tv,
    upcoming_movies, bollywood_movies, anime_tv, kdrama,
    normalise_card,
)

_FEED_TTL    = 3600   # 1 h full feed
_SECTION_TTL = 1800   # 30 min individual section

# ── Cursor helpers ────────────────────────────────────────────────────────────

def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()

def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except Exception:
        return 1


# ── Section builder ───────────────────────────────────────────────────────────

def _make_section(section_id: str, title: str, raw: list[dict], media_type: str, page: int, layout: str = "row") -> dict:
    items    = [normalise_card(r, media_type) for r in raw[:20]]
    has_more = len(raw) >= 20
    return {
        "id":          section_id,
        "title":       title,
        "layout":      layout,
        "items":       items,
        "has_more":    has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }


# ── Full feed ─────────────────────────────────────────────────────────────────

async def build_feed(force: bool = False) -> dict:
    key = "feed:home:v3"
    if not force:
        cached = await cache_get(key)
        if cached:
            return cached

    (
        tr_movies, tr_tv, pop_movies, pop_tv,
        top_movies, top_tv, now_playing, on_air,
        upcoming, bolly, anime, kd,
    ) = await asyncio.gather(
        trending_movies(), trending_tv(), popular_movies(), popular_tv(),
        top_rated_movies(), top_rated_tv(), now_playing_movies(), on_the_air_tv(),
        upcoming_movies(), bollywood_movies(), anime_tv(), kdrama(),
        return_exceptions=True,
    )

    def safe(val, default=None):
        return val if isinstance(val, list) else (default or [])

    sections = [
        s for s in [
            _make_section("trending_movies",  "🔥 Trending Movies",  safe(tr_movies),   "movie", 1),
            _make_section("trending_tv",      "📺 Trending Series",  safe(tr_tv),       "tv",    1),
            _make_section("now_playing",      "🎬 Now Playing",      safe(now_playing), "movie", 1),
            _make_section("popular_movies",   "🍿 Popular Movies",   safe(pop_movies),  "movie", 1),
            _make_section("top_rated_movies", "⭐ Top Rated Movies", safe(top_movies),  "movie", 1),
            _make_section("popular_tv",       "📡 Popular Series",   safe(pop_tv),      "tv",    1),
            _make_section("top_rated_tv",     "🏆 Top Rated Series", safe(top_tv),      "tv",    1),
            _make_section("on_the_air",       "📻 On The Air",       safe(on_air),      "tv",    1),
            _make_section("upcoming",         "🚀 Coming Soon",      safe(upcoming),    "movie", 1),
            _make_section("bollywood",        "🎭 Bollywood",        safe(bolly),       "movie", 1),
            _make_section("anime",            "⛩️ Anime",            safe(anime),       "tv",    1),
            _make_section("kdrama",           "🌸 K-Drama",          safe(kd),          "tv",    1),
        ]
        if s["items"]
    ]

    result = {
        "sections":     sections,
        "cache_ttl_ms": _FEED_TTL * 1000,
    }
    await cache_set(key, result, ttl=_FEED_TTL)
    return result


# ── Section pagination ─────────────────────────────────────────────────────────

_SECTION_FETCHERS = {
    "trending_movies":  (trending_movies,    "movie"),
    "trending_tv":      (trending_tv,        "tv"),
    "now_playing":      (now_playing_movies, "movie"),
    "popular_movies":   (popular_movies,     "movie"),
    "popular_tv":       (popular_tv,         "tv"),
    "top_rated_movies": (top_rated_movies,   "movie"),
    "top_rated_tv":     (top_rated_tv,       "tv"),
    "on_the_air":       (on_the_air_tv,      "tv"),
    "upcoming":         (upcoming_movies,    "movie"),
    "bollywood":        (bollywood_movies,   "movie"),
    "anime":            (anime_tv,           "tv"),
    "kdrama":           (kdrama,             "tv"),
}


async def get_section(section_id: str, cursor: Optional[str] = None, limit: int = 20) -> dict:
    if section_id not in _SECTION_FETCHERS:
        return {"items": [], "has_more": False, "next_cursor": None, "cache_ttl_ms": 0}

    page    = _decode_cursor(cursor)
    fetcher, media_type = _SECTION_FETCHERS[section_id]

    cache_key = f"feed:section:{section_id}:{page}:{limit}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    raw = await fetcher(page=page)
    if not isinstance(raw, list):
        raw = []

    items    = [normalise_card(r, media_type) for r in raw[:limit]]
    has_more = len(raw) >= limit

    result = {
        "items":        items,
        "has_more":     has_more,
        "next_cursor":  _encode_cursor(page + 1) if has_more else None,
        "cache_ttl_ms": _SECTION_TTL * 1000,
    }
    await cache_set(cache_key, result, ttl=_SECTION_TTL)
    return result
