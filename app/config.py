"""
Application configuration — loaded once from .env via pydantic-settings.
All tunables live here. Nothing else imports os.environ directly.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ─────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    debug: bool = False

    # ── TMDB ───────────────────────────────────────────────────────────────────
    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    # ── FlareSolverr ───────────────────────────────────────────────────────────
    flaresolverr_url: str = ""          # comma-separated list

    # ── WARP ───────────────────────────────────────────────────────────────────
    warp_mode: str = "off"              # off | required | fallback | all
    warp_proxy_url: str = ""            # socks5://host:port (comma-sep)
    warp_flaresolverr_url: str = ""

    # ── Provider tuning ────────────────────────────────────────────────────────
    provider_timeout_ms: int = 45_000
    cache_ttl_seconds: int = 480        # 8-minute warm cache

    # ── Cache ──────────────────────────────────────────────────────────────────
    redis_url: str = ""                 # empty = use in-memory cache

    # ── Indian providers ───────────────────────────────────────────────────────
    castle_suffix: str = ""

    # ── Anime providers ────────────────────────────────────────────────────────
    anizone_base_url: str = "https://anizone.to"
    animeparhe_base_url: str = "https://animepahe.pw"

    # ── Subtitle services ──────────────────────────────────────────────────────
    wyzie_key: str = ""

    # ── Consumet ───────────────────────────────────────────────────────────────
    consumet_url: str = ""
    consumet_providers: str = "zoro,animepahe,animekai,gogoanime"

    # ── Debrid ─────────────────────────────────────────────────────────────────
    realdebrid_key: str = ""
    alldebrid_key: str = ""
    torbox_key: str = ""

    # ── TVDB ───────────────────────────────────────────────────────────────────
    tvdb_api_key: str = ""
    tvdb_pin: str = ""

    # ── HDRezka ────────────────────────────────────────────────────────────────
    hdrezka_base_url: str = "https://rezka.ag"

    # ── Torrent streaming ──────────────────────────────────────────────────────
    torrent_enabled: bool = False
    torrent_dir: str = "/tmp/reelz-torrents"
    webtorrent_port: int = 9999

    # ── App authentication ─────────────────────────────────────────────────────
    # Shared secret sent by the Android app as X-Reelz-Token header.
    # Set in .env / Render environment variables. Rotate without an app update
    # by pushing a new value to config.json + this env var simultaneously.
    app_secret_token: str = ""

    # ── Circuit breaker ────────────────────────────────────────────────────────
    # 2 failures before tripping (was 4) — faster failure detection when a
    # provider is dead, reducing tail latency for users.
    # 300s cooldown (was 600s) — dead providers are retried sooner.
    circuit_breaker_fail_threshold: int = 2
    circuit_breaker_cooldown_s: int = 300
    provider_stats_file: str = "./provider-stats.json"

    # ── Payments / Paystack ────────────────────────────────────────────────────
    # Secret key from Paystack dashboard → API Keys & Webhooks.
    # Used to validate HMAC-SHA512 webhook signatures.
    # Never expose this in client-side code or config.json.
    paystack_secret_key: str = ""

    # ── Misc ───────────────────────────────────────────────────────────────────
    debug_token: str = ""
    doh_enabled: bool = True
    doh_provider: str = "google"

    # ── CORS ───────────────────────────────────────────────────────────────────
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
