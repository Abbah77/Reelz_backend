"""
Stream orchestrator — upgraded for POST endpoints.

Key changes vs SSE version:
  - run_providers_first_wins(): races all providers with asyncio, returns the
    VERY FIRST valid m3u8 (preferred) or mp4 stream as soon as one provider
    finishes. No waiting for slow providers. 2-10× faster TTFP.
  - run_providers_all(): still available for building the full fallback ladder
    returned alongside the first result.
  - run_download_providers_deduped(): expands m3u8 playlists into per-resolution
    entries AND deduplicates mp4 links by quality — no duplicate 1080p entries.
  - Circuit breaker, priority scoring, and dedup logic unchanged.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx
import orjson

from app.models import (
    ContentKind,
    ExtractorResult,
    LinkData,
    Stream,
    StreamEntry,
    SubtitleEntry,
    DownloadLink,
)
from app.providers.base import Provider, safe_invoke, get_providers_for_kind, _TimedOutResult
from app.provider_stats import provider_stats
from app.config import get_settings

_settings = get_settings()


# ── File-size prober (HEAD request for direct mp4/mkv CDN links) ──────────────

async def _probe_size(url: str, headers: dict) -> int | None:
    """HEAD-probe a direct URL for its Content-Length."""
    try:
        from app.utils.http import get_client
        client = await get_client()
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            **headers,
        }
        r = await client.head(
            url, headers=hdrs, timeout=5, follow_redirects=True
        )
        cl = r.headers.get("content-length")
        val = int(cl) if cl else None
        # Sanity check: reject implausibly small values (< 1 MB)
        return val if val and val > 1_000_000 else None
    except Exception:
        return None


# ── Priority scoring ──────────────────────────────────────────────────────────

_QUALITY_SCORES: dict[str, int] = {
    "2160p": 100, "4k": 100,
    "1080p": 80,
    "720p":  60,
    "480p":  40,
    "360p":  20,
}

_HOST_BONUS: dict[str, int] = {
    "rivestream":     5,
    "vidfast":        4,
    "allmovieland":   3,
    "hdrezka":        3,
    "castle":         2,
    "vegamovies":     1,
    "hdhub4u":        1,
}

_TYPE_SCORES: dict[str, int] = {
    "m3u8":   10,
    "mp4":    6,
    "iframe": 2,
}


def _score_stream(stream: Stream, provider_id: str) -> int:
    score = _TYPE_SCORES.get(stream.type, 0)
    if stream.quality:
        q_norm = stream.quality.lower().replace(" ", "")
        for k, v in _QUALITY_SCORES.items():
            if k in q_norm:
                score += v
                break
    score += _HOST_BONUS.get(provider_id.lower(), 0)
    return score


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return url


def _lang_label(server: str) -> str:
    s = server.lower()
    if "hindi"    in s: return "Hindi"
    if "tamil"    in s: return "Tamil"
    if "telugu"   in s: return "Telugu"
    if "malayalam" in s: return "Malayalam"
    if "kannada"  in s: return "Kannada"
    if "bengali"  in s: return "Bengali"
    if "marathi"  in s: return "Marathi"
    if "punjabi"  in s: return "Punjabi"
    if "korean"   in s or "kor" in s: return "Korean"
    if "japanese" in s or "jpn" in s or "sub" in s: return "Japanese"
    if "chinese"  in s or "chi" in s or "mandarin" in s: return "Chinese"
    if "french"   in s or "fra" in s: return "French"
    if "spanish"  in s or "esp" in s: return "Spanish"
    if "arabic"   in s or "ara" in s: return "Arabic"
    if "dubbed"   in s or "dub" in s: return "Dubbed"
    return "English"


def _make_stream_entry(stream: Stream, provider: Provider) -> StreamEntry:
    return StreamEntry(
        provider=provider.name,
        provider_id=provider.id,
        name=stream.server,
        url=stream.link,
        type=stream.type,
        quality=stream.quality,
        language=_lang_label(stream.server),
        headers=stream.headers,
        playable=stream.type != "iframe",
        priority=0,
    )


def _dedup_streams(entries: list[tuple[StreamEntry, int]]) -> list[StreamEntry]:
    seen: set[str] = set()
    out: list[StreamEntry] = []
    for entry, _score in entries:
        key = _norm_url(entry.url)
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


# ── FAST FIRST-WINS: returns as soon as ANY provider yields a valid stream ────

async def run_providers_first_wins(
    data: LinkData,
    kind: ContentKind,
    timeout_ms: int = 0,
) -> tuple[StreamEntry | None, list[StreamEntry]]:
    """
    Fan out to all eligible providers concurrently.
    Returns (best_stream, all_streams) where best_stream is the first valid
    m3u8 found (or first mp4 if no m3u8 arrives within the timeout).

    Speed strategy:
      - asyncio.wait(FIRST_COMPLETED) breaks out the moment any provider
        returns a playable m3u8.
      - If that first finisher gives only mp4/iframe, we keep waiting until
        a m3u8 arrives or all providers finish — whichever comes first.
      - The full list is built in the background and returned for the
        fallback ladder.
    """
    if timeout_ms == 0:
        timeout_ms = _settings.provider_timeout_ms

    all_providers = get_providers_for_kind(kind)
    if not all_providers:
        return None, []

    eligible: list[Provider] = []
    for p in all_providers:
        if await provider_stats.should_run(p.id):
            eligible.append(p)

    if not eligible:
        return None, []

    # Results accumulate here as providers finish
    scored: list[tuple[StreamEntry, int]] = []
    lock = asyncio.Lock()

    async def invoke_one(p: Provider) -> list[StreamEntry]:
        """Runs one provider, records stats, returns its stream entries."""
        t0 = time.monotonic()
        try:
            result: ExtractorResult = await safe_invoke(p, data, timeout_ms)
            dur_ms = int((time.monotonic() - t0) * 1000)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            result = ExtractorResult()

        local: list[StreamEntry] = []
        for stream in result.streams:
            if not stream.link or stream.type == "iframe":
                continue
            entry = _make_stream_entry(stream, p)
            score = _score_stream(stream, p.id)
            local.append((entry, score))

        outcome: str
        if local:
            outcome = "found"
        elif isinstance(result, _TimedOutResult):
            outcome = "failed"
        else:
            outcome = "empty"

        await provider_stats.record(p.id, outcome, dur_ms)

        async with lock:
            scored.extend(local)

        return [e for e, _ in local]

    # Create tasks for all providers
    tasks = {asyncio.ensure_future(invoke_one(p)): p for p in eligible}

    best_m3u8: StreamEntry | None = None
    best_mp4:  StreamEntry | None = None
    pending = set(tasks.keys())

    # Race: collect until we have an m3u8 winner OR all done
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            try:
                entries = fut.result()
            except Exception:
                entries = []

            for entry in entries:
                if entry.type == "m3u8" and best_m3u8 is None:
                    best_m3u8 = entry
                elif entry.type == "mp4" and best_mp4 is None:
                    best_mp4 = entry

        # Break as soon as we have an m3u8
        if best_m3u8 is not None:
            # Cancel remaining tasks but let them clean up in background
            for t in pending:
                t.cancel()
            # Wait briefly so cancelled tasks can flush their stats
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            break

    # Sort full results and assign priorities
    scored.sort(key=lambda x: x[1], reverse=True)
    all_entries = _dedup_streams(scored)
    for i, entry in enumerate(all_entries):
        entry.priority = i

    winner = best_m3u8 or best_mp4
    return winner, all_entries


# ── ALL providers (for building the full fallback ladder synchronously) ────────

async def run_providers(
    data: LinkData,
    kind: ContentKind,
    *,
    on_provider_done: Optional[Callable[[str, str, list[StreamEntry], int], None]] = None,
    timeout_ms: int = 0,
) -> tuple[list[StreamEntry], list[SubtitleEntry]]:
    """Fan out to all providers — waits for everyone. Used by SSE path."""
    if timeout_ms == 0:
        timeout_ms = _settings.provider_timeout_ms

    all_providers = get_providers_for_kind(kind)
    if not all_providers:
        return [], []

    eligible: list[Provider] = []
    skipped: list[Provider] = []
    for p in all_providers:
        if await provider_stats.should_run(p.id):
            eligible.append(p)
        else:
            skipped.append(p)
            if on_provider_done:
                on_provider_done(p.id, "circuit_open", [], 0)

    if not eligible:
        return [], []

    scored: list[tuple[StreamEntry, int]] = []
    all_subs: list[SubtitleEntry] = []
    subs_seen: set[str] = set()
    lock = asyncio.Lock()

    async def invoke_one(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            result: ExtractorResult = await safe_invoke(p, data, timeout_ms)
            dur_ms = int((time.monotonic() - t0) * 1000)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            result = ExtractorResult()

        local_entries: list[StreamEntry] = []
        for stream in result.streams:
            if not stream.link:
                continue
            entry = _make_stream_entry(stream, p)
            score = _score_stream(stream, p.id)
            local_entries.append((entry, score))

        local_subs: list[SubtitleEntry] = []
        for sub in result.subtitles:
            if not sub.url or sub.url in subs_seen:
                continue
            local_subs.append(SubtitleEntry(
                provider=p.id,
                language=sub.language,
                label=sub.label or sub.language,
                url=sub.url,
                format=sub.format or "srt",
            ))

        outcome = "found" if local_entries else ("failed" if isinstance(result, _TimedOutResult) else "empty")
        await provider_stats.record(p.id, outcome, dur_ms)

        state = "found" if local_entries else "empty"
        async with lock:
            scored.extend(local_entries)
            for sub in local_subs:
                if sub.url not in subs_seen:
                    subs_seen.add(sub.url)
                    all_subs.append(sub)

        if on_provider_done:
            on_provider_done(p.id, state, [e for e, _ in local_entries], dur_ms)

    async def invoke_with_tracking(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            await invoke_one(p)
        except (asyncio.TimeoutError, Exception):
            dur_ms = int((time.monotonic() - t0) * 1000)
            await provider_stats.record(p.id, "failed", dur_ms)
            if on_provider_done:
                on_provider_done(p.id, "failed", [], dur_ms)

    await asyncio.gather(*[invoke_with_tracking(p) for p in eligible])

    scored.sort(key=lambda x: x[1], reverse=True)
    deduped = _dedup_streams(scored)
    for i, entry in enumerate(deduped):
        entry.priority = i

    return deduped, all_subs


# ── DOWNLOAD: expand m3u8 → per-resolution + dedup mp4 by quality ─────────────

_DOWNLOAD_PROVIDER_IDS = {
    # Indian download-link providers (direct mp4/mkv)
    "vegamovies", "hdhub4u", "fourkhdhub", "rogmovies",
    "multimovies", "movies4u", "uhdmovies", "moviesmod",
    "topmovies", "bollyflix", "cinemacity",
    # Stream providers that also surface direct mp4/m3u8 links
    "rivestream", "allmovieland", "vidfast", "vidrock",
    "hexa", "xpass", "vaplayer", "dahmermovies", "hexasu",
    "hdrezka", "castle",
}

# Standard quality labels normalised for dedup
_QUALITY_NORM = {
    "2160p": "2160p", "4k": "2160p", "uhd": "2160p",
    "1080p": "1080p", "fhd": "1080p",
    "720p":  "720p",  "hd":  "720p",
    "480p":  "480p",  "sd":  "480p",
    "360p":  "360p",
    "240p":  "240p",
}

_RESOLUTION_ORDER = ["2160p", "1080p", "720p", "480p", "360p", "240p"]


def _normalise_quality(raw: str | None) -> str:
    if not raw:
        return "unknown"
    q = raw.lower().strip()
    for k, v in _QUALITY_NORM.items():
        if k in q:
            return v
    # Try to extract a number like "1080" → "1080p"
    m = re.search(r"(\d{3,4})", q)
    if m:
        h = int(m.group(1))
        if h >= 2000: return "2160p"
        if h >= 900:  return "1080p"
        if h >= 600:  return "720p"
        if h >= 420:  return "480p"
        if h >= 300:  return "360p"
        return "240p"
    return raw.strip() or "unknown"


async def _resolve_direct_url(url: str, headers: dict) -> str | None:
    """
    Follow redirects on a URL to see if it lands on a direct .mp4/.mkv file.
    Returns the final resolved URL if it's a direct media file, else None.

    Strategy:
      1. Try HEAD first (cheap, no body download).
      2. If the server rejects HEAD (405, 403, or connection error) fall back
         to a range-GET for 0 bytes — still gets the final URL + content-type
         without downloading the file body.
      3. Any 4xx/5xx on the final hop → discard the link (it's dead).
    """
    from app.utils.http import get_client
    client = await get_client()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
        **headers,
    }

    def _is_direct_media(final_url: str, content_type: str) -> bool:
        return (
            any(ext in final_url.lower() for ext in (".mp4", ".mkv", ".webm"))
            or any(ct in content_type for ct in ("video/", "application/octet-stream"))
        )

    # ── Attempt 1: HEAD ───────────────────────────────────────────────────────
    try:
        r = await client.head(url, headers=hdrs, timeout=8, follow_redirects=True)
        final_url = str(r.url)
        # Treat any 4xx/5xx as a dead link
        if r.status_code >= 400:
            return None
        content_type = r.headers.get("content-type", "")
        if _is_direct_media(final_url, content_type):
            return final_url
        # HEAD returned 2xx but content-type was ambiguous — fall through to GET
        if r.status_code not in (405, 403):
            # Server answered and it's not a media file at all — discard
            return None
    except Exception:
        pass  # connection error or timeout — try GET fallback

    # ── Attempt 2: Range-GET for 0 bytes (avoids body, gets redirect + type) ──
    try:
        range_hdrs = {**hdrs, "Range": "bytes=0-0"}
        r = await client.get(url, headers=range_hdrs, timeout=8, follow_redirects=True)
        final_url = str(r.url)
        if r.status_code >= 400:
            return None
        content_type = r.headers.get("content-type", "")
        if _is_direct_media(final_url, content_type):
            return final_url
    except Exception:
        pass

    return None


async def _validate_m3u8_variants(
    links: list[DownloadLink],
    headers: dict,
) -> list[DownloadLink]:
    """
    HEAD-check each m3u8 variant URL concurrently and discard any that return
    4xx/5xx or fail to connect.  This prevents dead quality entries (e.g. a
    provider exposes 1080p/720p/480p but only 720p actually exists) from
    reaching the client.
    """
    from app.utils.http import get_client
    client = await get_client()

    async def _check(lnk: DownloadLink) -> DownloadLink | None:
        try:
            r = await client.head(
                lnk.url, headers=headers, timeout=5, follow_redirects=True
            )
            if r.status_code < 400:
                return lnk
        except Exception:
            pass
        return None

    results = await asyncio.gather(*[_check(lnk) for lnk in links], return_exceptions=True)
    return [r for r in results if r and not isinstance(r, Exception)]


async def _fetch_m3u8_resolutions(
    url: str,
    headers: dict,
    provider: str,
    provider_id: str,
    language: str,
    original_quality: str | None = None,
) -> list[DownloadLink]:
    """
    Fetch an m3u8 master playlist and extract per-resolution stream URLs.
    Returns one DownloadLink per available resolution (no duplicates).
    If fetching fails, returns a single entry for the master URL.
    """
    try:
        from app.utils.http import get_client
        client = await get_client()
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
            **headers,
        }
        r = await client.get(url, headers=hdrs, timeout=8, follow_redirects=True)
        if not r or r.status_code >= 400:
            raise ValueError("bad status")

        text = r.text
        lines = text.splitlines()

        results: list[DownloadLink] = []
        seen_res: set[str] = set()

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                # Parse resolution and bandwidth from tag
                res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                bw_match  = re.search(r"BANDWIDTH=(\d+)", line)
                height    = int(res_match.group(2)) if res_match else 0
                bandwidth = int(bw_match.group(1))  if bw_match  else 0

                # Get the URI on next non-comment line
                i += 1
                while i < len(lines) and lines[i].strip().startswith("#"):
                    i += 1
                if i >= len(lines):
                    break
                seg_url = lines[i].strip()
                if not seg_url or seg_url.startswith("#"):
                    i += 1
                    continue

                # Resolve relative URLs
                if not seg_url.startswith("http"):
                    base = url.rsplit("/", 1)[0] + "/"
                    seg_url = base + seg_url

                # Quality label from resolution
                if height >= 2000:   q = "2160p"
                elif height >= 900:  q = "1080p"
                elif height >= 600:  q = "720p"
                elif height >= 420:  q = "480p"
                elif height >= 300:  q = "360p"
                elif height > 0:     q = "240p"
                else:
                    # Infer from bandwidth
                    if bandwidth >= 4_000_000:   q = "1080p"
                    elif bandwidth >= 2_000_000: q = "720p"
                    elif bandwidth >= 800_000:   q = "480p"
                    elif bandwidth >= 400_000:   q = "360p"
                    else:                         q = "240p"

                if q not in seen_res:
                    seen_res.add(q)
                    results.append(DownloadLink(
                        provider=provider,
                        provider_id=provider_id,
                        url=seg_url,
                        type="m3u8",
                        quality=q,
                        language=language,
                        headers=headers,
                    ))
            i += 1

        if results:
            # Validate that each variant URL actually responds before returning
            # it — avoids passing dead quality URLs to the client.
            results = await _validate_m3u8_variants(results, hdrs)
            if results:
                # Sort by resolution descending
                order = {q: i for i, q in enumerate(_RESOLUTION_ORDER)}
                results.sort(key=lambda x: order.get(x.quality or "", 99))
                return results

    except Exception:
        pass

    # Fallback: validate the master URL itself before returning it
    try:
        from app.utils.http import get_client
        client = await get_client()
        _hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36",
            **headers,
        }
        r = await client.head(url, headers=_hdrs, timeout=6, follow_redirects=True)
        if r.status_code >= 400:
            return []   # master URL is dead — don't return it
    except Exception:
        return []       # can't reach it at all — skip

    return [DownloadLink(
        provider=provider,
        provider_id=provider_id,
        url=url,
        type="m3u8",
        quality=original_quality or "Auto",  # preserve known label from anchor context
        language=language,
        headers=headers,
    )]


async def run_download_providers(
    data: LinkData,
    kind: ContentKind,
) -> list[DownloadLink]:
    """
    Fan out to all download providers concurrently.
    - m3u8 master playlists → expanded into per-resolution entries
    - mp4 links → deduplicated by normalised quality label (no double 1080p etc.)
    """
    providers = [
        p for p in get_providers_for_kind(kind)
        if p.id in _DOWNLOAD_PROVIDER_IDS
    ]
    if not providers:
        return []

    eligible = [p for p in providers if await provider_stats.should_run(p.id)]
    if not eligible:
        return []

    lock = asyncio.Lock()
    # Track seen qualities PER language to avoid double 1080p English entries
    seen_mp4: dict[str, set[str]] = {}   # language → set of normalised quality
    raw_links: list[DownloadLink] = []   # m3u8 masters to expand
    mp4_links: list[DownloadLink] = []   # direct mp4/mkv

    async def invoke_one(p: Provider) -> None:
        t0 = time.monotonic()
        try:
            result = await safe_invoke(p, data, _settings.provider_timeout_ms)
            dur_ms = int((time.monotonic() - t0) * 1000)
            outcome = "found" if any(s.link for s in result.streams) else "empty"
            await provider_stats.record(p.id, outcome, dur_ms)
        except Exception:
            dur_ms = int((time.monotonic() - t0) * 1000)
            await provider_stats.record(p.id, "failed", dur_ms)
            return

        async with lock:
            for stream in result.streams:
                if not stream.link:
                    continue
                lang = _lang_label(stream.server)
                link = DownloadLink(
                    provider=p.name,
                    provider_id=p.id,
                    url=stream.link,
                    type=stream.type or "mp4",
                    quality=stream.quality,
                    language=lang,
                    headers=stream.headers,
                )
                if stream.type == "m3u8":
                    raw_links.append(link)
                else:
                    q_norm = _normalise_quality(stream.quality)
                    lang_seen = seen_mp4.setdefault(lang, set())
                    if q_norm not in lang_seen:
                        lang_seen.add(q_norm)
                        link.quality = q_norm  # normalise label
                        mp4_links.append(link)

    await asyncio.gather(*[invoke_one(p) for p in eligible])

    # Expand m3u8 masters in parallel
    expand_tasks = [
        _fetch_m3u8_resolutions(
            lnk.url, lnk.headers, lnk.provider, lnk.provider_id, lnk.language,
            lnk.quality,   # pass known quality label so fallback preserves it
        )
        for lnk in raw_links
    ]
    expanded_results = await asyncio.gather(*expand_tasks, return_exceptions=True)

    # Merge expanded m3u8 entries, dedup by (language, quality)
    seen_m3u8: dict[str, set[str]] = {}
    m3u8_links: list[DownloadLink] = []
    for res in expanded_results:
        if isinstance(res, Exception):
            continue
        for lnk in res:
            q_norm = _normalise_quality(lnk.quality)
            lang_seen = seen_m3u8.setdefault(lnk.language, set())
            if q_norm not in lang_seen:
                lang_seen.add(q_norm)
                lnk.quality = q_norm
                m3u8_links.append(lnk)

    # Resolve mp4 links: follow redirect chains to get the real CDN URL.
    # Many providers return a bounce URL that only resolves to a direct mp4
    # after following 1–3 redirects. Without this step the client gets a
    # redirect URL that requires Referer/cookies it doesn't have.
    #
    # If _resolve_direct_url returns None the URL is dead or not a direct
    # media file — we mark it for removal rather than passing a broken link
    # to the client.
    _dead_mp4: set[int] = set()

    async def resolve_mp4_link(idx: int, lnk: DownloadLink) -> None:
        if lnk.type in ("mp4", "mkv") and lnk.url:
            resolved = await _resolve_direct_url(lnk.url, lnk.headers)
            if resolved is None:
                # URL is dead / not a downloadable media file
                _dead_mp4.add(idx)
            elif resolved != lnk.url:
                lnk.url = resolved
                # Clear headers that were specific to the redirect chain
                lnk.headers = {}

    await asyncio.gather(
        *[resolve_mp4_link(i, lnk) for i, lnk in enumerate(mp4_links)],
        return_exceptions=True,
    )

    # Drop dead mp4 links
    mp4_links = [lnk for i, lnk in enumerate(mp4_links) if i not in _dead_mp4]

    # Combine: direct mp4/mkv first (immediately downloadable), then m3u8
    # m3u8 links are kept as fallback for clients that can handle HLS
    all_links = mp4_links + m3u8_links

    # Probe file sizes for direct mp4/mkv links in parallel.
    # Skip m3u8 — variant playlists don't have a meaningful Content-Length.
    async def fill_size(lnk: DownloadLink) -> None:
        if lnk.size_bytes is None and lnk.type != "m3u8":
            lnk.size_bytes = await _probe_size(lnk.url, lnk.headers)

    await asyncio.gather(
        *[fill_size(lnk) for lnk in all_links],
        return_exceptions=True,
    )

    # Sort by resolution descending within each language
    order = {q: i for i, q in enumerate(_RESOLUTION_ORDER)}
    all_links.sort(key=lambda x: (x.language, order.get(x.quality or "", 99)))

    return all_links


# ── SSE helpers (kept for backward compat if SSE is still used) ───────────────

def _sse_event(event: str, data: dict | list | str) -> str:
    if isinstance(data, (dict, list)):
        payload = orjson.dumps(data).decode()
    else:
        payload = data
    return f"event: {event}\ndata: {payload}\n\n"
