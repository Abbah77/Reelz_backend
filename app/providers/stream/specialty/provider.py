"""
providers/stream/specialty/provider.py — specialty stream providers.

KissKh:  Asian drama (Korean/Chinese/Thai) platform.
Castle:  Indian multi-language content (Hindi/Tamil/Telugu + anime dub).
HDRezka: Large multi-audio catalog (Russian/Eastern European CDN).
"""
from __future__ import annotations

import re

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Stream
from app.clients.http import app, safe_get, UA
from app.config import get_settings

_settings = get_settings()


# ── KissKh ────────────────────────────────────────────────────────────────────

class KissKhProvider(Provider):
    id = "kisskh"
    name = "KissKh"
    kinds = ["asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            base = "https://kisskh.co"
            if data.season is None:
                url = f"{base}/Drama/Embed?id={data.tmdb_id if hasattr(data, 'tmdb_id') else data.id}"
            else:
                url = f"{base}/Drama/Embed?id={data.id}&ep={data.episode}"
            result.streams.append(Stream(server="KissKh", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── Castle (Indian multi-language) ────────────────────────────────────────────

class CastleProvider(Provider):
    id = "castle"
    name = "Castle"
    kinds = ["movie", "series", "asian", "anime"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        suffix = _settings.castle_suffix
        if not suffix:
            return result
        try:
            base = f"https://castle{suffix}.com"
            if data.season is None:
                url = f"{base}/embed/{data.id}"
            else:
                url = f"{base}/embed/{data.id}/{data.season}/{data.episode}"
            res = await app.get(url, headers={"User-Agent": UA, "Referer": f"{base}/"})
            if not res or not res.is_successful:
                return result

            # Extract sources from page script
            for m in re.finditer(
                r'\{[^{}]*"file"\s*:\s*"([^"]+\.m3u8[^"]*)"[^{}]*"title"\s*:\s*"([^"]*)"',
                res.text,
            ):
                stream_url, title = m.group(1), m.group(2)
                result.streams.append(Stream(
                    server=f"Castle {title}" if title else "Castle",
                    link=stream_url,
                    type="m3u8",
                    headers={"Referer": f"{base}/"},
                ))
        except Exception:
            pass
        return result


# ── HDRezka ───────────────────────────────────────────────────────────────────

class HDRezkaProvider(Provider):
    id = "hdrezka"
    name = "HDRezka"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        base = _settings.hdrezka_base_url
        try:
            # Search by title + year
            search_url = f"{base}/search/?do=search&subaction=search&q={data.title}"
            res = await safe_get(search_url, headers={"User-Agent": UA, "Referer": f"{base}/"})
            if not res or not res.is_successful:
                return result

            soup = res.document
            first = soup.select_one(".b-content__inline_item a")
            if not first:
                return result

            page_url = first.get("href", "")
            if not page_url:
                return result

            page = await safe_get(page_url, headers={"User-Agent": UA, "Referer": search_url})
            if not page or not page.is_successful:
                return result

            # Extract stream ID and translator
            id_m = re.search(r'id_post\s*=\s*(\d+)', page.text)
            if not id_m:
                return result
            content_id = id_m.group(1)

            # Build embed URL with first available translator
            tr_m = re.search(r'translations\.push\(\{.*?id:(\d+)', page.text, re.S)
            translator_id = tr_m.group(1) if tr_m else "0"

            if data.season is None:
                ajax_url = f"{base}/ajax/get_cdn_series/"
                payload = f"id={content_id}&translator_id={translator_id}&action=get_movie"
            else:
                ajax_url = f"{base}/ajax/get_cdn_series/"
                payload = (
                    f"id={content_id}&translator_id={translator_id}"
                    f"&season={data.season}&episode={data.episode}&action=get_stream"
                )

            ajax = await app.post(
                ajax_url,
                body=payload,
                headers={
                    "User-Agent": UA,
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if not ajax or not ajax.is_successful:
                return result

            j = ajax.json()
            if not j or not j.get("success"):
                return result

            url_str = j.get("url", "")
            for quality_block in re.finditer(r'\[(\d+p)\](https?://[^,\s]+)', url_str):
                quality, stream_url = quality_block.group(1), quality_block.group(2)
                result.streams.append(Stream(
                    server=f"HDRezka {quality}",
                    link=stream_url,
                    type="mp4" if ".mp4" in stream_url else "m3u8",
                    quality=quality,
                    headers={"Referer": page_url},
                ))

        except Exception:
            pass
        return result


# ── Vidlink (disabled — dead upstream, easy to revive) ────────────────────────

class VidlinkProvider(Provider):
    id = "vidlink"
    name = "Vidlink"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return ProviderResult()
