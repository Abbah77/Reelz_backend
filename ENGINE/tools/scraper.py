"""
ENGINE/tools/scraper.py — HTML scraping helpers plugin.

Wraps BeautifulSoup for providers that need to parse HTML pages.
Always use this instead of importing bs4 directly in a provider,
so the dependency is centralised.

Usage:
    from ENGINE.tools.scraper import parse, find_links, fetch_soup, fetch_soup_cf, cf_get, extract_media_urls

    soup = parse(html_string)
    links = find_links(soup, href_pattern="episode")

    # CF-aware fetch — tries plain HTTP first, falls back to FlareSolverr:
    html = await cf_get("https://example.com/page")

    # Extract all .m3u8 / .mp4 URLs from an HTML string:
    urls = extract_media_urls(html)
"""
from __future__ import annotations

import re
from typing import Optional

from ENGINE.tools.http import get_client, UA


def parse(html: str):
    """Parse an HTML string into a BeautifulSoup object."""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def find_links(soup, *, href_pattern: Optional[str] = None, text_pattern: Optional[str] = None) -> list[str]:
    """
    Find <a href> values optionally filtered by href regex and/or link text regex.
    Returns a deduplicated list of href strings.
    """
    seen: set[str] = set()
    results: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if href_pattern and not re.search(href_pattern, href, re.I):
            continue
        if text_pattern and not re.search(text_pattern, a.get_text(), re.I):
            continue
        if href not in seen:
            seen.add(href)
            results.append(href)
    return results


async def fetch_soup(url: str, *, referer: str = "", extra_headers: Optional[dict] = None):
    """
    GET a URL and return a BeautifulSoup object, or None on error.
    """
    from bs4 import BeautifulSoup
    try:
        client = await get_client()
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        r = await client.get(url, headers=headers, timeout=15.0)
        if r.status_code >= 400:
            return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None


async def fetch_soup_cf(url: str, *, use_warp: bool = False):
    """
    GET a Cloudflare-protected URL via FlareSolverr and return a BeautifulSoup
    object, or None on failure / FlareSolverr not configured.

    Use this instead of fetch_soup() for any site that sits behind a
    Cloudflare JS challenge (e.g. Subscene, YIFY).

    Usage:
        from ENGINE.tools.scraper import fetch_soup_cf
        soup = await fetch_soup_cf("https://subscene.com/subtitles/search?query=Avatar")
        if soup is None:
            return result  # FlareSolverr not available or request failed

    Args:
        url:       Full URL to fetch.
        use_warp:  Route through WARP-backed FlareSolverr (WARP_FLARESOLVERR_URL).
                   Default False uses the standard FLARESOLVERR_URL.
    """
    from bs4 import BeautifulSoup
    from ENGINE.tools.flaresolverr import solve_cloudflare
    try:
        html, _cookies, _ua = await solve_cloudflare(url, use_warp=use_warp)
        if not html:
            return None
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return None


_CF_CHALLENGE_RE = re.compile(r"just a moment|cf-browser-verification|__cf_chl|checking your browser", re.I)
_MEDIA_URL_RE = re.compile(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)')


async def cf_get(url: str, *, referer: str = "", extra_headers: Optional[dict] = None, use_warp: bool = False) -> Optional[str]:
    """
    CF-aware GET: tries a plain HTTP request first; if the response looks like
    a Cloudflare JS challenge, falls back to FlareSolverr.

    Returns the raw HTML string, or None on failure.

    This is the universal replacement for private _cf_get() helpers that were
    copy-pasted across R_018–R_025. Any provider that needs to fetch a
    Cloudflare-protected page should use this instead.

    Usage:
        from ENGINE.tools.scraper import cf_get

        html = await cf_get("https://example.com/page", referer="https://example.com")
        if html is None:
            return result  # site unreachable

    Args:
        url:           Full URL to fetch.
        referer:       Optional Referer header value.
        extra_headers: Optional dict of additional request headers.
        use_warp:      Route FlareSolverr through WARP (WARP_FLARESOLVERR_URL).
    """
    from ENGINE.tools.flaresolverr import solve_cloudflare
    try:
        client = await get_client()
        headers: dict = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        r = await client.get(url, headers=headers, timeout=20)
        if r.status_code < 400 and not _CF_CHALLENGE_RE.search(r.text):
            return r.text
    except Exception:
        pass
    # FlareSolverr fallback
    html, _cookies, _ua = await solve_cloudflare(url, use_warp=use_warp)
    return html


def extract_media_urls(html: str) -> list[str]:
    """
    Scan an HTML string and return all unique .m3u8 and .mp4 URLs found.

    This is the universal replacement for the regex scan that was copy-pasted
    inside _load_extractor() across R_018–R_025. Returns plain URL strings;
    the provider is responsible for building Stream objects from them.

    Usage:
        from ENGINE.tools.scraper import cf_get, extract_media_urls

        html = await cf_get(source_url)
        if html:
            for url in extract_media_urls(html):
                result.streams.append(Stream(url=url, type="m3u8" if ".m3u8" in url else "mp4", server=...))
    """
    seen: set[str] = set()
    urls: list[str] = []
    for m in _MEDIA_URL_RE.finditer(html):
        u = m.group(1)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls
