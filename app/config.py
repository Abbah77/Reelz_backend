"""
Application configuration — mirrors .env.example from the Node project.
"""
from __future__ import annotations
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Server ──────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    debug: bool = False

    # ── TMDB ────────────────────────────────────────────────────────────────
    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p"

    # ── FlareSolverr ────────────────────────────────────────────────────────
    flaresolverr_url: str = ""          # comma-separated list

    # ── WARP ────────────────────────────────────────────────────────────────
    warp_mode: str = "off"              # off | required | fallback | all
    warp_proxy_url: str = ""            # socks5://host:port (comma-sep)
    warp_flaresolverr_url: str = ""

    # ── Provider tuning ─────────────────────────────────────────────────────
    provider_timeout_ms: int = 45_000
    cache_ttl_seconds: int = 300        # 5-minute warm cache

    # ── Castle (Indian multi-lang) ───────────────────────────────────────────
    castle_suffix: str = ""

    # ── Anime provider base URLs (rotate occasionally) ───────────────────────
    anizone_base_url: str = "https://anizone.to"
    animeparhe_base_url: str = "https://animepahe.pw"

    # ── Subtitle services ────────────────────────────────────────────────────
    wyzie_key: str = ""

    # ── Consumet ─────────────────────────────────────────────────────────────
    consumet_url: str = ""
    consumet_providers: str = "zoro,animepahe,animekai,gogoanime"

    # ── Debrid ───────────────────────────────────────────────────────────────
    realdebrid_key: str = ""
    alldebrid_key: str = ""
    torbox_key: str = ""

    # ── TVDB ─────────────────────────────────────────────────────────────────
    tvdb_api_key: str = ""
    tvdb_pin: str = ""

    # ── HDRezka ──────────────────────────────────────────────────────────────
    hdrezka_base_url: str = "https://rezka.ag"

    # ── Torrent streaming ─────────────────────────────────────────────────────
    torrent_enabled: bool = False       # set TORRENT_ENABLED=1 to activate
    torrent_dir: str = "/tmp/reelz-torrents"
    torrent_idle_ms: int = 20 * 60 * 1000   # evict torrents unused >20 min
    torrent_add_timeout_ms: int = 90_000     # metadata discovery timeout
    webtorrent_port: int = 9999         # port for webtorrent-cli daemon

    # ── Circuit breaker ───────────────────────────────────────────────────────
    circuit_breaker_fail_threshold: int = 4      # consecutive failures to trip
    circuit_breaker_cooldown_s: int = 600        # 10-minute cooldown
    provider_stats_file: str = "./provider-stats.json"

    # ── Misc ─────────────────────────────────────────────────────────────────
    debug_token: str = ""
    doh_enabled: bool = True
    doh_provider: str = "google"        # google | cloudflare | quad9

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
