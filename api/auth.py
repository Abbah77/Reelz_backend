"""
api/auth.py — Static token verification for ENGINE endpoints.

This is the gate security for all ENGINE routes (stream, download, subtitle, shorts).
Completely separate from user JWTs (USERS/jwt.py).

Usage:
    from api.auth import verify
    @router.post("", dependencies=[Depends(verify)])
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
    FastAPI dependency — enforce the static app secret token.
    Returns None on success. Raises 403 on failure.
    No token configured (empty APP_SECRET_TOKEN) → open dev mode.
    """
    secret = _s.app_secret_token
    if not secret:
        return  # dev mode — no auth required

    if x_reelz_token != secret:
        raise HTTPException(status_code=403, detail="Forbidden")
