"""
api/envelope.py — Single standard envelope for every response in Reelz.

Usage:
    from api.envelope import ok, err

    return ok({"streams": [...], "expires_at_ms": ...}, cache_ttl_ms=3_600_000)
    return err("No streams available for this title")

Shape — success:
    { "ok": true,  "data": {...}, "error": null,  "cache_ttl_ms": 3600000 }

Shape — error:
    { "ok": false, "data": null,  "error": "...", "cache_ttl_ms": null    }

Rules:
  • ok / data / error / cache_ttl_ms are ALWAYS present at the root.
  • cache_ttl_ms is response-level metadata — it lives at root, not inside data.
  • expires_at_ms (stream/download link expiry) is content — it lives inside data.
  • HTTP status codes remain the authoritative error signal; ok is a fast-check.
  • Never raise 401 / 403 on guest-ok routes — those must return ok responses.
"""
from __future__ import annotations

from typing import Any, Optional


def ok(data: Any, cache_ttl_ms: Optional[int] = None) -> dict:
    """Wrap a successful payload in the standard envelope."""
    return {
        "ok":           True,
        "data":         data,
        "error":        None,
        "cache_ttl_ms": cache_ttl_ms,
    }


def err(message: str, cache_ttl_ms: Optional[int] = None) -> dict:
    """Wrap an error message in the standard envelope."""
    return {
        "ok":           False,
        "data":         None,
        "error":        message,
        "cache_ttl_ms": cache_ttl_ms,
    }
