"""
CATALOG/search.py — Search logic.

Supports multi (both types) and type-filtered searches.
Cursor is a base64-encoded page number for TMDB pagination.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from CATALOG.tmdb import search_multi, search_typed, normalise_card

_SEARCH_TTL = 300  # 5 min


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except Exception:
        return 1


_VALID_MEDIA_TYPES = {"movie", "tv"}


async def search(
    query: str,
    media_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
) -> dict:
    if not query or not query.strip():
        return {"items": [], "has_more": False, "next_cursor": None, "cache_ttl_ms": 0}

    page = _decode_cursor(cursor)

    if media_type and media_type in _VALID_MEDIA_TYPES:
        raw = await search_typed(query.strip(), media_type, page)
        results = raw.get("results", [])
        items = [
            normalise_card(r, media_type)
            for r in results[:limit]
        ]
    else:
        raw = await search_multi(query.strip(), page)
        results = raw.get("results", [])
        items = []
        for r in results:
            mt = r.get("media_type")
            if mt not in ("movie", "tv"):
                continue
            items.append(normalise_card(r, mt))
            if len(items) >= limit:
                break

    total_pages = raw.get("total_pages", 1)
    has_more = page < total_pages and len(results) > 0

    return {
        "items": items,
        "has_more": has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
        "cache_ttl_ms": _SEARCH_TTL * 1000,
    }
