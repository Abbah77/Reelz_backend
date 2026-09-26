"""
ENGINE/providers/Shorts/R_303/R_303.py — iFunny (SeoCloud WeFeed) Shorts

Pulls short-form video posts from the iFunny community feed via the
SeoCloud WeFeed BFF API that the iFunny Android/web client uses.

Observed from network capture (devtools):
  GET https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group
        ?seoKey=lol-loop-GfRk3lGcil2&page=1&perPage=20
  Origin:        https://ifunny.club
  Referer:       https://ifunny.club/
  X-Client-Info: {"package_name":"movieboxbuzz","timezone":"Africa/Lagos"}

Confirmed response structure (from live devtools capture):
  {
    "code": 0,
    "message": "...",
    "data": {
      "pager": { "hasMore": true, "nextPage": 2, ... },
      "items": [
        {
          "itemType": "POST",
          "postId": "...",
          "userId": "...",
          "title": "...",
          "content": "...",          ← caption/text
          "mediaType": "VIDEO",      ← always uppercase
          "media": {
            "mediaType": "VIDEO",
            "cover": {
              "url": "https://pbcdn.aoneroom.com/image/...",   ← thumbnail
              ...
            },
            "video": [
              {
                "url": "https://macdn.aoneroom.com/vone/...", ← direct MP4
                "duration": 21,
                "height": 1024,
                "width": 576,
                "fps": 22,
                "bitrate": 344,
                "definition": 0
              }
            ]
          },
          "stat": { "likeCount": 12337, "commentCount": 75, "shareCount": 5209 },
          ...
        }
      ]
    }
  }

The seoKey "lol-loop-GfRk3lGcil2" was observed live in the capture;
all other keys were observed in prior sessions and remain in rotation.

Type: mp4
Flow:
  1. Pick a random seoKey from the known-good list.
  2. Pick a random page (1–5) so repeated calls return different results.
  3. GET /post/list-trending/group with all required headers.
  4. Parse JSON → data.items → filter VIDEO posts → extract MP4 from
     media.video[0].url and thumbnail from media.cover.url.
"""
from __future__ import annotations

import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_BASE_URL = "https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group"
_ORIGIN   = "https://ifunny.club"
_REFERER  = "https://ifunny.club/"

# Group seoKeys — observed in live network captures.
# Format: <slug>-<random-suffix> assigned by the backend per group.
_SEO_KEYS = [
    "lol-loop-GfRk3lGcil2",          # comedy loop feed  ← confirmed live capture
    "meme-and-scream-yvTjtEYXgS4",   # comedy / meme compilations
    "shortv-QIHhE5Hp1m5",            # short video mix
    "netflix-and-more-GHlH5Uah4Y6",  # trailer / clip style
    "hot-girls-OjV29uXW1w4",         # trending lifestyle
    "relaxing-central-GjIDo5NSwv5",  # chill / nature
    "k-drama-club-IQF5pxWOoK9",      # K-drama clips
]

_PER_PAGE     = 20
_MAX_PAGE     = 5    # stay within first five pages to avoid empty tail pages
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
                return result

            body = resp.json()

            # Confirmed path from live capture: body["data"]["items"]
            data_obj = body.get("data") or {}
            items = data_obj.get("items") or []

            if not isinstance(items, list):
                return result

            for item in items:
                # Only handle video posts — API returns "VIDEO" (uppercase)
                media_type = (item.get("mediaType") or "").upper()
                if media_type != "VIDEO":
                    continue

                # ── Video URL ─────────────────────────────────────────────
                # Confirmed structure: item["media"]["video"][0]["url"]
                # The "video" field is a list of quality variants; take the first.
                media   = item.get("media") or {}
                videos  = media.get("video") or []
                video_url = ""
                if isinstance(videos, list) and videos:
                    video_url = (videos[0].get("url") or "").strip()

                if not video_url or not video_url.startswith("http"):
                    continue

                # ── Thumbnail ─────────────────────────────────────────────
                # Confirmed structure: item["media"]["cover"]["url"]
                cover = media.get("cover") or {}
                thumb_url = (cover.get("url") or "").strip() or None

                # ── Title ─────────────────────────────────────────────────
                # "title" is the post heading; "content" is the caption/text.
                title = (
                    item.get("title")
                    or item.get("content")
                    or seo_key.split("-")[0].capitalize()
                ).strip()
                if len(title) > 120:
                    title = title[:117] + "…"

                result.shorts.append(Short(
                    url       = video_url,
                    title     = title,
                    thumbnail = thumb_url,
                    referer   = _REFERER,
                    origin    = _ORIGIN,
                ))

        except Exception:
            pass

        return result
