"""
ENGINE/providers/Shorts/R-302/R_302.py — Archive.org TikTok Shorts

Pulls random short-form videos from the `tiktoks` collection on
archive.org.  Items in that collection are MPEG4 files (.mp4),
directly downloadable — no HLS/m3u8 wrapping needed.

Strategy (fast + random, no shuffle):
  1. Hit the Archive.org Advanced Search API to get the total result
     count for collection:tiktoks.
  2. Pick a random page offset (1..max_page) and fetch `count` items
     from that page.  This gives true random sampling without having
     to load the full corpus.
  3. For each identifier, build a direct .mp4 URL and thumbnail URL.
     No extra per-item HTTP call is made — Archive.org URLs are
     deterministic: https://archive.org/download/{id}/{id}.mp4

Tools needed: none (uses shared ENGINE HTTP client)
"""
from __future__ import annotations

import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_SEARCH_URL = "https://archive.org/advancedsearch.php"
_COLLECTION = "tiktoks"
_COUNT = 30          # items to pull per call — enough to fill several pages
_MAX_ROWS = 100      # archive.org hard cap for a single page response


class R302Provider(Provider):
    id = "R-302"
    name = "Archive.org TikToks"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client = await get_client()

            # ── Step 1: get total result count (cheap — rows=0) ───────────
            probe = await client.get(
                _SEARCH_URL,
                params={
                    "q": f"collection:{_COLLECTION} mediatype:movies",
                    "rows": 0,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=8,
            )
            if probe.status_code >= 400:
                return result

            total = probe.json().get("response", {}).get("numFound", 0)
            if not total:
                return result

            # ── Step 2: pick a random page and fetch items ────────────────
            # We request _MAX_ROWS per page; pick a random starting page so
            # every call lands on a different slice of the corpus.
            max_page = max(1, total // _MAX_ROWS)
            page = random.randint(1, max_page)

            search = await client.get(
                _SEARCH_URL,
                params={
                    "q": f"collection:{_COLLECTION} mediatype:movies",
                    "fl[]": "identifier,title,description",
                    "rows": _MAX_ROWS,
                    "page": page,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=10,
            )
            if search.status_code >= 400:
                return result

            docs = search.json().get("response", {}).get("docs", [])
            if not docs:
                return result

            # ── Step 3: random sample from this page, build URLs ──────────
            sample = random.sample(docs, min(_COUNT, len(docs)))

            for doc in sample:
                iid = doc.get("identifier", "").strip()
                if not iid:
                    continue

                title = (doc.get("title") or doc.get("description") or iid).strip()
                # Truncate long captions to something UI-friendly
                if len(title) > 120:
                    title = title[:117] + "…"

                # Direct MP4 — archive.org naming convention is consistent:
                # https://archive.org/download/{identifier}/{identifier}.mp4
                mp4_url = f"https://archive.org/download/{iid}/{iid}.mp4"

                # Thumbnail served by archive.org's image service
                thumb_url = (
                    f"https://archive.org/services/get-item-image.php"
                    f"?identifier={iid}&mediatype=movies&collection={_COLLECTION}"
                )

                result.shorts.append(Short(
                    url=mp4_url,
                    title=title,
                    thumbnail=thumb_url,
                ))

        except Exception:
            pass

        return result
