"""
ENGINE/providers/Stream/R-023/R_023.py — MultiMovies (Indian multi-audio)

DooPlay WordPress: build slug URL -> player options -> admin-ajax -> embed_url -> stream.
CF-gated + requires WARP for clearance reuse.
Ported from Streamplay's MultiMoviesProvider.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.flaresolverr import solve_cloudflare
from ENGINE.tools.warp import warp_proxy


def _create_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


async def _cf_get(url: str) -> tuple[str | None, dict]:
    """Return (html, cookies)."""
    try:
        client = await get_client()
        r = await client.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code < 400 and not re.search(r"just a moment", r.text, re.I):
            return r.text, {}
    except Exception:
        pass
    html, cookies, ua = await solve_cloudflare(url, use_warp=True)
    return html, cookies


async def _load_extractor(source: str, referer: str) -> list[Stream]:
    html, _ = await _cf_get(source)
    if not html:
        return []
    streams: list[Stream] = []
    for m in re.finditer(r'(https?://[^"\'<>\s]+\.(?:m3u8|mp4)[^"\'<>\s]*)', html):
        url = m.group(1)
        streams.append(Stream(url=url, type="m3u8" if ".m3u8" in url else "mp4", server="R-023 MultiMovies"))
    return streams


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

            html, cookies = await _cf_get(url)
            if not html or re.search(r"just a moment", html, re.I):
                return result

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
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
                            **({"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else {}),
                        },
                        timeout=15,
                    )
                    j = post_res.json()
                    embed_url = (j.get("embed_url") or "").strip().strip('"')
                    if not embed_url.startswith("http") or re.search(r"youtube", embed_url, re.I):
                        continue
                    for s in await _load_extractor(embed_url, url):
                        result.streams.append(s)
                except Exception:
                    continue
        except Exception:
            pass
        return result
