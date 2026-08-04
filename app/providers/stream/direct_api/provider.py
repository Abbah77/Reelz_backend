"""
providers/stream/direct_api/provider.py

All "direct API" stream providers — no Cloudflare, pure JSON endpoints.
Each is a self-contained class. Adding one: copy a class, tweak the URL.
Deleting one: remove the class and the registry import. Nothing else changes.
"""
from __future__ import annotations

import re

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Stream
from app.clients.http import app, UA
from app.utils.encdec import enc_dec_get


# ── TwoEmbed ──────────────────────────────────────────────────────────────────

class TwoEmbedProvider(Provider):
    id = "twoembed"
    name = "2Embed"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                url = f"https://www.2embed.cc/embed/{data.id}"
            else:
                url = f"https://www.2embed.cc/embedtv/{data.id}&s={data.season}&e={data.episode}"
            result.streams.append(Stream(server="2Embed", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── VidFast ───────────────────────────────────────────────────────────────────

class VidFastProvider(Provider):
    id = "vidfast"
    name = "VidFast"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                api_url = f"https://vidfast.co/api/movie/{data.id}"
            else:
                api_url = f"https://vidfast.co/api/tv/{data.id}/{data.season}/{data.episode}"
            res = await app.get(api_url, headers={"User-Agent": UA, "Referer": "https://vidfast.co/"})
            if not res or not res.is_successful:
                return result
            j = res.json()
            if not j:
                return result
            sources = j.get("sources") or j.get("data", {}).get("sources", [])
            for src in (sources if isinstance(sources, list) else []):
                url = src.get("url") or src.get("file", "")
                if url:
                    result.streams.append(Stream(
                        server="VidFast",
                        link=url,
                        type="m3u8" if ".m3u8" in url else "mp4",
                        quality=src.get("label"),
                    ))
        except Exception:
            pass
        return result


# ── VidRock ───────────────────────────────────────────────────────────────────

class VidRockProvider(Provider):
    id = "vidrock"
    name = "VidRock"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                url = f"https://vidrock.to/embed/movie/{data.id}"
            else:
                url = f"https://vidrock.to/embed/tv/{data.id}/{data.season}/{data.episode}"
            result.streams.append(Stream(server="VidRock", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── HexaSU ────────────────────────────────────────────────────────────────────

class HexaSUProvider(Provider):
    id = "hexasu"
    name = "HexaSU"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            base = "https://hexasu.com"
            if data.season is None:
                embed_url = f"{base}/embed/movie/{data.id}"
            else:
                embed_url = f"{base}/embed/tv/{data.id}/{data.season}/{data.episode}"
            # Step 1: load embed page to get token
            headers = {"User-Agent": UA, "Referer": f"{base}/"}
            res = await app.get(embed_url, headers=headers)
            if not res or not res.is_successful:
                return result
            # Step 2: find stream API call in page
            token_match = re.search(r'token["\s:=]+["\']([a-zA-Z0-9_-]+)["\']', res.text)
            if not token_match:
                result.streams.append(Stream(server="HexaSU", link=embed_url, type="iframe"))
                return result
            token = token_match.group(1)
            api_url = f"{base}/api/source/{token}"
            api_res = await app.post(api_url, headers={**headers, "X-Requested-With": "XMLHttpRequest"})
            if not api_res or not api_res.is_successful:
                return result
            j = api_res.json()
            if not j or not j.get("success"):
                return result
            for src in j.get("data", []):
                url = src.get("file", "")
                if url:
                    result.streams.append(Stream(
                        server="HexaSU",
                        link=url,
                        type="m3u8" if ".m3u8" in url else "mp4",
                        quality=src.get("label"),
                        headers={"Referer": base + "/"},
                    ))
        except Exception:
            pass
        return result


# ── AllMovieLand ──────────────────────────────────────────────────────────────

class AllMovieLandProvider(Provider):
    id = "allmovieland"
    name = "AllMovieLand"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            base = "https://allmovieland.com"
            if data.season is None:
                url = f"{base}/embed2/?id={data.id}"
            else:
                url = f"{base}/embed2/?id={data.id}&s={data.season}&e={data.episode}"
            res = await app.get(url, headers={"User-Agent": UA, "Referer": f"{base}/"})
            if not res or not res.is_successful:
                return result
            m = re.search(r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', res.text)
            if m:
                result.streams.append(Stream(
                    server="AllMovieLand",
                    link=m.group(1),
                    type="m3u8",
                    headers={"Referer": base + "/"},
                ))
            else:
                result.streams.append(Stream(server="AllMovieLand", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── XPass ─────────────────────────────────────────────────────────────────────

class XpassProvider(Provider):
    id = "xpass"
    name = "XPass"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                url = f"https://xpassed.com/embed/movie/{data.id}"
            else:
                url = f"https://xpassed.com/embed/tv/{data.id}/{data.season}/{data.episode}"
            result.streams.append(Stream(server="XPass", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── VaplayerV2 ────────────────────────────────────────────────────────────────

class VaplayerV2Provider(Provider):
    id = "vaplayerv2"
    name = "Vaplayer"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            base = "https://streamdata.vaplayer.ru"
            if data.season is None:
                api_url = f"{base}/api/source/movie/{data.id}"
            else:
                api_url = f"{base}/api/source/tv/{data.id}/{data.season}/{data.episode}"
            res = await app.get(api_url, headers={"User-Agent": UA, "Referer": "https://vaplayer.ru/"})
            if not res or not res.is_successful:
                return result
            j = res.json()
            if not j:
                return result
            for src in (j.get("sources") or []):
                url = src.get("url") or src.get("file", "")
                if url:
                    result.streams.append(Stream(
                        server=f"Vaplayer [{src.get('label', '')}]",
                        link=url,
                        type="m3u8" if ".m3u8" in url else "mp4",
                        quality=src.get("label"),
                        headers={"Referer": "https://vaplayer.ru/"},
                    ))
        except Exception:
            pass
        return result


# ── DahmerMovies ──────────────────────────────────────────────────────────────

class DahmerMoviesProvider(Provider):
    id = "dahmermovies"
    name = "DahmerMovies"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        try:
            if data.season is None:
                url = f"https://dahmermovies.com/embed/movie/{data.id}"
            else:
                url = f"https://dahmermovies.com/embed/tv/{data.id}/{data.season}/{data.episode}"
            result.streams.append(Stream(server="DahmerMovies", link=url, type="iframe"))
        except Exception:
            pass
        return result


# ── Stubs kept for reference but not registered ──────────────────────────────

class HexaProvider(Provider):
    """Old Hexa stub — replaced by HexaSUProvider."""
    id = "hexa"
    name = "Hexa (legacy)"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return ProviderResult()


class VaplayerProvider(Provider):
    """Old Vaplayer stub — replaced by VaplayerV2Provider."""
    id = "vaplayer"
    name = "Vaplayer (legacy)"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return ProviderResult()
