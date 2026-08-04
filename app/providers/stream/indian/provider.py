"""
providers/stream/indian/provider.py — Indian Cloudflare-gated stream providers.

All share the same pattern:
  1. Search site for title.
  2. Find episode/movie page.
  3. Extract m3u8/mp4 links.

Each is a standalone class. Delete a class + remove from registry = done.
"""
from __future__ import annotations

import re
from typing import Optional

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, Stream
from app.clients.http import safe_get, UA
from app.utils.retry import retry


def _build_query(data: LinkData) -> str:
    q = data.title
    if data.year:
        q += f" {data.year}"
    return q


def _find_m3u8(text: str, referer: str) -> list[Stream]:
    streams = []
    for m in re.finditer(r'["\']([^"\']*\.m3u8[^"\']*)["\']', text):
        link = m.group(1)
        if link.startswith("http"):
            streams.append(Stream(
                server="",           # filled by caller
                link=link,
                type="m3u8",
                headers={"Referer": referer},
            ))
    return streams


async def _scrape_indian(
    base: str,
    data: LinkData,
    provider_name: str,
) -> ProviderResult:
    """
    Generic Indian scraper pattern:
      search → result page → extract m3u8/mp4
    """
    result = ProviderResult()
    try:
        query = _build_query(data)
        search_url = f"{base}/?s={query.replace(' ', '+')}"
        res = await retry(2, lambda: safe_get(search_url, cloudflare=True,
                                               headers={"User-Agent": UA, "Referer": f"{base}/"}))
        if not res or not res.is_successful:
            return result

        soup = res.document
        link = soup.select_one(".entry-title a, .post-title a, article a")
        if not link:
            return result

        page_url = link.get("href", "")
        if not page_url or not page_url.startswith("http"):
            return result

        page = await retry(2, lambda: safe_get(page_url, cloudflare=True,
                                                headers={"User-Agent": UA, "Referer": search_url}))
        if not page or not page.is_successful:
            return result

        streams = _find_m3u8(page.text, page_url)
        for s in streams:
            s.server = provider_name
        result.streams.extend(streams)

        # Fallback: look for mp4 links
        if not result.streams:
            for m in re.finditer(r'["\']([^"\']*\.mp4[^"\']*)["\']', page.text):
                link_url = m.group(1)
                if link_url.startswith("http"):
                    result.streams.append(Stream(
                        server=provider_name,
                        link=link_url,
                        type="mp4",
                        headers={"Referer": page_url},
                    ))

    except Exception:
        pass
    return result


# ── Indian providers — one class per site ─────────────────────────────────────
# Changing a domain: edit the BASE constant inside the class. That's it.

class VegaMoviesProvider(Provider):
    id = "vegamovies"
    name = "VegaMovies"
    kinds = ["movie", "series", "asian"]
    BASE = "https://vegamovies.ms"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class HdHub4uProvider(Provider):
    id = "hdhub4u"
    name = "HdHub4u"
    kinds = ["movie", "series", "asian"]
    BASE = "https://hdhub4u.skin"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class FourKHdHubProvider(Provider):
    id = "fourkhhub"
    name = "4KHdHub"
    kinds = ["movie", "series", "asian"]
    BASE = "https://4khdub.com"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class Movies4uProvider(Provider):
    id = "movies4u"
    name = "Movies4u"
    kinds = ["movie", "series", "asian"]
    BASE = "https://movies4u.hair"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class RogMoviesProvider(Provider):
    id = "rogmovies"
    name = "RogMovies"
    kinds = ["movie", "series", "asian"]
    BASE = "https://rogmovies.com"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class MultiMoviesProvider(Provider):
    id = "multimovies"
    name = "MultiMovies"
    kinds = ["movie", "series", "asian"]
    BASE = "https://multimovies.store"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class UhdMoviesProvider(Provider):
    id = "uhdmovies"
    name = "UhdMovies"
    kinds = ["movie", "series", "asian"]
    BASE = "https://uhdmovies.world"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class MoviesModProvider(Provider):
    id = "moviesmod"
    name = "MoviesMod"
    kinds = ["movie", "series", "asian"]
    BASE = "https://moviesmod.day"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class TopMoviesProvider(Provider):
    id = "topmovies"
    name = "TopMovies"
    kinds = ["movie", "series", "asian"]
    BASE = "https://topmovies.rent"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class BollyflixProvider(Provider):
    id = "bollyflix"
    name = "Bollyflix"
    kinds = ["movie", "series", "asian"]
    BASE = "https://bollyflix.land"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)


class CineMacityProvider(Provider):
    id = "cinemacity"
    name = "CineMacity"
    kinds = ["movie", "series", "asian"]
    BASE = "https://cinemacity.pro"

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_indian(self.BASE, data, self.name)
