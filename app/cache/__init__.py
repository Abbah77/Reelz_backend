"""
cache/__init__.py — cache backend selector.

Switching from memory to Redis tomorrow:
  Change the two lines below. Nothing else in the codebase changes.
"""
from __future__ import annotations

from app.config import get_settings

_settings = get_settings()

# ── Backend selection ──────────────────────────────────────────────────────────
# Change these two lines to swap backends. That's it.

if _settings.redis_url:
    from app.cache.redis import RedisCache as _Backend      # type: ignore[assignment]
    cache = _Backend()
else:
    from app.cache.memory import MemoryCache as _Backend    # type: ignore[assignment]
    cache = _Backend()

__all__ = ["cache"]
