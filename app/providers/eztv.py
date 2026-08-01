"""
EZTV torrent provider — TV shows only.

EZTV exposes a real JSON API (no scraping, no Cloudflare):

  GET https://eztv.re/api/get-torrents
      ?imdb_id=<numeric_imdb_id>   # strip the leading "tt"
      &limit=100
      &page=1

Response shape:
  {
    "torrents_count": 42,
    "limit": 100,
    "page": 1,
    "torrents": [
      {
        "id": 369459,
        "hash": "...",
        "filename": "Show.S02E11.WEB.H264-GROUP[eztv].mkv",
        "title": "Show S02E11 WEB H264 GROUP EZTV",
        "torrent_url": "https://...",
        "magnet_url": "magnet:?xt=...",
        "seeds": 19,
        "peers": 13,
        "size_bytes": "2616073115",
        "date_released_unix": 1503992162,
        "season": "2",
        "episode": "11",
        "small_screenshot": "",
        "large_screenshot": "",
        "imdb_id": "5016504"
      },
      ...
    ]
  }

Integration strategy:
  - For a specific episode request (season + episode set): return only the matching
    season/episode torrents, ranked by seeds desc.
  - For a movie request (season is None): EZTV is TV-only so we return empty.
  - Results are returned as type="torrent" streams whose `link` is the magnet URI.
    The orchestrator / torrent subsystem handles playback the same way as the
    existing TorrentMovieProvider / TorrentTvProvider.
  - We try up to 3 domain mirrors in order; the API path is identical across them.
  - Seeds/peers metadata is stored in the quality field for display.

This provider is TORRENT_MODE only — it is registered in torrent.py alongside
the existing TorrentMovieProvider and TorrentTvProvider.
"""
from __future__ import annotations

import re
from typing import Optional

from app.models import LinkData, ExtractorResult, Stream
from app.providers.base import Provider
from app.utils.http import safe_get, UA

# EZTV mirrors — try in order, stop on first success
_MIRRORS = [
    "https://eztv.re",
    "https://eztvtorrent.co",
    "https://eztv-official.is",
]

_API_PATH = "/api/get-torrents"
_LIMIT = 100


def _numeric_imdb(imdb_id: Optional[str]) -> Optional[str]:
    """Strip the 'tt' prefix → EZTV expects a bare number."""
    if not imdb_id:
        return None
    m = re.match(r"tt(\d+)", imdb_id)
    return m.group(1) if m else None


def _quality_label(torrent: dict) -> str:
    """Extract a human-readable quality tag from the torrent filename/title."""
    filename = torrent.get("filename") or torrent.get("title") or ""
    for tag in ("2160p", "4K", "1080p", "720p", "480p", "360p"):
        if tag.lower() in filename.lower():
            return tag
    return "Unknown"


class EztvProvider(Provider):
    """
    EZTV — TV show torrent index with a real JSON API.
    Only active in torrent mode (registered via app/providers/torrent.py).
    Skips non-TV content and content without an IMDB id.
    """
    id = "eztv"
    name = "EZTV"
    kinds = ["series"]   # TV only — EZTV has no movie catalogue worth using

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()

        # Movies have season=None; EZTV is TV-only
        if data.season is None:
            return result

        numeric_imdb = _numeric_imdb(data.imdb_id)
        if not numeric_imdb:
            return result

        torrents: list[dict] = []

        for mirror in _MIRRORS:
            try:
                url = (
                    f"{mirror}{_API_PATH}"
                    f"?imdb_id={numeric_imdb}"
                    f"&limit={_LIMIT}"
                    f"&page=1"
                )
                res = await safe_get(url, headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Referer": f"{mirror}/",
                })
                if not res or not res.is_successful:
                    continue

                j = res.json() or {}
                raw = j.get("torrents") or []
                if raw:
                    torrents = raw
                    break   # got data from this mirror

            except Exception:
                continue

        if not torrents:
            return result

        # Filter to the requested season + episode
        want_s = str(data.season)
        want_e = str(data.episode) if data.episode is not None else None

        matched: list[dict] = []
        for t in torrents:
            t_season = str(t.get("season") or "")
            t_episode = str(t.get("episode") or "")
            if t_season != want_s:
                continue
            if want_e and t_episode != want_e:
                continue
            matched.append(t)

        # Sort by seeds descending — most available torrent first
        matched.sort(key=lambda t: int(t.get("seeds") or 0), reverse=True)

        for torrent in matched:
            magnet = torrent.get("magnet_url") or ""
            if not magnet.startswith("magnet:"):
                continue

            seeds = torrent.get("seeds") or 0
            peers = torrent.get("peers") or 0
            quality = _quality_label(torrent)
            title = torrent.get("title") or torrent.get("filename") or "EZTV"

            result.streams.append(Stream(
                server=f"EZTV [{quality}] ↑{seeds}s/{peers}p",
                link=magnet,
                type="torrent",           # handled by the torrent subsystem
                quality=quality,
                headers={},               # no HTTP headers for magnet links
            ))

        return result
