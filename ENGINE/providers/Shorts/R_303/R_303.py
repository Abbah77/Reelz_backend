"""
ENGINE/providers/Shorts/R_303/R_303.py — iFunny (SeoCloud WeFeed) Shorts

Pulls short-form video posts from the iFunny community feed via the
SeoCloud WeFeed BFF API that the iFunny Android/web client uses.

Observed from network capture (devtools):
  GET https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group
        ?seoKey=<group-key>&page=1&perPage=20
  Origin: https://ifunny.club
  X-Client-Info: {"package_name":"movieboxbuzz","timezone":"Africa/Lagos"}

The endpoint returns a JSON body containing an array of post objects.
Each post with mediaType "video" carries a direct MP4 playback URL and
a thumbnail image URL — both are playable without auth.

Multiple group seoKeys are rotated at random so results vary across
calls (the same groups seen in the capture):
  • meme-and-scream   — comedy/meme compilations
  • shortv            — short video mix
  • netflix-and-more  — trailer/clip style
  • hot-girls         — trending lifestyle
  • relaxing-central  — chill/nature
  • k-drama-club      — K-drama clips

Type: mp4
Flow:
  1. Pick a random seoKey from the list.
  2. Pick a random page (1–5) so repeated calls return different results.
  3. GET /post/list-trending/group with required headers.
  4. Parse JSON → find video posts → build Short objects.
"""
from __future__ import annotations

import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_BASE_URL = "https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group"
_ORIGIN   = "https://ifunny.club"
_REFERER  = "https://ifunny.club/"

# Group seoKeys observed in the network capture; each maps to a themed feed.
_SEO_KEYS = [
    "meme-and-scream-yvTjtEYXgS4",   # comedy / meme
    "shortv-QIHhE5Hp1m5",            # short video mix
    "netflix-and-more-GHlH5Uah4Y6",  # trailer / clips
    "hot-girls-OjV29uXW1w4",         # trending lifestyle
    "relaxing-central-GjIDo5NSwv5",  # chill / nature
    "k-drama-club-IQF5pxWOoK9",      # K-drama clips
]

_PER_PAGE    = 20
_MAX_PAGE    = 5       # stay within the first five pages to avoid empty results
_HTTP_TIMEOUT = 10


class R303Provider(Provider):
    id   = "R-303"
    name = "iFunny WeFeed Shorts"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client  = await get_client()
            seo_key = random.choice(_SEO_KEYS)
            page    = random.randint(1, _MAX_PAGE)

            resp = await client.get(
                _BASE_URL,
                params={
                    "seoKey":  seo_key,
                    "page":    page,
                    "perPage": _PER_PAGE,
                },
                headers={
                    "User-Agent":    UA,
                    "Accept":        "application/json",
                    "Origin":        _ORIGIN,
                    "Referer":       _REFERER,
                    "Accept-Language": "en-US,en;q=0.9",
                    "X-Client-Info": '{"package_name":"movieboxbuzz","timezone":"Africa/Lagos"}',
                },
                timeout=_HTTP_TIMEOUT,
            )

            if resp.status_code >= 400:
                return result

            body = resp.json()

            # The API wraps results under different keys depending on version;
            # try the most common paths.
            posts = (
                body.get("data", {}).get("posts")
                or body.get("data", {}).get("items")
                or body.get("posts")
                or body.get("items")
                or []
            )

            if not isinstance(posts, list):
                return result

            for post in posts:
                # Only handle video posts
                media_type = (post.get("mediaType") or post.get("type") or "").lower()
                if media_type not in ("video", "mp4"):
                    continue

                # Video URL — try several common field names
                video_url = (
                    post.get("videoUrl")
                    or post.get("url")
                    or post.get("mediaUrl")
                    or (post.get("media") or {}).get("url")
                    or ""
                ).strip()

                if not video_url or not video_url.startswith("http"):
                    continue

                # Thumbnail — prefer a dedicated thumb field
                thumb_url = (
                    post.get("thumbnailUrl")
                    or post.get("thumbnail")
                    or post.get("coverUrl")
                    or (post.get("thumbnail") or {}).get("url")
                    or ""
                ).strip() or None

                # Title — use caption / title / creator name
                title = (
                    post.get("title")
                    or post.get("caption")
                    or post.get("description")
                    or post.get("text")
                    or seo_key.split("-")[0].capitalize()
                ).strip()
                if len(title) > 120:
                    title = title[:117] + "…"

                result.shorts.append(Short(
                    url       = video_url,
                    title     = title,
                    thumbnail = thumb_url or None,
                    referer   = _REFERER,
                    origin    = _ORIGIN,
                ))

        except Exception:
            pass

        return result
