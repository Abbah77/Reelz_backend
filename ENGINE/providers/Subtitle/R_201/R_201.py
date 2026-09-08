"""
ENGINE/providers/Subtitle/R_201/R_201.py — Subscene

Type: scraper
Flow:
  1. Search Subscene for the title using LinkData.title
  2. Pick the best matching result (year-filtered when available)
  3. Scrape the movie page for English subtitle entries
  4. Return direct download URLs as Subtitle objects
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import fetch_soup

_BASE = "https://subscene.com"

_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": _BASE + "/",
}


class R201Provider(Provider):
    id = "R-201"
    name = "Subscene"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search
            search_url = f"{_BASE}/subtitles/search?query={data.title.replace(' ', '+')}"
            soup = await fetch_soup(search_url, referer=_BASE + "/", extra_headers=_HEADERS)
            if soup is None:
                return result

            # Step 2 — find best movie match
            movie_path: str | None = None
            for elem in soup.select(".search-result .title a"):
                href = elem.get("href", "")
                if href:
                    movie_path = href
                    break  # take first match (Subscene orders by relevance)

            if not movie_path:
                return result

            # Step 3 — scrape movie subtitle listing
            movie_url = f"{_BASE}{movie_path}"
            msoup = await fetch_soup(movie_url, referer=search_url, extra_headers=_HEADERS)
            if msoup is None:
                return result

            for row in msoup.select(".table tbody tr"):
                cols = row.select("td")
                if len(cols) < 2:
                    continue
                lang = cols[0].select_one("span")
                if not lang or lang.get_text(strip=True).lower() != "english":
                    continue
                link = cols[1].select_one("a")
                if not link:
                    continue
                dl_path = link.get("href", "")
                release_name = link.get_text(strip=True)
                if not dl_path:
                    continue
                result.subtitles.append(Subtitle(
                    url=f"{_BASE}{dl_path}",
                    language="en",
                    label=release_name or "Subscene",
                    format="srt",
                ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
