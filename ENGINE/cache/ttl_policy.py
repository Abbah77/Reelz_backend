"""
ENGINE/cache/ttl_policy.py — Smart per-provider cache TTL policy.

This is the single source of truth for how long stream/download results
from each provider should be cached, both in the app (cache_ttl_ms) and
in Cloudflare (Cache-Control: public, max-age=N, s-maxage=N).

WHY TWO TTLs?
─────────────
  cache_ttl_ms   → how long the app (Redis / Memory / Cloudflare KV) holds the result.
  cf_max_age_s   → how long Cloudflare's edge caches the HTTP response.

  They are set independently because:
    • Cloudflare should evict a *bit* before the app does, so clients
      always receive a response while the app still has something warm.
    • Some URLs have hard-coded query-string expiry tokens that the app
      can infer; Cloudflare gets told a fraction of that window.

HOW TTL IS CHOSEN:
─────────────────
  Case A — Provider gives a real expires_at_ms on the Stream object:
    • Remaining lifetime = expires_at_ms - now
    • cache_ttl_ms  = 75% of remaining lifetime  (safe margin)
    • cf_max_age_s  = 70% of remaining lifetime  (evict from CF a bit earlier)
    • These are floors/ceilings defined per provider below.

  Case B — Provider gives no expiry (most scrapers):
    • We use psychology:
      - CDN-hosted m3u8s (served from a CDN with rotating tokens) → short
      - Direct archive / static hosting → long
      - iframes (third-party player pages) → very short (page layout changes)
      - MP4 direct links from scraping → medium
      - Trailers / YouTube shorts → very long (they never change)
    • Values are conservative. It is better to re-fetch than to serve a dead link.

Provider taxonomy built from reading all R_*.py files:
  R-001  2Embed        iframe constructed from TMDB id            → short
  R-002  VidFast       CDN m3u8 via JSON API                      → short-medium
  R-003  VidRock       CDN m3u8 via AES-encrypted API             → short-medium
  R-004  HexaSU        CDN m3u8 via double-crypto API             → short
  R-005  AllMovieLand  CDN m3u8 via JSON API                      → short-medium
  R-006  Xpass         CDN m3u8 from page scrape                  → short
  R-007  VaplayerV2    CDN m3u8 via JSON API                      → short-medium
  R-008  DahmerMovies  MP4 from Apache directory                  → medium-long
  R-009  RiveStream    CDN m3u8 multi-server w/ secret key        → very short (key rotates)
  R-010  PrimeVids     iframe → m3u8                              → short
  R-011  KissKh        CDN m3u8 via multi-step key fetch          → short (kkey expires)
  R-012  Castle        CDN m3u8 via AES-encrypted + security key  → short (key expires ~1h)
  R-013  HDRezka       CDN m3u8 via translator CDN                → short-medium
  R-014  AniZone       mp4 direct media link                      → medium
  R-015  AniNeko       CDN m3u8 packed-JS embed                   → short
  R-016  AnimeNoSub    CDN m3u8 via megaplay                      → short
  R-017  AnimeWorld    CDN m3u8 via zephyrflick                   → short
  R-018  VegaMovies    scraped mp4/m3u8 (CF-protected)            → medium
  R-019  HdHub4u       scraped mp4/m3u8 (CF-protected)            → medium
  R-020  4KHdHub       scraped mp4/m3u8 (CF-protected)            → medium
  R-021  Movies4u      scraped mp4/m3u8 (CF-protected)            → medium
  R-022  RogMovies     scraped mp4/m3u8 (CF-protected)            → medium
  R-023  MultiMovies   scraped mp4 via DooPlay                    → medium
  R-024  UhdMovies     scraped mp4 via driveleech/driveseed        → medium
  R-025  Moviesmod     scraped mp4 via hrefli bypass              → medium
  R-101  Download TPL  template, no-op                            → medium
  R-201  OpenSubtitles per-download signed URL                    → short (1-hour token)
  R-202  Subtitle #2   (same family, signed)                      → short
  R-301  TMDB Trailers YouTube links (never expire)               → very long
  R-302  Archive.org   static archive mp4                         → very long
"""
from __future__ import annotations

import time
from typing import Optional

# ── Per-provider TTL table (seconds) ─────────────────────────────────────────
# (app_ttl_s, cf_max_age_s)
# app_ttl_s   : how long the app backend caches the result
# cf_max_age_s: how long Cloudflare should cache the HTTP response edge-side
#               Always <= app_ttl_s so CF evicts before the app does.

