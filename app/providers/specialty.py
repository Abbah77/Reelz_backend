"""
Specialty providers:
  - HDRezka  (rezka.ag — large multi-audio catalog)
  - KissKh   (Asian / Korean drama)
  - Vidlink   (enc-dec backed)
  - CastleTV (Indian multi-language, AES-128-CBC encrypted)
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Optional

from app.models import LinkData, ExtractorResult, Stream, Subtitle
from app.providers.base import Provider
from app.utils.http import app, safe_get, UA
from app.utils.encdec import enc_dec_get


# ══════════════════════════════════════════════════════════════════
# HDRezka
# ══════════════════════════════════════════════════════════════════

def _get_hdrezka_base() -> str:
    from app.config import get_settings
    return get_settings().hdrezka_base_url.rstrip("/")


def _clear_trash(data: str) -> str:
    if not data or data.startswith("["):
        return data
    alphabet = ["@", "#", "!", "^", "$"]
    combos: list[str] = []
    for c1 in alphabet:
        for c2 in alphabet:
            combos.append(c1 + c2)
    for c1 in alphabet:
        for c2 in alphabet:
            for c3 in alphabet:
                combos.append(c1 + c2 + c3)
    s = data.replace("#h", "").replace("//_//", "")
    for c in combos:
        s = s.replace(base64.b64encode(c.encode()).decode(), "")
    try:
        return base64.b64decode(s + "===").decode("utf-8")
    except Exception:
        return ""


def _parse_streams(decoded: str) -> list[dict]:
    out: list[dict] = []
    for part in decoded.split(","):
        m = re.match(r"^\s*\[([^\]]+)\](.+)$", part)
        if not m:
            continue
        quality_m = re.search(r"\d{3,4}", m.group(1))
        quality = quality_m.group(0) if quality_m else ""
        mirrors = [u.strip() for u in m.group(2).split(" or ") if u.strip().startswith("http")]
        url = next((u for u in mirrors if ".m3u8" in u.lower()), mirrors[0] if mirrors else "")
        if url:
            out.append({"quality": quality, "url": url})
    return out


def _parse_subs(raw: object) -> list[Subtitle]:
    if not isinstance(raw, str) or not raw:
        return []
    out: list[Subtitle] = []
    for m in re.finditer(r"\[([^\]]+)\](https?://[^,]+)", raw):
        out.append(Subtitle(language=m.group(1).strip(), url=m.group(2).strip()))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class HDRezkaProvider(Provider):
    id = "hdrezka"
    name = "HDRezka"
    kinds = ["movie", "series", "asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        base = _get_hdrezka_base()
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": f"{base}/",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            # 1. Search
            sr = await app.get(
                f"{base}/search/?do=search&subaction=search&q={re.sub(chr(32), '+', data.title)}",
                headers=hdrs,
            )
            if not sr or not sr.is_successful:
                return result

            # Parse search results
            soup = sr.document
            hits: list[dict] = []
            for el in soup.find_all(class_="b-content__inline_item"):
                a = el.find(class_="b-content__inline_item-link")
                link_el = a.find("a") if a else None
                if not link_el:
                    continue
                href = link_el.get("href", "")
                title = link_el.get_text(strip=True)
                id_m = re.search(r"/(\d+)-", href)
                year_m = re.search(r"-(\d{4})[.-]", href)
                hits.append({
                    "id": id_m.group(1) if id_m else "",
                    "url": href,
                    "title": title,
                    "year": int(year_m.group(1)) if year_m else None,
                    "is_series": "/series/" in href,
                })

            # Score best hit
            want_series = data.type == "tv"
            want_norm = _norm(data.title)
            best: Optional[dict] = None
            best_score = 0
            for hit in hits:
                if not hit["id"] or not hit["url"].endswith(".html"):
                    continue
                cand_norm = _norm(hit["title"])
                if want_norm not in cand_norm and cand_norm not in want_norm:
                    continue
                score = 2 if cand_norm == want_norm else 1
                if data.year and hit["year"] and hit["year"] == data.year:
                    score += 1
                if hit["is_series"] == want_series:
                    score += 1
                if score > best_score:
                    best_score = score
                    best = hit
            if not best:
                return result

            # 2. Load film page
            film_res = await app.get(best["url"], headers=hdrs)
            if not film_res or not film_res.is_successful:
                return result
            film_html = film_res.text

            # Extract film ID and default translator
            film_id_m = re.search(r"sof\.tv\.initCDN\w+Events\(\s*(\d+)", film_html)
            trans_id_m = re.search(r"translator_id\s*[=:]\s*(\d+)", film_html)
            if not film_id_m:
                return result
            film_id = film_id_m.group(1)
            trans_id = trans_id_m.group(1) if trans_id_m else "0"

            # Collect all translators
            trans_map: dict[str, str] = {trans_id: "Default"}
            ts = film_res.document
            for li in ts.find_all("li", attrs={"data-translator_id": True}):
                tid = li["data-translator_id"]
                tname = li.get_text(strip=True)
                trans_map[tid] = tname

            # 3. For each translator, call CDN ajax
            async def fetch_translator(tid: str, tname: str) -> None:
                try:
                    if data.season is None:
                        body = f"id={film_id}&translator_id={tid}&is_camrip=0&is_ads=0&is_director=0&action=get_movie"
                    else:
                        body = (
                            f"id={film_id}&translator_id={tid}&season={data.season}"
                            f"&episode={data.episode}&is_camrip=0&is_ads=0&is_director=0&action=get_stream"
                        )
                    import time
                    cdn_res = await app.post(
                        f"{base}/ajax/get_cdn_series/?t={int(time.time()*1000)}",
                        body=body,
                        headers={**hdrs, "X-Requested-With": "XMLHttpRequest",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                        content_type="application/x-www-form-urlencoded",
                    )
                    j = cdn_res.json() if cdn_res else None
                    if not j or not j.get("success") or not j.get("url"):
                        return
                    streams = _parse_streams(_clear_trash(str(j["url"])))
                    subs = _parse_subs(j.get("subtitle"))
                    for s in streams:
                        result.streams.append(Stream(
                            server=f"HDRezka [{tname}] {s['quality']}p",
                            link=s["url"],
                            type="m3u8" if ".m3u8" in s["url"] else "mp4",
                            quality=f"{s['quality']}p" if s["quality"] else None,
                            headers=hdrs,
                        ))
                    result.subtitles.extend(subs)
                except Exception:
                    pass

            await asyncio.gather(*[fetch_translator(tid, tname) for tid, tname in trans_map.items()])

        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# KissKh (Asian / Korean drama)
# ══════════════════════════════════════════════════════════════════

class KissKhProvider(Provider):
    id = "kisskh"
    name = "KissKh"
    kinds = ["asian"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if not data.title:
            return result

        base = "https://kisskh.co"
        try:
            # API search
            sr = await app.get(
                f"{base}/api/DramaList/Search?q={re.sub(chr(32), '+', data.title)}&type=0",
                headers={"Referer": base},
            )
            j = sr.json() if sr else None
            items = (j or {}).get("data") or (j if isinstance(j, list) else [])

            best_id: Optional[int] = None
            for item in items:
                if _norm(data.title) in _norm(item.get("title", "")):
                    best_id = item.get("id")
                    break
            if not best_id and items:
                best_id = items[0].get("id")
            if not best_id:
                return result

            if data.season is None:
                ep_id = 1
            else:
                # Get episode list
                ep_list_res = await app.get(
                    f"{base}/api/DramaList/Drama/{best_id}/Episodes",
                    headers={"Referer": base},
                )
                ep_list = (ep_list_res.json() if ep_list_res else None) or []
                ep_num = data.episode or 1
                ep_id = next(
                    (e.get("id") for e in ep_list if e.get("number") == ep_num),
                    ep_list[ep_num - 1].get("id") if ep_num <= len(ep_list) else None,
                )
            if not ep_id:
                return result

            # Get stream token
            token_res = await app.get(
                f"{base}/api/DramaList/GetToken?id={ep_id}",
                headers={"Referer": base},
            )
            tok_j = token_res.json() if token_res else None
            token = (tok_j or {}).get("token")
            if not token:
                return result

            result.streams.append(Stream(
                server="KissKh",
                link=f"{base}/api/DramaList/GetVideoByName?filename={token}&type=1",
                type="m3u8",
                headers={"Referer": base},
            ))

            # Subtitles
            sub_res = await app.get(
                f"{base}/api/DramaList/GetSubtitle?id={ep_id}",
                headers={"Referer": base},
            )
            sub_j = sub_res.json() if sub_res else None
            for sub in (sub_j if isinstance(sub_j, list) else []):
                url = sub.get("src") or sub.get("url") or ""
                lang = sub.get("label") or sub.get("lang") or "unknown"
                if url:
                    result.subtitles.append(Subtitle(language=lang, url=url))

        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# Vidlink (enc-dec backed, currently unreliable — kept for revival)
# ══════════════════════════════════════════════════════════════════

class VidlinkProvider(Provider):
    id = "vidlink"
    name = "Vidlink"

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        if data.is_anime or not data.id:
            return result
        try:
            base = "https://vidlink.pro"
            enc_json = await enc_dec_get(f"enc-vidlink?text={data.id}")
            enc_data = (enc_json or {}).get("result")
            if not enc_data:
                return result

            hdrs = {
                "User-Agent": UA,
                "Connection": "keep-alive",
                "Referer": f"{base}/",
                "Origin": base,
            }
            if data.season is None:
                api_url = f"{base}/api/b/movie/{enc_data}"
            else:
                api_url = f"{base}/api/b/tv/{enc_data}/{data.season}/{data.episode}"

            resp = await app.get(api_url, headers=hdrs, timeout=10)
            j = resp.json() if resp else None
            stream_data = (j or {}).get("stream")
            if not stream_data or not stream_data.get("playlist"):
                return result

            result.streams.append(Stream(
                server="Vidlink",
                link=stream_data["playlist"],
                type="m3u8",
                headers=hdrs,
            ))
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════
# CastleTV (Indian multi-language, AES-128-CBC)
# ══════════════════════════════════════════════════════════════════

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

_CASTLE_API = "https://api.hlowb.com"
_CASTLE_HDRS = {
    "Referer": _CASTLE_API,
    "User-Agent": "okhttp/4.9.2",
    "Content-Type": "application/json",
}
_CASTLE_STOP_WORDS = {
    "the", "a", "an", "of", "in", "on", "to", "and", "or", "my", "with",
    "season", "part", "world", "another", "life", "story", "movie", "film",
    "tv", "series", "saga", "arc", "chapter", "no", "wo", "ga", "isekai", "de",
}
_castle_key_cache: Optional[str] = None
_castle_key_exp: float = 0.0


async def _get_castle_security_key() -> Optional[str]:
    import time
    global _castle_key_cache, _castle_key_exp
    if _castle_key_cache and time.time() < _castle_key_exp:
        return _castle_key_cache
    try:
        r = await app.get(
            f"{_CASTLE_API}/v0.1/system/getSecurityKey/1?channel=IndiaA&clientType=1&lang=en-US",
            headers={"Referer": _CASTLE_API},
            timeout=12,
        )
        j = r.json() if r else None
        if j and j.get("code") == 200 and j.get("data"):
            _castle_key_cache = j["data"]
            _castle_key_exp = time.time() + 3600
            return _castle_key_cache
    except Exception:
        pass
    return None


def _castle_decrypt(enc_b64: str, key_b64: str) -> Optional[str]:
    try:
        from app.config import get_settings
        suffix = get_settings().castle_suffix
        raw = base64.b64decode(key_b64) + suffix.encode("ascii")
        key = raw[:16].ljust(16, b"\x00") if len(raw) < 16 else raw[:16]
        decipher = Cipher(algorithms.AES(key), modes.CBC(key), backend=default_backend()).decryptor()
        dec = decipher.update(base64.b64decode(enc_b64)) + decipher.finalize()
        # Strip PKCS7 padding
        pad = dec[-1]
        return dec[:-pad].decode("utf-8")
    except Exception:
        return None


def _castle_title_score(want: str, candidate: str) -> int:
    wn = _norm(want)
    cn = _norm(candidate)
    if wn == cn:
        return 3
    want_toks = {t for t in wn.split() if t not in _CASTLE_STOP_WORDS and t}
    cand_toks = {t for t in cn.split() if t not in _CASTLE_STOP_WORDS and t}
    if not want_toks or not cand_toks:
        return 0
    inter = len(want_toks & cand_toks)
    ratio = inter / min(len(want_toks), len(cand_toks))
    if ratio >= 0.999:
        return 2
    if ratio >= 0.6:
        return 1
    return 0


class CastleProvider(Provider):
    id = "castle"
    name = "CastleTV"
    kinds = ["movie", "series", "asian", "anime"]

    async def invoke(self, data: LinkData) -> ExtractorResult:
        result = ExtractorResult()
        from app.config import get_settings
        if not get_settings().castle_suffix:
            return result

        try:
            sec_key = await _get_castle_security_key()
            if not sec_key:
                return result

            # Search
            search_url = (
                f"{_CASTLE_API}/v0.1/content/search"
                f"?channel=IndiaA&clientType=1&lang=en-US"
                f"&keyword={re.sub(chr(32), '+', data.title)}&page=1&pageSize=20"
            )
            sr = await app.get(search_url, headers=_CASTLE_HDRS, timeout=12)
            j = sr.json() if sr else None
            raw_results = (j or {}).get("data") or []

            # Unwrap if encrypted
            if isinstance(raw_results, str):
                dec = _castle_decrypt(raw_results, sec_key)
                if dec:
                    import json
                    try:
                        raw_results = json.loads(dec) if dec else []
                    except Exception:
                        raw_results = []

            # Find best match
            want_medium = "anime" if data.is_anime else ("movie" if data.type == "movie" else "tv")
            best: Optional[dict] = None
            best_score = 0
            for row in raw_results:
                ts = _castle_title_score(data.title, row.get("name") or row.get("title") or "")
                if not ts:
                    continue
                medium_str = str(row.get("movieTypeName") or "").lower()
                ms = 0
                if want_medium == "anime" and "anime" in medium_str:
                    ms = 2
                elif want_medium == "movie" and re.search(r"movie|film", medium_str):
                    ms = 2
                elif want_medium == "tv" and re.search(r"tv|show|series|drama", medium_str):
                    ms = 2
                score = ts + ms
                if score > best_score:
                    best_score = score
                    best = row

            if not best:
                return result

            content_id = best.get("id") or best.get("contentId")
            if not content_id:
                return result

            # Get episode/movie streams
            if data.season is None:
                stream_url = (
                    f"{_CASTLE_API}/v0.1/content/getVideoUrl"
                    f"?channel=IndiaA&clientType=1&lang=en-US"
                    f"&contentId={content_id}&securityKey={sec_key}"
                )
            else:
                stream_url = (
                    f"{_CASTLE_API}/v0.1/content/getVideoUrl"
                    f"?channel=IndiaA&clientType=1&lang=en-US"
                    f"&contentId={content_id}&season={data.season}&episode={data.episode}"
                    f"&securityKey={sec_key}"
                )

            sv_res = await app.get(stream_url, headers=_CASTLE_HDRS, timeout=15)
            sv_j = sv_res.json() if sv_res else None
            sources = (sv_j or {}).get("data") or []

            if isinstance(sources, str):
                dec = _castle_decrypt(sources, sec_key)
                if dec:
                    import json
                    try:
                        sources = json.loads(dec)
                    except Exception:
                        sources = []

            for src in (sources if isinstance(sources, list) else []):
                url = src.get("url") or src.get("streamUrl") or ""
                lang = src.get("languageName") or src.get("language") or "Hindi"
                if url.startswith("http"):
                    result.streams.append(Stream(
                        server=f"CastleTV [{lang}]",
                        link=url,
                        type="m3u8" if ".m3u8" in url else "mp4",
                        headers={"Referer": _CASTLE_API},
                    ))

        except Exception:
            pass
        return result
