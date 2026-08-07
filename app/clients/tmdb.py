"""
clients/tmdb.py — TMDB API client.

Used only by managers to enrich LinkData before handing it to providers.
Providers never call TMDB directly.

Improvements over original:
  - Meta-cache: enrichment results cached 24h independently of stream cache.
    When stream cache expires the metadata (genre, country, anime flag) is
    served from this cache — saves one TMDB call per repeat title request.
  - 429 retry: explicit handling of Retry-After header; retries up to 2 times
    before falling back to defaults. Previously 429 was silently swallowed,
    resulting in wrong genre detection and missing Bollywood/Anime flags.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.clients.http import app as http_app
from app.config import get_settings
from app.schemas.provider import LinkData, ContentKind

_settings = get_settings()

# ── Kind detection ─────────────────────────────────────────────────────────────

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


# ── HTTP helper with 429 retry ─────────────────────────────────────────────────

async def _tmdb_get(url: str, max_retries: int = 2):
    """
    GET a TMDB URL with automatic retry on 429 (rate limit).

    Respects Retry-After header. Falls back to 3-second wait if the header
    is absent. Returns the response object on success, None on failure.
    """
    for attempt in range(max_retries + 1):
        try:
            res = await http_app.get(url, timeout=8)
        except Exception:
            return None

        if res is None:
            return None

        if res.status_code == 429:
            if attempt < max_retries:
                wait_s = int(res.headers.get("Retry-After", "3"))
                # Cap wait at 10s — don't block the event loop too long
                await asyncio.sleep(min(wait_s, 10))
                continue
            # Exhausted retries
            return None

        if res.is_successful:
            return res

        # 4xx/5xx (non-429) — no point retrying
        return None

    return None


# ── Main enrichment function ───────────────────────────────────────────────────

async def enrich_link_data(
    link_data: LinkData,
    media_type: str,
) -> tuple[LinkData, ContentKind]:
    """
    Fetch TMDB metadata and enrich LinkData with anime/asian/bollywood flags.

    Cache strategy (two-tier):
      L1 — meta cache (this function): keyed by tmdb:{type}:{id}, TTL 24h.
           Survives stream cache expiry so we don't re-call TMDB every time
           the stream cache misses for a popular title.
      L2 — stream cache (caller): keyed by full stream request, TTL varies.

    Falls back to plain kind detection if TMDB key is missing or all retries fail.
    Returns (enriched_link_data, content_kind).
    """
    from app.cache import cache  # lazy import — avoids circular dependency

    default_kind: ContentKind = "movie" if media_type == "movie" else "series"

    if not _settings.tmdb_api_key:
        return link_data, default_kind

    mtype = "movie" if media_type == "movie" else "tv"
    meta_key = f"tmdb:meta:{mtype}:{link_data.id}"

    # ── L1 cache hit ──────────────────────────────────────────────────────────
    try:
        cached_meta = await cache.get(meta_key)
        if cached_meta and isinstance(cached_meta, dict):
            kind: ContentKind = cached_meta.get("kind", default_kind)
            orig_title = cached_meta.get("org_title", "")
            updated: dict = {
                "is_anime": cached_meta.get("is_anime", False),
                "is_asian": cached_meta.get("is_asian", False),
                "is_bollywood": cached_meta.get("is_bollywood", False),
            }
            if orig_title and orig_title != link_data.title:
                updated["org_title"] = orig_title
            return link_data.model_copy(update=updated), kind
    except Exception:
        pass  # Cache read failure is non-fatal — continue to live fetch

    # ── Live TMDB fetch ────────────────────────────────────────────────────────
    try:
        url = (
            f"{_settings.tmdb_base_url}/{mtype}/{link_data.id}"
            f"?api_key={_settings.tmdb_api_key}&append_to_response=keywords"
        )
        res = await _tmdb_get(url)
        j = res.json() if res is not None else None
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
        orig_title = j.get("original_title") or j.get("original_name") or ""
        is_bollywood = "IN" in origin_countries

        # ── Write L1 cache ─────────────────────────────────────────────────────
        try:
            await cache.set(meta_key, {
                "kind": kind,
                "is_anime": kind == "anime",
                "is_asian": kind == "asian",
                "is_bollywood": is_bollywood,
                "org_title": orig_title,
            }, ttl=86_400)  # 24 hours
        except Exception:
            pass  # Cache write failure is non-fatal

        # ── Enrich LinkData ────────────────────────────────────────────────────
        updated = {
            "is_anime": kind == "anime",
            "is_asian": kind == "asian",
            "is_bollywood": is_bollywood,
        }
        if orig_title and orig_title != link_data.title:
            updated["org_title"] = orig_title

        return link_data.model_copy(update=updated), kind

    except Exception:
        return link_data, default_kind
