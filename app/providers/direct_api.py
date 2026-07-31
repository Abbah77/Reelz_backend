"""
Direct-API providers (no Cloudflare, no FlareSolverr needed):
  - AllMovieLand
  - 2Embed (iframe)
  - VidFast
  - VidRock
  - Hexa
  - Xpass
  - Vaplayer
  - DahmerMovies
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

from app.models import LinkData, ExtractorResult, Stream, Subtitle
from app.providers.base import Provider
from app.utils.http import app, safe_get, UA
from app.utils.encdec import enc_dec_get, enc_dec_post


# ══════════════════════════════════════════════════════════════════
# AllMovieLand
# ══════════════════════════════════════════════════════════════════

_DOMAIN_RE = re.compile(r"const AwsIndStreamDomain\s*=\s*['\"]([^'\"]+)['\"]")


class AllMovieLandProvider(Provider):
    id = "allmovieland"
    name = "AllMovieLand"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        imdb = data.imdb_id
        if not imdb:
            return result

        try:
            season = data.season
            episode = data.episode

            # 1. Find AwsIndStreamDomain from player.js
            pr = await safe_get("https://allmovieland.link/player.js?v=60%20128")
            m = _DOMAIN_RE.search(pr.text) if pr else None
            if not m:
                return result
            host = m.group(1)

            # 2. GET the play page
            res = await safe_get(f"{host}/play/{imdb}", referer="https://allmovieland.io/")
            if not res or not res.is_successful:
                return result

            # 3. Extract inline JSON config
            script_data = ""
            soup = res.document
            for sc in soup.find_all("script"):
                txt = sc.get_text() or ""
                if "playlist" in txt and not script_data:
                    script_data = txt
                    break
            if not script_data:
                return result

            start = script_data.find("{")
            if start < 0:
                return result

            # Brace-match
            depth, end, in_str, esc = 0, -1, False, False
            for i in range(start, len(script_data)):
                ch = script_data[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end < 0:
                return result

            import json as _json
            try:
                cfg = _json.loads(script_data[start:end + 1])
            except Exception:
                return result

            file_path = cfg.get("file")
            key = cfg.get("key", "")
            if not file_path:
                return result

            json_url = file_path if file_path.startswith("http") else host + file_path
            headers = {"X-CSRF-TOKEN": str(key)}

            srv_res = await safe_get(json_url, headers=headers, referer="https://allmovieland.io/")
            if not srv_res or not srv_res.is_successful:
                return result
            server_list = srv_res.json()
            if not isinstance(server_list, list):
                return result

            # 4. Collect server entries
            servers: list[tuple[str, str]] = []
            if season is None:
                servers = [(s["file"], s.get("title", "")) for s in server_list if s.get("file")]
            else:
                season_obj = next((s for s in server_list if s.get("id") == str(season)), None)
                ep_obj = next(
                    (f for f in (season_obj.get("folder", []) if season_obj else [])
                     if f.get("episode") == str(episode)),
                    None,
                )
                servers = [
                    (f["file"], f.get("title", ""))
                    for f in (ep_obj.get("folder", []) if ep_obj else [])
                    if f.get("file")
                ]

            async def fetch_server(srv: str, lang: str) -> None:
                try:
                    r = await app.post(
                        f"{host}/playlist/{srv}.txt",
                        referer="https://allmovieland.io/",
                        headers=headers,
                    )
                    if not r or not r.is_successful:
                        return
                    playlist_url = r.text.strip()
                    if not playlist_url.startswith("http"):
                        return
                    result.streams.append(Stream(
                        server=f"AllMovieLand-{lang}" if lang else "AllMovieLand",
                        link=playlist_url,
                        type="m3u8" if ".m3u8" in playlist_url else "mp4",
                        headers={
                            "User-Agent": UA,
                            "Referer": "https://allmovieland.io",
                            "Origin": "https://allmovieland.io",
                        },
                    ))
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(s, l) for s, l in servers])

        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# 2Embed (iframe)
# ══════════════════════════════════════════════════════════════════

class TwoEmbedProvider(Provider):
    id = "2embed"
    name = "2Embed"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if data.is_anime or not data.imdb_id:
            return result
        try:
            base = "https://www.2embed.cc"
            if data.season is None:
                url = f"{base}/embed/{data.imdb_id}"
            else:
                url = f"{base}/embedtv/{data.imdb_id}?s={data.season}&e={data.episode}"
            res = await app.get(url, headers={"Referer": url})
            soup = res.document
            iframe = soup.find("iframe", id="iframesrc")
            src = iframe.get("data-src") if iframe else None
            if src:
                result.streams.append(Stream(server="2Embed (Iframe)", link=src, type="iframe"))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# VidFast
# ══════════════════════════════════════════════════════════════════

_ENC_TOKEN_RE = re.compile(r'\\"en\\":\\"(.*?)\\"')


class VidFastProvider(Provider):
    id = "vidfast"
    name = "VidFast"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result
        version = "1"
        base = "https://vidfast.pro"
        try:
            if data.season is None:
                req_url = f"{base}/movie/{data.id}"
            else:
                req_url = f"{base}/tv/{data.id}/{data.season}/{data.episode}"

            base_headers = {
                "User-Agent": UA,
                "Referer": f"{base}/",
                "X-Requested-With": "XMLHttpRequest",
            }

            page = await app.get(req_url, headers=base_headers)
            m = _ENC_TOKEN_RE.search(page.text)
            if not m:
                return result
            encoded_text = m.group(1)

            enc_json = await enc_dec_get(f"enc-vidfast?text={encoded_text}&version={version}")
            meta = (enc_json or {}).get("result")
            if not meta or not meta.get("servers") or not meta.get("stream") or not meta.get("token"):
                return result

            servers_url = meta["servers"]
            stream_base = meta["stream"]
            token = meta["token"]
            post_headers = {**base_headers, "X-CSRF-Token": token}

            # Encrypted server list
            srv_resp = await app.post(servers_url, headers=post_headers, content_type="application/json")
            servers_encrypted = srv_resp.text if srv_resp and srv_resp.is_successful else None
            if not servers_encrypted:
                return result

            dec_root = await enc_dec_post("dec-vidfast", {"text": servers_encrypted, "version": version})
            server_list: list = (dec_root or {}).get("result", [])
            if not server_list:
                return result

            async def resolve_server(server: dict, index: int) -> None:
                name = server.get("name", f"Server {index+1}")
                if not server.get("data"):
                    return
                try:
                    stream_url = f"{stream_base}/{server['data']}"
                    enc_resp = await app.post(stream_url, headers=post_headers, content_type="application/json")
                    enc_body = enc_resp.text if enc_resp and enc_resp.is_successful else None
                    if not enc_body:
                        return
                    dec2 = await enc_dec_post("dec-vidfast", {"text": enc_body, "version": version})
                    final_url = (dec2 or {}).get("result", {}).get("url")
                    if not final_url:
                        return
                    result.streams.append(Stream(
                        server=f"VidFast [{name}]",
                        link=final_url,
                        type="m3u8" if ".m3u8" in final_url else "mp4",
                        quality="1080p",
                        headers={"Referer": f"{base}/"},
                    ))
                    for track in (dec2 or {}).get("result", {}).get("tracks", []):
                        if track.get("file") and track.get("label"):
                            result.subtitles.append(Subtitle(language=track["label"], url=track["file"]))
                except Exception:
                    pass

            await asyncio.gather(*[resolve_server(s, i) for i, s in enumerate(server_list)])
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# VidRock
# ══════════════════════════════════════════════════════════════════

class VidRockProvider(Provider):
    id = "vidrock"
    name = "VidRock"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result
        try:
            base = "https://vidrock.pro"
            if data.season is None:
                url = f"{base}/movie/{data.id}"
            else:
                url = f"{base}/tv/{data.id}/{data.season}/{data.episode}"
            res = await app.get(url, headers={"Referer": f"{base}/"})
            # Extract from page (similar pattern to VidFast)
            m = _ENC_TOKEN_RE.search(res.text)
            if not m:
                return result
            encoded = m.group(1)
            dec = await enc_dec_get(f"dec-vidrock?text={encoded}")
            final = (dec or {}).get("result", {})
            if isinstance(final, str) and final.startswith("http"):
                result.streams.append(Stream(
                    server="VidRock",
                    link=final,
                    type="m3u8" if ".m3u8" in final else "mp4",
                    headers={"Referer": f"{base}/"},
                ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# Hexa
# ══════════════════════════════════════════════════════════════════

class HexaProvider(Provider):
    id = "hexa"
    name = "Hexa"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result
        try:
            base = "https://hexa.watch"
            if data.season is None:
                url = f"{base}/watch?id={data.id}"
            else:
                url = f"{base}/watch?id={data.id}&s={data.season}&e={data.episode}"
            res = await app.get(url, headers={"Referer": f"{base}/"})
            # Hexa embeds encoded token in page
            m = re.search(r'data-key="([^"]+)"', res.text)
            if not m:
                return result
            token = m.group(1)
            dec = await enc_dec_get(f"dec-hexa?text={quote(token, safe='')}")
            stream_url = (dec or {}).get("result", "")
            if stream_url and stream_url.startswith("http"):
                result.streams.append(Stream(
                    server="Hexa",
                    link=stream_url,
                    type="m3u8" if ".m3u8" in stream_url else "mp4",
                    headers={"Referer": f"{base}/"},
                ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# Xpass
# ══════════════════════════════════════════════════════════════════

class XpassProvider(Provider):
    id = "xpass"
    name = "Xpass"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.imdb_id:
            return result
        try:
            base = "https://xpau.se"
            if data.season is None:
                url = f"{base}/movie/{data.imdb_id}"
            else:
                url = f"{base}/show/{data.imdb_id}/{data.season}/{data.episode}"
            res = await app.get(url, headers={"Referer": f"{base}/"})
            soup = res.document
            # Look for direct m3u8/mp4 sources
            for sc in soup.find_all("script"):
                txt = sc.get_text() or ""
                m = re.search(r'["\']([^"\']*\.m3u8[^"\']*)["\']', txt)
                if m:
                    result.streams.append(Stream(
                        server="Xpass",
                        link=m.group(1),
                        type="m3u8",
                        headers={"Referer": base},
                    ))
                    break
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# Vaplayer
# ══════════════════════════════════════════════════════════════════

class VaplayerProvider(Provider):
    id = "vaplayer"
    name = "Vaplayer"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result
        try:
            base = "https://vaplayer.xyz"
            if data.season is None:
                url = f"{base}/embed/movie?tmdb={data.id}"
            else:
                url = f"{base}/embed/tv?tmdb={data.id}&season={data.season}&episode={data.episode}"
            res = await app.get(url, headers={"Referer": f"{base}/"})
            for m in re.finditer(r'["\']([^"\']*\.m3u8[^"\']*)["\']', res.text):
                link = m.group(1)
                if "http" in link:
                    result.streams.append(Stream(
                        server="Vaplayer",
                        link=link,
                        type="m3u8",
                        headers={"Referer": base},
                    ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# DahmerMovies
# ══════════════════════════════════════════════════════════════════

class DahmerMoviesProvider(Provider):
    id = "dahmermovies"
    name = "DahmerMovies"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result
        try:
            base = "https://dahmermovies.com"
            if data.season is None:
                api_url = f"{base}/api/movie/{data.id}"
            else:
                api_url = f"{base}/api/tv/{data.id}/{data.season}/{data.episode}"
            res = await app.get(api_url, headers={"Referer": f"{base}/"})
            j = res.json()
            if not j:
                return result
            for src in (j.get("sources") or []):
                url = src.get("url") or src.get("file") or ""
                if url.startswith("http"):
                    result.streams.append(Stream(
                        server="DahmerMovies",
                        link=url,
                        type="m3u8" if ".m3u8" in url else "mp4",
                        quality=src.get("quality"),
                        headers={"Referer": base},
                    ))
        except Exception:
            pass
        return result
