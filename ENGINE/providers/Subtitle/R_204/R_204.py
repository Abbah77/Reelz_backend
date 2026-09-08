"""
ENGINE/providers/Subtitle/R_204/R_204.py — YIFY Subtitles

Type: scraper
Flow:
  1. Search YIFY Subtitles for the title
  2. Pick the first matching movie result
  3. Scrape the movie page for English subtitle entries
  4. Return download URLs as Subtitle objects
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import UA
from ENGINE.tools.scraper import fetch_soup

_BASE = "https://www.yifysubtitles.com"

_HEADERS = {
    "User-Agent": UA,
    "Referer": _BASE + "/",
}


class R204Provider(Provider):
    id = "R-204"
    name = "YIFY"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search
            search_url = f"{_BASE}/search?q={data.title.replace(' ', '+')}"
            soup = await fetch_soup(search_url, referer=_BASE + "/", extra_headers=_HEADERS)
            if soup is None:
                return result

            # Step 2 — pick first movie match
            movie_slug: str | None = None
            for elem in soup.select(".movie-item a"):
                href = elem.get("href", "")
                if href:
                    movie_slug = href
                    break

            if not movie_slug:
                return result

            # Step 3 — scrape movie subtitle page
            movie_url = f"{_BASE}/movies/{movie_slug.strip('/')}" if not movie_slug.startswith("http") else movie_slug
            msoup = await fetch_soup(movie_url, referer=search_url, extra_headers=_HEADERS)
            if msoup is None:
                return result

            # Step 4 — find English subtitles in the table
            for row in msoup.select(".subtitle-table tbody tr"):
                lang_cell = row.select_one(".language")
                lang = lang_cell.get_text(strip=True).lower() if lang_cell else ""
                if "english" not in lang:
                    continue
                dl_cell = row.select_one(".download")
                if dl_cell is None:
                    continue
                dl_id = dl_cell.get("data-id", "")
                quality_cell = row.select_one(".quality")
                quality = quality_cell.get_text(strip=True) if quality_cell else ""
                if not dl_id:
                    # fallback: look for an <a> tag inside the row
                    a = row.select_one('a[href*="/download"]')
                    if a:
                        href = a.get("href", "")
                        result.subtitles.append(Subtitle(
                            url=f"{_BASE}{href}" if href.startswith("/") else href,
                            language="en",
                            label=quality or "YIFY",
                            format="srt",
                        ))
                    continue
                result.subtitles.append(Subtitle(
                    url=f"{_BASE}/download/{dl_id}",
                    language="en",
                    label=quality or "YIFY",
                    format="srt",
                ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
