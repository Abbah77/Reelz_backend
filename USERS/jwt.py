"""
USERS/jwt.py — JWT signing and verification.

We issue our own short-lived access tokens after Google auth succeeds.
The app sends these as: Authorization: Bearer <token>

Payload:
    sub   — user.id (our UUID)
    email — for debug convenience
    exp   — expiry timestamp
    iat   — issued at
"""
from __future__ import annotations

import time
from typing import Optional

import jwt as pyjwt
from fastapi import HTTPException, Request

from config import get_settings

_s = get_settings()

_SECRET    = _s.jwt_secret
_ALGORITHM = _s.jwt_algorithm
_TTL_H     = _s.jwt_access_ttl_hours


def sign(user_id: str, email: str = "") -> tuple[str, int]:
    """
    Returns (token, expires_at_ms).
    expires_at_ms is milliseconds epoch — matches Android client expectation.
    """
    now = int(time.time())
    expires = now + _TTL_H * 3600
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": expires,
    }
    token = pyjwt.encode(payload, _SECRET, algorithm=_ALGORITHM)
    return token, expires * 1000


def verify(token: str) -> dict:
    """
    Decode and validate token. Raises HTTPException 401 on any failure.
    Returns the decoded payload.
    """
    try:
        return pyjwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def user_id_from_request(request: Request) -> Optional[str]:
    """Extract user_id from Bearer token. Returns None if absent/invalid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    try:
        payload = pyjwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None


async def require_user(request: Request) -> str:
    """
    FastAPI dependency — raises 401 if no valid JWT.
    Returns user_id on success.
    """
    uid = user_id_from_request(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid
