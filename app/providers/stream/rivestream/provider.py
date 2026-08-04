"""
providers/stream/rivestream/provider.py

Flow:
  1. Fetch VideoProviderServices list.
  2. Scrape homepage → find _app script → extract key array.
  3. Derive secretKey via Cloudflare Worker.
  4. Fan out across services → parse streams.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from app.providers.base import Provider
from app.schemas.provider import LinkData, ProviderResult
from app.clients.http import safe_get, UA
from app.utils.retry import retry
from app.providers.stream.rivestream.constants import RIVESTREAM_API, WORKER_URL
from app.providers.stream.rivestream.parser import (
    extract_app_script_path,
    extract_key_list,
    parse_sources,
)


class RiveStreamProvider(Provider):
    id = "rivestream"
    name = "RiveStream"

    async def invoke(self, data: LinkData) -> ProviderResult:
        result = ProviderResult()
        if not data.id:
            return result

        try:
            headers = {"User-Agent": UA}

            # 1. Service list
            svc_url = (
                f"{RIVESTREAM_API}/api/backendfetch"
                f"?requestID=VideoProviderServices&secretKey=rive"
            )
            svc_resp = await retry(3, lambda: safe_get(svc_url, headers=headers))
            if not svc_resp:
                return result
            svc_json = svc_resp.json()
            services: list[str] = svc_json.get("data", []) if svc_json else []

            # 2. Scrape homepage for _app script
            home = await retry(3, lambda: safe_get(RIVESTREAM_API, headers=headers, timeout=20))
            if not home or not home.text:
                return result
            app_path = extract_app_script_path(home.text)
            if not app_path:
                return result

            # 3. Extract key array from JS
            js_url = f"{RIVESTREAM_API}{app_path}"
            js_resp = await retry(3, lambda: safe_get(js_url, headers=headers))
            if not js_resp or not js_resp.text:
                return result
            key_list = extract_key_list(js_resp.text)
            c_list = ",".join(quote(k, safe="") for k in key_list)

            # 4. Derive secretKey via worker
            worker_url = f"{WORKER_URL}?input={quote(str(data.id))}&cList={c_list}"
            sk_resp = await retry(3, lambda: safe_get(worker_url, headers=headers))
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
                    r = await retry(3, lambda: safe_get(stream_url, headers=headers, timeout=10))
                    if not r:
                        return
                    j = r.json()
                    if not j:
                        return
                    sources = j.get("data", {}).get("sources", [])
                    streams = parse_sources(sources, "RiveStream")
                    result.streams.extend(streams)
                except Exception:
                    pass

            await asyncio.gather(*[fetch_service(s) for s in services])

        except Exception:
            pass

        return result
