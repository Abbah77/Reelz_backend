"""
ENGINE/providers/Shorts/R_302/R_302.py — Archive.org TikTok Shorts

Pulls short-form videos from the `tiktoks` collection on archive.org.

The old strategy assumed every item's downloadable file was named
{identifier}.mp4.  Archive.org changed: many items use different
filenames or sub-paths.  The correct pattern is now:

  1. Search the Advanced Search API (as before) to get identifiers.
  2. For each identifier, call the metadata API:
       https://archive.org/metadata/{identifier}/files
     to find the first .mp4 file entry.  The canonical download URL is:
       https://archive.org/download/{identifier}/{filename}
  3. Thumbnail: use the item-image endpoint (still valid):
       https://archive.org/services/img/{identifier}

To avoid per-item metadata calls blocking the whole response, we resolve
only a small batch (RESOLVE_LIMIT) and stop as soon as WANT valid URLs
are found.

Type: mp4
Flow:
  1. Probe total count via Advanced Search (rows=0).
  2. Pick a random page, fetch FETCH_ROWS identifiers.
  3. For each identifier (up to RESOLVE_LIMIT), call metadata API to
     find the real .mp4 filename.
  4. Build Short objects with verified URLs.
"""
from __future__ import annotations

import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_SEARCH_URL   = "https://archive.org/advancedsearch.php"
_META_URL     = "https://archive.org/metadata/{identifier}/files"
_THUMB_URL    = "https://archive.org/services/img/{identifier}"
_COLLECTION   = "tiktoks"
_FETCH_ROWS   = 100   # items fetched from search per call
_RESOLVE_LIMIT = 60   # max identifiers we attempt to resolve
_WANT          = 30   # stop once we have this many valid shorts
_HTTP_TIMEOUT  = 8    # seconds per HTTP call


class R302Provider(Provider):
    id   = "R-302"
    name = "Archive.org TikToks"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client = await get_client()

            # ── Step 1: probe total count ─────────────────────────────────
            probe = await client.get(
                _SEARCH_URL,
                params={
                    "q":      f"collection:{_COLLECTION} mediatype:movies",
                    "rows":   0,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=_HTTP_TIMEOUT,
            )
            if probe.status_code >= 400:
                return result

            total = probe.json().get("response", {}).get("numFound", 0)
            if not total:
                return result

            # ── Step 2: random page, fetch identifiers ────────────────────
            max_page = max(1, total // _FETCH_ROWS)
            page     = random.randint(1, max_page)

            search = await client.get(
                _SEARCH_URL,
                params={
                    "q":      f"collection:{_COLLECTION} mediatype:movies",
                    "fl[]":   "identifier,title,description",
                    "rows":   _FETCH_ROWS,
                    "page":   page,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=_HTTP_TIMEOUT,
            )
            if search.status_code >= 400:
                return result

            docs = search.json().get("response", {}).get("docs", [])
            if not docs:
                return result

            # Shuffle so we don't always resolve the same page-top items
            random.shuffle(docs)

            # ── Step 3: resolve real filenames via metadata API ───────────
            for doc in docs[:_RESOLVE_LIMIT]:
                if len(result.shorts) >= _WANT:
                    break

                iid = doc.get("identifier", "").strip()
                if not iid:
                    continue

                try:
                    meta_resp = await client.get(
                        _META_URL.format(identifier=iid),
                        headers={"User-Agent": UA},
                        timeout=_HTTP_TIMEOUT,
                    )
                    if meta_resp.status_code >= 400:
                        continue

                    files = meta_resp.json()
                    if not isinstance(files, list):
                        continue

                    # Find first mp4 that is not a derivative/thumbnail
                    mp4_name = None
                    for f in files:
                        name = f.get("name", "")
                        fmt  = f.get("format", "").lower()
                        source = f.get("source", "").lower()
                        if (
                            name.lower().endswith(".mp4")
                            and source != "derivative"
                            and "thumb" not in name.lower()
                        ):
                            mp4_name = name
                            break

                    # Fall back to any mp4 if no original found
                    if not mp4_name:
                        for f in files:
                            name = f.get("name", "")
                            if name.lower().endswith(".mp4") and "thumb" not in name.lower():
                                mp4_name = name
                                break

                    if not mp4_name:
                        continue

                except Exception:
                    continue

                title = (
                    doc.get("title") or doc.get("description") or iid
                ).strip()
                if len(title) > 120:
                    title = title[:117] + "…"

                mp4_url   = f"https://archive.org/download/{iid}/{mp4_name}"
                thumb_url = _THUMB_URL.format(identifier=iid)

                result.shorts.append(Short(
                    url       = mp4_url,
                    title     = title,
                    thumbnail = thumb_url,
                ))

        except Exception:
            pass

        return result
