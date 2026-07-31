"""
RiveStream provider — Python port of src/extractors/rivestream.ts

Flow:
  1. Fetch the VideoProviderServices list.
  2. Scrape the _app script from homepage; extract `let c = [...]` key array.
  3. Derive secretKey via Cloudflare Worker (input=id, cList=keys).
  4. For each service, fetch stream JSON and push m3u8/mp4 links.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode, quote

from app.models import LinkData, ExtractorResult, Stream
from app.providers.base import Provider
from app.utils.http import safe_get, UA

RIVESTREAM_API = "https://www.rivestream.app"
_APP_RE = re.compile(r'src="(/[^"]*_app[^"]*)"')
_KEY_ARRAY_RE = re.compile(r'let\s+c\s*=\s*(\[[^\]]*\])')
_STRING_RE = re.compile(r'"([^"]+)"')


class RiveStreamProvider(Provider):
    id = "rivestream"
    name = "RiveStream"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.id:
            return result

        try:
            headers = {"User-Agent": UA}

            # 1. Service list
            svc_url = f"{RIVESTREAM_API}/api/backendfetch?requestID=VideoProviderServices&secretKey=rive"
            svc_resp = await _retry(3, lambda: safe_get(svc_url, headers=headers))
            if not svc_resp:
                return result
            svc_json = svc_resp.json()
            services: list[str] = svc_json.get("data", []) if svc_json else []

            # 2. Scrape homepage for _app script src
            home = await _retry(3, lambda: safe_get(RIVESTREAM_API, headers=headers, timeout=20))
            if not home or not home.text:
                return result
            m = _APP_RE.search(home.text)
            if not m:
                return result
            app_script_path = m.group(1)

            # 3. Fetch the JS and extract key array
            js_url = f"{RIVESTREAM_API}{app_script_path}"
            js_resp = await _retry(3, lambda: safe_get(js_url, headers=headers))
            if not js_resp or not js_resp.text:
                return result
            js = js_resp.text

            key_list: list[str] = []
            for km in _KEY_ARRAY_RE.finditer(js):
                arr_str = km.group(1)
                if len(arr_str) > 2:
                    key_list = _STRING_RE.findall(arr_str)
                    break

            c_list = ",".join(quote(k, safe="") for k in key_list)

            # 4. Derive secretKey via worker
            worker_url = (
                f"https://rivestream.supe2372.workers.dev/"
                f"?input={quote(str(data.id))}&cList={c_list}"
            )
            sk_resp = await _retry(3, lambda: safe_get(worker_url, headers=headers))
            secret_key = (sk_resp.text or "").strip() if sk_resp else None
            if not secret_key:
                return result

            # 5. Fan out across services
            async def fetch_service(source: str) -> None:
                try:
                    if data.season is None:
                        stream_url = (
                            f"{RIVESTREAM_API}/api/backendfetch"
                            f"?requestID=movieVideoProvider"
                            f"&id={data.id}&service={source}&secretKey={secret_key}"
                        )
                    else:
                        stream_url = (
                            f"{RIVESTREAM_API}/api/backendfetch"
                            f"?requestID=tvVideoProvider"
                            f"&id={data.id}&season={data.season}&episode={data.episode}"
                            f"&service={source}&secretKey={secret_key}"
                        )

                    r = await _retry(3, lambda: safe_get(stream_url, headers=headers, timeout=10))
                    if not r or not r.text:
                        return
                    j = r.json()
                    if not j:
                        return

                    sources = j.get("data", {}).get("sources", [])
                    if not isinstance(sources, list):
                        return

                    for src in sources:
                        src_name: str = src.get("source", "")
                        label = (
                            f"RiveStream {src_name}[{src.get('quality','')}]"
                            if "asiacloud" in src_name.lower()
                            else f"RiveStream {src_name}"
                        )
                        url: str = src.get("url", "")
                        if not url:
                            continue

                        try:
                            if "proxy?url=" in url:
                                from urllib.parse import unquote
                                fully_decoded = unquote(url)
                                encoded_url = fully_decoded.split("proxy?url=")[1].split("&headers=")[0]
                                decoded_url = unquote(encoded_url)

                                encoded_headers = fully_decoded.split("&headers=")[1] if "&headers=" in fully_decoded else ""
                                import json
                                try:
                                    headers_map: dict = json.loads(unquote(encoded_headers))
                                except Exception:
                                    headers_map = {}

                                video_headers = {
                                    k: v for k, v in headers_map.items()
                                    if k in ("Referer", "Origin", "User-Agent")
                                }
                                ext: str = "m3u8" if ".m3u8" in decoded_url.lower() else "mp4"
                                result.streams.append(Stream(
                                    server=label,
                                    link=decoded_url,
                                    type=ext,
                                    quality="1080p",
                                    headers=video_headers,
                                ))
                            else:
                                ext = "m3u8" if ".m3u8" in url.lower() else "mp4"
                                result.streams.append(Stream(
                                    server=f"{label} (VLC)",
                                    link=url,
                                    type=ext,
                                    quality="1080p",
                                ))
                        except Exception:
                            pass
                except Exception:
                    pass

            await asyncio.gather(*[fetch_service(s) for s in services])

        except Exception as exc:
            pass

        return result


async def _retry(times: int, fn):
    for i in range(times):
        try:
            r = await fn()
            if r is not None:
                return r
        except Exception:
            if i < times - 1:
                await asyncio.sleep(0.3 * (i + 1))
    return None
