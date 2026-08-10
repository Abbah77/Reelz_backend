"""
api/auth.py — Token verification plugin.

This is the gate security. Every request passes through here first.
Add new security checks here. Remove them here. Nothing else changes.

Usage:
    from api.auth import verify
    # use as FastAPI dependency on any route
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request
from config import get_settings

_s = get_settings()


async def verify(
    request: Request,
    x_reelz_token: str = Header(default=""),
) -> None:
    """
    FastAPI dependency — inject into any route to enforce auth.
    Returns None on success. Raises 403 on failure.
    Add rate limiting, IP whitelisting, JWT — all here.
    """
    secret = _s.app_secret_token
    if not secret:
        return  # no token configured = open (dev mode)

    if x_reelz_token != secret:
        raise HTTPException(status_code=403, detail="Forbidden")
