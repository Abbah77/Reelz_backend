"""
ENGINE/manager/tmdb.py — TMDB metadata enrichment.

Used ONLY by managers to detect content kind before passing to providers.
Providers NEVER call TMDB directly.

Detects: anime, Asian drama, Bollywood.
Results cached 24h independently so metadata survives stream cache expiry.
"""
from __future__ import annotations

from typing import Optional
from ENGINE.tools.http import get_client, UA
from ENGINE.cache.cache import get as cache_get, set as cache_set
from config import get_settings

_s = get_settings()

_ANIME_KEYWORDS = {
    "anime", "manga adaptation", "based on manga",
    "based on light novel", "shounen", "shōnen", "seinen", "isekai",
}
_ASIAN_COUNTRIES = {"KR", "CN", "TW", "HK", "TH", "VN"}


def _detect(media_type: str, countries: list, genre_ids: list, keywords: list) -> str:
    is_animation = 16 in genre_ids
    kw = {k.lower() for k in keywords}
    if ("JP" in countries and is_animation) or (kw & _ANIME_KEYWORDS):
        return "anime"
    if set(countries) & _ASIAN_COUNTRIES:
        return "asian"
    if "IN" in countries:
        return "bollywood"
    return media_type  # "movie" or "tv" → "series"


async def enrich(tmdb_id: int, media_type: str, title: str) -> dict:
    """
    Returns enriched metadata dict:
        kind, is_anime, is_asian, is_bollywood, org_title

    Falls back to defaults if TMDB key missing or request fails.
    """
    default = {
        "kind": media_type,
        "is_anime": False,
        "is_asian": False,
        "is_bollywood": False,
        "org_title": None,
    }

    if not _s.tmdb_api_key:
        return default

    cache_key = f"tmdb:meta:{media_type}:{tmdb_id}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    mtype = "movie" if media_type == "movie" else "tv"
    url = (
        f"{_s.tmdb_base_url}/{mtype}/{tmdb_id}"
        f"?api_key={_s.tmdb_api_key}&append_to_response=keywords"
    )

    try:
        client = await get_client()
        res = await client.get(url, headers={"User-Agent": UA}, timeout=8)
        if res.status_code >= 400:
            return default

        j = res.json()
        countries = j.get("origin_country") or [
            c.get("iso_3166_1", "") for c in j.get("production_countries", [])
        ]
        genre_ids = [g["id"] for g in j.get("genres", []) if "id" in g]
        kw_block = j.get("keywords", {})
        kw_list = kw_block.get("keywords") or kw_block.get("results", [])
        keywords = [k.get("name", "") for k in kw_list]
        kind = _detect(media_type, countries, genre_ids, keywords)
        org_title = j.get("original_title") or j.get("original_name")

        meta = {
            "kind": kind,
            "is_anime": kind == "anime",
            "is_asian": kind == "asian",
            "is_bollywood": kind == "bollywood",
            "org_title": org_title,
        }
        await cache_set(cache_key, meta, ttl=86_400)
        return meta
    except Exception:
        return default
