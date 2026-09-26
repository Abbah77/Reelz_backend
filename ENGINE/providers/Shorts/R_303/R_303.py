"""
ENGINE/providers/Shorts/R_303/R_303.py — iFunny (SeoCloud WeFeed) Shorts

Pulls short-form video posts from the iFunny community feed via the
SeoCloud WeFeed BFF API used by the iFunny Android/web client.

BUG FIXED: The API response wraps items under body["data"]["list"] in newer
versions, NOT body["data"]["items"]. We now check both keys so it works
regardless of which field name the API returns.

Also tries multiple seoKeys in parallel rather than picking one randomly,
so a single dead key doesn't cause an empty result.
"""
from __future__ import annotations

import asyncio
import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_BASE_URL = "https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group"
_ORIGIN   = "https://ifunny.club"
_REFERER  = "https://ifunny.club/"

_SEO_KEYS = [
    "lol-loop-GfRk3lGcil2",
    "meme-and-scream-yvTjtEYXgS4",
    "shortv-QIHhE5Hp1m5",
    "netflix-and-more-GHlH5Uah4Y6",
    "hot-girls-OjV29uXW1w4",
    "relaxing-central-GjIDo5NSwv5",
    "k-drama-club-IQF5pxWOoK9",
]

_PER_PAGE     = 20
_MAX_PAGE     = 5
_HTTP_TIMEOUT = 10
_TRY_KEYS     = 3   # try this many keys in parallel; first with results wins


def _parse_items(body: dict) -> list:
    """Handle both 'items' and 'list' field names the API has used."""
    data_obj = body.get("data") or {}
    return data_obj.get("items") or data_obj.get("list") or []


class R303Provider(Provider):
    id   = "R-303"
    name = "iFunny WeFeed Shorts"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client   = await get_client()
            keys     = random.sample(_SEO_KEYS, min(_TRY_KEYS, len(_SEO_KEYS)))
            page     = random.randint(1, _MAX_PAGE)

            async def fetch_key(seo_key: str) -> list[Short]:
                shorts = []
                try:
                    resp = await client.get(
                        _BASE_URL,
                        params={
                            "seoKey":  seo_key,
                            "page":    page,
                            "perPage": _PER_PAGE,
                        },
                        headers={
                            "User-Agent":      UA,
                            "Accept":          "application/json",
                            "Origin":          _ORIGIN,
                            "Referer":         _REFERER,
                            "Accept-Language": "en-US,en;q=0.9",
                            "X-Client-Info":   '{"package_name":"movieboxbuzz","timezone":"Africa/Lagos"}',
                        },
                        timeout=_HTTP_TIMEOUT,
                    )
                    if resp.status_code >= 400:
                        return shorts

                    body  = resp.json()
                    # Guard: API sometimes returns {"code": 0} with no data on bad key
                    if body.get("code", -1) != 0:
                        return shorts

                    items = _parse_items(body)
                    if not isinstance(items, list):
                        return shorts

                    for item in items:
                        media_type = (item.get("mediaType") or "").upper()
                        if media_type != "VIDEO":
                            continue

                        media     = item.get("media") or {}
                        videos    = media.get("video") or []
                        video_url = ""
                        if isinstance(videos, list) and videos:
                            video_url = (videos[0].get("url") or "").strip()

                        if not video_url or not video_url.startswith("http"):
                            continue

                        cover     = media.get("cover") or {}
                        thumb_url = (cover.get("url") or "").strip() or None

                        title = (
                            item.get("title")
                            or item.get("content")
                            or seo_key.split("-")[0].capitalize()
                        ).strip()
                        if len(title) > 120:
                            title = title[:117] + "…"

                        shorts.append(Short(
                            url       = video_url,
                            title     = title,
                            thumbnail = thumb_url,
                            referer   = _REFERER,
                            origin    = _ORIGIN,
                        ))
                except Exception:
                    pass
                return shorts

            # Run all keys concurrently; merge results
            all_results = await asyncio.gather(*[fetch_key(k) for k in keys])
            for batch in all_results:
                result.shorts.extend(batch)

        except Exception:
            pass

        return result
