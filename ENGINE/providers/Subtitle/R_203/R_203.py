"""
ENGINE/providers/Subtitle/R_203/R_203.py — TVsubtitles.net

Type: scraper
Flow:
  1. Search TVsubtitles.net for the title
  2. Pick the first matching result (movie or TV show page)
  3. Scrape the page for English subtitle download links
  4. Return download URLs as Subtitle objects
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import UA
from ENGINE.tools.scraper import fetch_soup

_BASE = "https://www.tvsubtitles.net"

_HEADERS = {
    "User-Agent": UA,
    "Referer": _BASE + "/",
}


class R203Provider(Provider):
    id = "R-203"
    name = "TVsubtitles"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search
            search_url = f"{_BASE}/search.php?q={data.title.replace(' ', '+')}"
            soup = await fetch_soup(search_url, referer=_BASE + "/", extra_headers=_HEADERS)
            if soup is None:
                return result

            # Step 2 — pick first result
            movie_path: str | None = None
            for a in soup.select(".box .title a"):
                href = a.get("href", "")
                if href:
                    movie_path = href
                    break

            if not movie_path:
                return result

            # Step 3 — scrape subtitle list page
            movie_url = f"{_BASE}{movie_path}" if movie_path.startswith("/") else f"{_BASE}/{movie_path}"
            msoup = await fetch_soup(movie_url, referer=search_url, extra_headers=_HEADERS)
            if msoup is None:
                return result

            # Step 4 — find English subtitle links
            for a in msoup.select(".box .list a"):
                img = a.find("img")
                lang = img.get("alt", "").lower() if img else ""
                if "english" not in lang and lang != "en":
                    continue
                dl_path = a.get("href", "")
                quality = a.get_text(strip=True)
                if not dl_path:
                    continue
                result.subtitles.append(Subtitle(
                    url=f"{_BASE}{dl_path}" if dl_path.startswith("/") else dl_path,
                    language="en",
                    label=quality or "TVsubtitles",
                    format="srt",
                ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
