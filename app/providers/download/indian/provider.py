"""
providers/download/indian/provider.py — Indian direct-download providers.

These surface mp4/mkv download links from Indian piracy sites.
All follow the same search → page → extract pattern.
"""
from __future__ import annotations

import re

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult, DownloadItem
from app.clients.http import safe_get, UA
from app.utils.retry import retry


def _build_query(data: LinkData) -> str:
    q = data.title
    if data.year:
        q += f" {data.year}"
    return q


async def _scrape_download_links(base: str, data: LinkData, provider_name: str) -> ProviderResult:
    result = ProviderResult()
    try:
        query = _build_query(data)
        search_url = f"{base}/?s={query.replace(' ', '+')}"
        res = await retry(2, lambda: safe_get(search_url, cloudflare=True,
                                               headers={"User-Agent": UA, "Referer": f"{base}/"}))
        if not res or not res.is_successful:
            return result

        soup = res.document
        link_el = soup.select_one(".entry-title a, .post-title a, article a")
        if not link_el:
            return result

        page_url = link_el.get("href", "")
        if not page_url or not page_url.startswith("http"):
            return result

        page = await retry(2, lambda: safe_get(page_url, cloudflare=True,
                                                headers={"User-Agent": UA, "Referer": search_url}))
        if not page or not page.is_successful:
            return result

        # Extract direct download links: .mp4, .mkv
        for m in re.finditer(r'href=["\']([^"\']*\.(mp4|mkv)[^"\']*)["\']', page.text):
            link = m.group(1)
            if not link.startswith("http"):
                continue
            # Try to infer quality from surrounding text
            quality_m = re.search(r'(4k|2160p|1080p|720p|480p|360p)', link, re.I)
            quality = quality_m.group(1).lower() if quality_m else None
            result.downloads.append(DownloadItem(
                server=provider_name,
                link=link,
                type=m.group(2),
                quality=quality,
                headers={"Referer": page_url},
            ))

    except Exception:
        pass
    return result


class VegaMoviesDownloadProvider(Provider):
    id = "vegamovies_dl"
    name = "VegaMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://vegamovies.ms", data, "VegaMovies")


class HdHub4uDownloadProvider(Provider):
    id = "hdhub4u_dl"
    name = "HdHub4u"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://hdhub4u.skin", data, "HdHub4u")


class FourKHdHubDownloadProvider(Provider):
    id = "fourkhhub_dl"
    name = "4KHdHub"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://4khdub.com", data, "4KHdHub")


class Movies4uDownloadProvider(Provider):
    id = "movies4u_dl"
    name = "Movies4u"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://movies4u.hair", data, "Movies4u")


class RogMoviesDownloadProvider(Provider):
    id = "rogmovies_dl"
    name = "RogMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://rogmovies.com", data, "RogMovies")


class MultiMoviesDownloadProvider(Provider):
    id = "multimovies_dl"
    name = "MultiMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://multimovies.store", data, "MultiMovies")


class UhdMoviesDownloadProvider(Provider):
    id = "uhdmovies_dl"
    name = "UhdMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://uhdmovies.world", data, "UhdMovies")


class MoviesModDownloadProvider(Provider):
    id = "moviesmod_dl"
    name = "MoviesMod"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://moviesmod.day", data, "MoviesMod")


class TopMoviesDownloadProvider(Provider):
    id = "topmovies_dl"
    name = "TopMovies"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://topmovies.rent", data, "TopMovies")


class BollyflixDownloadProvider(Provider):
    id = "bollyflix_dl"
    name = "Bollyflix"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://bollyflix.land", data, "Bollyflix")


class CineMacityDownloadProvider(Provider):
    id = "cinemacity_dl"
    name = "CineMacity"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ProviderResult:
        return await _scrape_download_links("https://cinemacity.pro", data, "CineMacity")
