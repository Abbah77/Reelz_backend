"""
tests/test_providers.py — provider isolation and contract tests.

Each test verifies:
  1. Provider returns ProviderResult (never raises)
  2. Provider result has the expected shape
  3. Providers are fully isolated (no cross-imports)
"""
from __future__ import annotations

import asyncio
import pytest

from app.schemas.provider import LinkData, ProviderResult
from app.providers.base import safe_invoke


def make_movie_data(**kwargs) -> LinkData:
    defaults = dict(id=550, type="movie", title="Fight Club", year=1999)
    return LinkData(**{**defaults, **kwargs})


def make_tv_data(**kwargs) -> LinkData:
    defaults = dict(id=1396, type="tv", title="Breaking Bad", season=1, episode=1)
    return LinkData(**{**defaults, **kwargs})


def make_anime_data(**kwargs) -> LinkData:
    defaults = dict(
        id=37854, type="tv", title="One Piece",
        season=1, episode=1, is_anime=True,
    )
    return LinkData(**{**defaults, **kwargs})


# ── Contract: every provider must return ProviderResult, never raise ──────────

@pytest.mark.asyncio
async def test_rivestream_returns_result():
    from app.providers.stream.rivestream.provider import RiveStreamProvider
    p = RiveStreamProvider()
    result = await safe_invoke(p, make_movie_data(), timeout_ms=5_000)
    assert isinstance(result, ProviderResult)


@pytest.mark.asyncio
async def test_primevids_returns_result():
    from app.providers.stream.primevids.provider import PrimeVidsProvider
    p = PrimeVidsProvider()
    result = await safe_invoke(p, make_movie_data(), timeout_ms=5_000)
    assert isinstance(result, ProviderResult)


@pytest.mark.asyncio
async def test_anime_provider_skips_non_anime():
    from app.providers.stream.anime.provider import AniZoneProvider
    p = AniZoneProvider()
    movie = make_movie_data()
    assert not movie.is_anime
    result = await safe_invoke(p, movie, timeout_ms=2_000)
    # Anime providers must return empty for non-anime — they guard with is_anime check
    assert result.streams == []


@pytest.mark.asyncio
async def test_primevids_skips_anime():
    from app.providers.stream.primevids.provider import PrimeVidsProvider
    p = PrimeVidsProvider()
    anime = make_anime_data()
    result = await safe_invoke(p, anime, timeout_ms=2_000)
    assert result.streams == []


# ── Isolation: provider modules must not import each other ───────────────────

def test_rivestream_does_not_import_primevids():
    """Providers must be fully isolated — Rule 1."""
    import app.providers.stream.rivestream.provider as mod
    import inspect
    src = inspect.getsource(mod)
    assert "primevids" not in src.lower()


def test_primevids_does_not_import_rivestream():
    import app.providers.stream.primevids.provider as mod
    import inspect
    src = inspect.getsource(mod)
    assert "rivestream" not in src.lower()


# ── Architecture: managers must not scrape (Rule 2) ──────────────────────────

def test_stream_manager_has_no_regex_scraping():
    """Managers coordinate, they don't scrape."""
    import app.managers.stream as mod
    import inspect
    src = inspect.getsource(mod)
    # No BeautifulSoup or HTML parsing in manager
    assert "BeautifulSoup" not in src
    assert "find_all" not in src


# ── Architecture: API must have no business logic (Rule 3) ───────────────────

def test_streams_api_has_no_provider_imports():
    import app.api.streams as mod
    import inspect
    src = inspect.getsource(mod)
    assert "providers" not in src
    assert "safe_invoke" not in src


# ── Registry: adding/removing providers ───────────────────────────────────────

def test_stream_registry_lists_providers():
    from app.providers.stream.registry import init_stream_providers, get_stream_providers
    init_stream_providers()
    providers = get_stream_providers()
    assert len(providers) > 0
    # All providers have id and name
    for p in providers:
        assert p.id, f"Provider {p.__class__.__name__} missing id"
        assert p.name, f"Provider {p.__class__.__name__} missing name"


def test_no_duplicate_provider_ids():
    from app.providers.stream.registry import init_stream_providers, get_stream_providers
    from app.providers.download.registry import init_download_providers, get_download_providers
    init_stream_providers()
    init_download_providers()
    all_providers = get_stream_providers() + get_download_providers()
    ids = [p.id for p in all_providers]
    assert len(ids) == len(set(ids)), f"Duplicate provider IDs: {[i for i in ids if ids.count(i) > 1]}"


# ── Cache: switching backends doesn't break managers ─────────────────────────

@pytest.mark.asyncio
async def test_cache_set_get_delete():
    from app.cache import cache
    await cache.set("test:key", {"hello": "world"}, ttl=60)
    val = await cache.get("test:key")
    assert val == {"hello": "world"}
    await cache.delete("test:key")
    val = await cache.get("test:key")
    assert val is None
