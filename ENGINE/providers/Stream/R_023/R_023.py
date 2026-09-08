"""
ENGINE/providers/Stream/R_023/R_023.py — MultiMovies (Indian multi-audio)

DooPlay WordPress: build slug URL -> player options -> admin-ajax -> embed_url -> stream.
CF-gated + requires WARP for clearance reuse.

Type: mp4 | m3u8
Flow:
  1. solve_cloudflare (WARP) on content URL -> parse player options + capture cookies
  2. POST wp-admin/admin-ajax.php with CF cookies -> embed_url per option
  3. cf_get embed_url -> extract_media_urls -> Stream objects

Ported from Streamplay's MultiMoviesProvider.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.flaresolverr import solve_cloudflare
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls
from ENGINE.tools.warp import warp_proxy


def _create_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


class R023Provider(Provider):
    id = "R-023"
    name = "MultiMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("multimovies")
            if not api:
                return result
            slug = _create_slug(data.title or "")
            if not slug:
                return result

            url = (
                f"{api}/movies/{slug}"
                if data.season is None
                else f"{api}/episodes/{slug}-{data.season}x{data.episode}"
            )

            # Need cookies for the DooPlay ajax call — use solve_cloudflare directly
            html, cookies, _ua = await solve_cloudflare(url, use_warp=True)
            if not html or re.search(r"just a moment", html, re.I):
                return result

            soup = parse(html)
            options: list[dict] = []
            for li in soup.select("ul#playeroptionsul li"):
                options.append({
                    "post": li.get("data-post") or "",
                    "nume": li.get("data-nume") or "",
                    "type": li.get("data-type") or "",
                })

            client = await get_client()
            for opt in options:
                if re.search(r"trailer", opt["nume"], re.I):
                    continue
                try:
                    body = urlencode({
                        "action": "doo_player_ajax",
                        "post": opt["post"],
                        "nume": opt["nume"],
                        "type": opt["type"],
                    })
                    post_res = await client.post(
                        f"{api}/wp-admin/admin-ajax.php",
                        content=body.encode(),
                        headers={
                            "User-Agent": UA,
                            "Referer": url,
                            "X-Requested-With": "XMLHttpRequest",
                            "Content-Type": "application/x-www-form-urlencoded",
                            **({
                                "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())
                            } if cookies else {}),
                        },
                        timeout=15,
                    )
                    j = post_res.json()
                    embed_url = (j.get("embed_url") or "").strip().strip('"')
                    if not embed_url.startswith("http") or re.search(r"youtube", embed_url, re.I):
                        continue
                    embed_html = await cf_get(embed_url, referer=url)
                    if embed_html:
                        for media_url in extract_media_urls(embed_html):
                            result.streams.append(Stream(
                                url=media_url,
                                type="m3u8" if ".m3u8" in media_url else "mp4",
                                server="R-023 MultiMovies",
                            ))
                except Exception:
                    continue
        except Exception:
            pass
        return result
