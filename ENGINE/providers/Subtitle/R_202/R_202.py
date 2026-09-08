"""
ENGINE/providers/Subtitle/R_202/R_202.py — Addic7ed

Type: scraper
Flow:
  1. Search Addic7ed for the title
  2. Pick the first matching result
  3. For TV: scrape the episode page (series/season/episode)
     For movies: scrape the movie show page
  4. Return completed English subtitle download URLs
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.http import UA
from ENGINE.tools.scraper import fetch_soup

_BASE = "https://www.addic7ed.com"

_HEADERS = {
    "User-Agent": UA,
    "Referer": _BASE + "/",
}


class R202Provider(Provider):
    id = "R-202"
    name = "Addic7ed"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search
            search_url = f"{_BASE}/search.php?q={data.title.replace(' ', '+')}"
            soup = await fetch_soup(search_url, referer=_BASE + "/", extra_headers=_HEADERS)
            if soup is None:
                return result

            # Step 2 — pick first result
            show_path: str | None = None
            for a in soup.select(".a1 a"):
                href = a.get("href", "")
                if href:
                    show_path = href
                    break

            if not show_path:
                return result

            # Step 3 — build subtitle page URL
            if data.type != "movie" and data.season and data.episode:
                # TV episode path: /serie/<show_name>/<season>/<episode>
                show_name = show_path.strip("/").split("/")[-1]
                sub_url = f"{_BASE}/serie/{show_name}/{data.season}/{data.episode}"
            else:
                sub_url = f"{_BASE}{show_path}" if show_path.startswith("/") else f"{_BASE}/{show_path}"

            msoup = await fetch_soup(sub_url, referer=search_url, extra_headers=_HEADERS)
            if msoup is None:
                return result

            # Step 4 — scrape subtitle table
            for row in msoup.select(".table tbody tr"):
                cols = row.select("td")
                if not cols:
                    continue
                lang = cols[0].get_text(strip=True).lower() if cols else ""
                if "english" not in lang:
                    continue
                dl_link = row.select_one('a[href*="/download/"]')
                if not dl_link:
                    continue
                dl_href = dl_link.get("href", "")
                version = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                if not dl_href:
                    continue
                result.subtitles.append(Subtitle(
                    url=f"{_BASE}{dl_href}" if dl_href.startswith("/") else dl_href,
                    language="en",
                    label=version or "Addic7ed",
                    format="srt",
                ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