_PROVIDER_TTL: dict[str, tuple[int, int]] = {
    # ── Very short (rotating tokens / keys that expire fast) ─────────────────
    "R-001": (300,   240),   # 2Embed iframe — constructed URL, but embed pages refresh
    "R-004": (480,   400),   # HexaSU — double-encrypted cap token, short-lived
    "R-009": (300,   240),   # RiveStream — Cloudflare worker secret key rotates
    "R-011": (480,   400),   # KissKh — kkey expires, multi-step auth
    "R-012": (3000,  2400),  # Castle — security key cached 1h on server, streams ~50min
    "R-015": (480,   400),   # AniNeko — CF-packed embed, fast-rotating
    "R-016": (480,   400),   # AnimeNoSub — megaplay embed tokens
    "R-017": (480,   400),   # AnimeWorld — zephyrflick tokens

    # ── Short (CDN m3u8 with signed query strings, typically 1–4 hours) ──────
    "R-002": (1800,  1500),  # VidFast — good CDN, 30-60 min typical window
    "R-003": (1800,  1500),  # VidRock — similar CDN stack
    "R-005": (1800,  1500),  # AllMovieLand — same pattern
    "R-006": (900,   750),   # Xpass — page scrape, less stable
    "R-007": (1800,  1500),  # VaplayerV2 — direct JSON, typical CDN lifetime
    "R-010": (600,   500),   # PrimeVids — iframe + m3u8 chain
    "R-013": (2400,  2000),  # HDRezka — CDN translator URLs, fairly stable
    "R-014": (3600,  3000),  # AniZone — direct media-player mp4, more stable
    "R-201": (3000,  2400),  # OpenSubtitles — download link token ~1h, use 50min
    "R-202": (3000,  2400),  # Subtitle provider 2

    # ── Medium (scraped MP4 / CF-protected pages — links stable hours to days)
    "R-008": (14400, 12000), # DahmerMovies — Apache directory, MP4, very stable
    "R-018": (7200,  6000),  # VegaMovies — scraped mp4, CF-protected, ~2-4h fresh
    "R-019": (7200,  6000),  # HdHub4u — same family
    "R-020": (7200,  6000),  # 4KHdHub — same
    "R-021": (7200,  6000),  # Movies4u — same
    "R-022": (7200,  6000),  # RogMovies — same
    "R-023": (7200,  6000),  # MultiMovies — DooPlay, fairly stable
    "R-024": (7200,  6000),  # UhdMovies — driveleech, links stable 4-8h
    "R-025": (7200,  6000),  # Moviesmod — hrefli, similar
    "R-101": (3600,  3000),  # Download template — default medium

    # ── Very long (static / permanent links) ──────────────────────────────────
    "R-301": (86400,  72000),  # TMDB Trailers — YouTube links, permanent
    "R-302": (604800, 518400), # Archive.org — archive MP4s, permanent (7 days)
}

# Fallback if provider not in table (unknown / new providers).
# Short by default — better to re-fetch than serve a dead link.
_DEFAULT_TTL: tuple[int, int] = (600, 480)


# ── Type-based TTL override (applied when the result type is known) ──────────
# Some URL types are always long/short regardless of provider.
_TYPE_OVERRIDE: dict[str, tuple[int, int]] = {
    "iframe":  (300, 240),   # iframes: embed pages change, always short
}


def get_provider_ttl(provider_id: str, url_type: str = "") -> tuple[int, int]:
    """
    Return (app_ttl_s, cf_max_age_s) for a given provider.

    url_type: "m3u8" | "mp4" | "iframe" — when provided, iframe always gets short TTL.
    """
    if url_type in _TYPE_OVERRIDE:
        return _TYPE_OVERRIDE[url_type]

    return _PROVIDER_TTL.get(provider_id, _DEFAULT_TTL)


def compute_ttl_from_expires(
    expires_at_ms: int,
    provider_id: str,
    url_type: str = "",
    safety_factor: float = 0.75,
    cf_factor: float = 0.70,
) -> tuple[int, int]:
    """
    When a provider returns a real expiry timestamp, compute TTL from it.

    safety_factor : fraction of remaining lifetime to use for app cache (0.75 = 75%)
    cf_factor     : fraction of remaining lifetime for Cloudflare edge (0.70 = 70%)

    Returns (app_ttl_s, cf_max_age_s).

    Floors/ceilings per provider are still respected — we never cache longer
    than the per-provider ceiling regardless of what the expiry says.
    """
    now_ms = int(time.time() * 1000)
    remaining_ms = expires_at_ms - now_ms

    if remaining_ms <= 0:
        # Already expired — do not cache at all.
        return (0, 0)

    remaining_s = remaining_ms / 1000

    app_ttl_s   = int(remaining_s * safety_factor)
    cf_max_age_s = int(remaining_s * cf_factor)

    # Clamp to per-provider ceiling — never cache longer than the table allows.
    provider_ceiling_app, provider_ceiling_cf = _PROVIDER_TTL.get(
        provider_id, (86400, 72000)  # generous ceiling for unknown providers
    )
    app_ttl_s    = min(app_ttl_s,    provider_ceiling_app)
    cf_max_age_s = min(cf_max_age_s, provider_ceiling_cf)

    # Minimum floor: 60 seconds — pointless to cache for less.
    app_ttl_s    = max(app_ttl_s,    60)
    cf_max_age_s = max(cf_max_age_s, 60)

    # iframe override always wins.
    if url_type == "iframe":
        app_ttl_s, cf_max_age_s = _TYPE_OVERRIDE["iframe"]

    return (app_ttl_s, cf_max_age_s)


def pick_best_ttl(streams: list[dict]) -> tuple[int, int]:
    """
    Given a list of stream dicts (as assembled by the manager), compute
    the best (app_ttl_s, cf_max_age_s) for the batch.

    Strategy:
      - If any stream carries expires_at_ms, use that to compute TTL.
        Use the *shortest* expiry across all streams (weakest link).
      - Otherwise, use the shortest per-provider TTL in the batch,
        because one short-lived provider would make the whole batch stale.
      - iframes in the batch pull the TTL down.
    """
    if not streams:
        return _DEFAULT_TTL

    now_ms = int(time.time() * 1000)

    # Collect TTLs for all streams.
    ttls: list[tuple[int, int]] = []

    for s in streams:
        pid     = s.get("provider_id", "")
        url_type = s.get("type", "")
        expires = s.get("expires_at_ms")

        if expires and isinstance(expires, (int, float)) and expires > now_ms:
            pair = compute_ttl_from_expires(int(expires), pid, url_type)
        else:
            pair = get_provider_ttl(pid, url_type)

        ttls.append(pair)

    # Use the minimum across the batch (weakest link).
    best_app = min(t[0] for t in ttls)
    best_cf  = min(t[1] for t in ttls)

    return (best_app, best_cf)


def ttl_to_ms(ttl_s: int) -> int:
    """Convert seconds to milliseconds."""
    return ttl_s * 1000
