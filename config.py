"""
config.py — All settings in one place. Nothing reads os.environ directly.

Every folder (api, ENGINE, USERS) imports from here.
Change a value here — it changes everywhere.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── Auth ──────────────────────────────────────────────────────────────────
    app_secret_token: str = ""          # X-Reelz-Token header value

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "*"

    # ── TMDB ─────────────────────────────────────────────────────────────────
    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    # ── Cache ─────────────────────────────────────────────────────────────────
    redis_url: str = ""                 # empty = memory cache
    cache_ttl_seconds: int = 480        # 8 min stream cache
    cache_backend: str = "memory"       # memory | redis | cloudflare

    # ── Circuit breaker ───────────────────────────────────────────────────────
    cb_fail_threshold: int = 2
    cb_cooldown_seconds: int = 300
    provider_stats_file: str = "./provider-stats.json"

    # ── Provider tuning ───────────────────────────────────────────────────────
    provider_timeout_ms: int = 45_000

    # ── WARP ──────────────────────────────────────────────────────────────────
    warp_mode: str = "off"              # off | required | fallback | all
    warp_proxy_url: str = ""            # socks5://host:port
    warp_flaresolverr_url: str = ""     # http://host:8191

    # ── FlareSolverr ──────────────────────────────────────────────────────────
    flaresolverr_url: str = ""

    # ── Captcha ───────────────────────────────────────────────────────────────
    anticaptcha_key: str = ""
    twocaptcha_key: str = ""

    # ── Residential IP ────────────────────────────────────────────────────────
    residential_proxy_url: str = ""     # http://user:pass@host:port

    # ── Debrid ────────────────────────────────────────────────────────────────
    realdebrid_key: str = ""
    alldebrid_key: str = ""
    torbox_key: str = ""

    # ── Provider-specific ─────────────────────────────────────────────────────
    castle_suffix: str = ""
    hdrezka_base_url: str = "https://rezka.ag"
    anizone_base_url: str = "https://anizone.to"
    wyzie_key: str = ""
    consumet_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
