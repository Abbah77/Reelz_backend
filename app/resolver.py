"""
Resolves the content 'kind' (movie / series / anime / asian) from a request
and builds a LinkData object for provider invocation.

This mirrors the Node extractors/index.ts kind-detection logic plus the
TMDB metadata fetch that populates anime flags.
"""
from __future__ import annotations

import re
from typing import Optional

from app.models import LinkData, StreamRequest, DownloadRequest, ContentKind


# ── Keyword lists (copied from Node kind detection) ───────────────────────────

_ANIME_ORIGIN_CODES = {"JP"}
_ASIAN_ORIGIN_CODES = {"KR", "CN", "TW", "HK", "TH", "VN", "IN"}

_ANIME_GENRE_IDS = {16}        # TMDB Animation genre
_ANIME_KEYWORDS = {
    "anime", "shōnen", "shounen", "shojo", "josei", "seinen", "isekai",
    "manga adaptation", "based on manga", "based on light novel",
}
_ASIAN_GENRE_IDS = set()       # not reliable via genre; rely on origin

_WESTERN_ANIMATION_ORIGINS = {"US", "CA", "GB", "AU", "FR", "DE", "IT", "ES"}


def _is_likely_anime(
    origin_countries: list[str],
    genre_ids: list[int],
    keywords: list[str],
    is_animation: bool,
) -> bool:
    if "JP" in origin_countries and is_animation:
        return True
    kw_lower = {k.lower() for k in keywords}
    if kw_lower & _ANIME_KEYWORDS:
        return True
    return False


def _is_likely_asian(origin_countries: list[str], is_anime: bool) -> bool:
    if is_anime:
        return False
    return bool(set(origin_countries) & _ASIAN_ORIGIN_CODES)


def _is_animation(genre_ids: list[int]) -> bool:
    return 16 in genre_ids


def resolve_kind(
    media_type: str,
    origin_countries: Optional[list[str]] = None,
    genre_ids: Optional[list[int]] = None,
    keywords: Optional[list[str]] = None,
) -> ContentKind:
    ocs = origin_countries or []
    gids = genre_ids or []
    kws = keywords or []
    is_anim = _is_animation(gids)
    is_anime = _is_likely_anime(ocs, gids, kws, is_anim)
    is_asian = _is_likely_asian(ocs, is_anime)
    if is_anime:
        return "anime"
    if is_asian:
        return "asian"
    if media_type == "movie":
        return "movie"
    return "series"


def build_link_data(req: StreamRequest | DownloadRequest) -> LinkData:
    """Build a basic LinkData from an API request (no TMDB enrichment)."""
    return LinkData(
        id=req.tmdb_id,
        imdb_id=req.imdb_id,
        type=req.type,
        season=req.season,
        episode=req.episode,
        title=req.title,
        year=req.year,
    )


async def build_enriched_link_data(
    req: StreamRequest | DownloadRequest,
    tmdb_api_key: Optional[str] = None,
) -> tuple[LinkData, ContentKind]:
    """
    Build LinkData with TMDB enrichment (origin, genres, keywords).
    Falls back to plain build_link_data if TMDB key missing or request fails.
    Returns (LinkData, kind).
    """
    ld = build_link_data(req)
    kind: ContentKind = "movie" if req.type == "movie" else "series"

    if not tmdb_api_key:
        return ld, kind

    try:
        from app.utils.http import app
        base = "https://api.themoviedb.org/3"
        mtype = "movie" if req.type == "movie" else "tv"
        detail_url = f"{base}/{mtype}/{req.tmdb_id}?api_key={tmdb_api_key}&append_to_response=keywords"
        res = await app.get(detail_url, timeout=8)
        j = res.json() if res else None
        if not j:
            return ld, kind

        origin_countries: list[str] = j.get("origin_country") or [
            c.get("iso_3166_1", "") for c in j.get("production_countries", [])
        ]
        genre_ids = [g.get("id") for g in j.get("genres", []) if g.get("id")]
        kw_block = j.get("keywords") or {}
        kw_list = kw_block.get("keywords", []) or kw_block.get("results", [])
        keywords = [k.get("name", "") for k in kw_list]

        kind = resolve_kind(req.type, origin_countries, genre_ids, keywords)

        # Enrich anime metadata
        is_anime = kind == "anime"
        is_asian = kind == "asian"

        # Alternate / original title
        orig_title = j.get("original_title") or j.get("original_name") or ""
        if orig_title and orig_title != ld.title:
            ld.org_title = orig_title

        ld.is_anime = is_anime
        ld.is_asian = is_asian
        ld.is_bollywood = "IN" in origin_countries

    except Exception:
        pass

    return ld, kind
