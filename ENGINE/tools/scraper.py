"""
ENGINE/tools/scraper.py — HTML scraping helpers plugin.

Wraps BeautifulSoup for providers that need to parse HTML pages.
Always use this instead of importing bs4 directly in a provider,
so the dependency is centralised.

Usage:
    from ENGINE.tools.scraper import parse, parse_text, find_links

    soup = parse(html_string)
    text = parse_text(html_string, "div.entry-content")
    links = find_links(soup, href_pattern="episode")
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
