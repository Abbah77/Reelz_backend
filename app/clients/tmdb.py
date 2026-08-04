"""
clients/tmdb.py — TMDB API client.

Used only by managers to enrich LinkData before handing it to providers.
Providers never call TMDB directly.
"""
from __future__ import annotations

from typing import Optional

from app.clients.http import app as http_app
from app.config import get_settings
from app.schemas.provider import LinkData, ContentKind

_settings = get_settings()

# ── Kind detection logic ───────────────────────────────────────────────────────

_ANIME_KEYWORDS = {
    "anime", "shōnen", "shounen", "shojo", "josei", "seinen", "isekai",
    "manga adaptation", "based on manga", "based on light novel",
}
_ASIAN_ORIGIN_CODES = {"KR", "CN", "TW", "HK", "TH", "VN", "IN"}


def _detect_kind(
    media_type: str,
    origin_countries: list[str],
    genre_ids: list[int],
    keywords: list[str],
) -> ContentKind:
    is_animation = 16 in genre_ids
    kw_lower = {k.lower() for k in keywords}

    is_anime = (
        ("JP" in origin_countries and is_animation)
        or bool(kw_lower & _ANIME_KEYWORDS)
    )
    is_asian = not is_anime and bool(set(origin_countries) & _ASIAN_ORIGIN_CODES)

    if is_anime:
        return "anime"
    if is_asian:
        return "asian"
    if media_type == "movie":
        return "movie"
    return "series"


async def enrich_link_data(
    link_data: LinkData,
    media_type: str,
) -> tuple[LinkData, ContentKind]:
    """
    Fetch TMDB metadata and enrich LinkData with anime/asian/bollywood flags.
    Falls back to plain kind detection if TMDB key is missing or call fails.
    Returns (enriched_link_data, content_kind).
    """
    default_kind: ContentKind = "movie" if media_type == "movie" else "series"

    if not _settings.tmdb_api_key:
        return link_data, default_kind

    try:
        mtype = "movie" if media_type == "movie" else "tv"
        url = (
            f"{_settings.tmdb_base_url}/{mtype}/{link_data.id}"
            f"?api_key={_settings.tmdb_api_key}&append_to_response=keywords"
        )
        res = await http_app.get(url, timeout=8)
        j = res.json() if res and res.is_successful else None
        if not j:
            return link_data, default_kind

        origin_countries: list[str] = j.get("origin_country") or [
            c.get("iso_3166_1", "") for c in j.get("production_countries", [])
        ]
        genre_ids = [g.get("id") for g in j.get("genres", []) if g.get("id")]
        kw_block = j.get("keywords") or {}
        kw_list = kw_block.get("keywords", []) or kw_block.get("results", [])
        keywords = [k.get("name", "") for k in kw_list]

        kind = _detect_kind(media_type, origin_countries, genre_ids, keywords)

        # Enrich the LinkData copy
        orig_title = j.get("original_title") or j.get("original_name") or ""
        if orig_title and orig_title != link_data.title:
            link_data = link_data.model_copy(update={"org_title": orig_title})

        link_data = link_data.model_copy(update={
            "is_anime": kind == "anime",
            "is_asian": kind == "asian",
            "is_bollywood": "IN" in origin_countries,
        })

    except Exception:
        return link_data, default_kind

    return link_data, kind
