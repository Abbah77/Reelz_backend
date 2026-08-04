"""
providers/stream/anime/provider.py — anime-specific stream providers.

These bail immediately for non-anime content (is_anime=False check)
so they add zero overhead to movie/series requests.
"""
from __future__ import annotations

import re

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Stream
from app.clients.http import app, safe_get, UA
from app.config import get_settings

_settings = get_settings()


def _anime_guard(data: LinkData) -> bool:
    """True if provider should skip (not anime content)."""
    return not data.is_anime


# ── AniZone ───────────────────────────────────────────────────────────────────

class AniZoneProvider(Provider):
    id = "anizone"
    name = "AniZone"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        if _anime_guard(data):
            return result
        base = _settings.anizone_base_url
        try:
            if data.season is None:
                url = f"{base}/embed/movie/{data.id}"
            else:
                ep = data.absolute_episode or data.episode or 1
                url = f"{base}/embed/{data.id}/{ep}"
            res = await safe_get(url, headers={"User-Agent": UA, "Referer": f"{base}/"})
            if not res or not res.is_successful:
                return result
            for m in re.finditer(r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', res.text):
                result.streams.append(Stream(
                    server="AniZone",
                    link=m.group(1),
                    type="m3u8",
                    headers={"Referer": f"{base}/"},
                ))
        except Exception:
            pass
        return result


# ── AniNeko ───────────────────────────────────────────────────────────────────

class AniNekoProvider(Provider):
    id = "anineko"
    name = "AniNeko"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        if _anime_guard(data):
            return result
        try:
            ep = data.absolute_episode or data.episode or 1
            url = f"https://anineko.com/embed/{data.id}/{ep}"
            result.streams.append(Stream(server="AniNeko", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── AnimeNoSub ────────────────────────────────────────────────────────────────

class AnimeNoSubProvider(Provider):
    id = "animenosub"
    name = "AnimeNoSub"
    kinds = ["anime"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        if _anime_guard(data):
            return result
        try:
            ep = data.absolute_episode or data.episode or 1
            url = f"https://animenosub.com/embed/{data.id}/{ep}"
            result.streams.append(Stream(server="AnimeNoSub", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── AnimeWorld (serves all kinds — has anime + non-anime content) ─────────────

class AnimeWorldProvider(Provider):
    id = "animeworld"
    name = "AnimeWorld"
    # No kind gate — intentionally serves all

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                url = f"https://www.animeworld.so/movie/{data.id}"
            else:
                ep = data.absolute_episode or data.episode or 1
                url = f"https://www.animeworld.so/play/{data.id}/{ep}"
            result.streams.append(Stream(server="AnimeWorld", link=url, type="iframe"))
        except Exception:
            pass
        return result
