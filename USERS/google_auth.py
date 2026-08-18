"""
USERS/google_auth.py — Verify a Google ID token and upsert the User row.

Schema v3:
- name, email, photo_url are NOT returned — Google SDK provides them to the app directly.
- Returns: ok, user_id, access_token, refresh_token, expires_at_ms, premium, premium_expires_at_ms
"""
from __future__ import annotations

import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.models import User
from USERS.jwt import sign, sign_refresh
from config import get_settings

_s = get_settings()

_GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


async def _verify_google_token(id_token: str) -> dict:
    """Verify a Google ID token. Returns decoded claims or raises HTTPException 401."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(_GOOGLE_TOKENINFO, params={"id_token": id_token})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google ID token")
        claims = r.json()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Could not reach Google auth servers")

    if _s.google_client_id:
        aud = claims.get("aud", "")
        azp = claims.get("azp", "")
        if _s.google_client_id not in (aud, azp):
            raise HTTPException(status_code=401, detail="Token audience mismatch")

    if claims.get("email_verified") not in ("true", True):
        raise HTTPException(status_code=401, detail="Google email not verified")

    return claims


async def google_sign_in(id_token: str, db: AsyncSession) -> dict:
    """
    Verify Google ID token, upsert user, issue JWT access + refresh tokens.
    Returns schema v3 auth response.
    """
    claims = await _verify_google_token(id_token)

    google_sub = claims["sub"]
    email      = claims.get("email", "")
    name       = claims.get("name", "")
    photo_url  = claims.get("picture")

    # Upsert
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user is None:
        r2 = await db.execute(select(User).where(User.email == email))
        user = r2.scalar_one_or_none()

    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            google_sub=google_sub,
            email=email,
            name=name,
            photo_url=photo_url,
        )
        db.add(user)
    else:
        user.google_sub = google_sub
        user.name       = name or user.name
        user.photo_url  = photo_url or user.photo_url

    await db.flush()

    access_token, expires_at_ms = sign(user.id, email)
    refresh_token = sign_refresh(user.id)

    return {
        "ok":                    True,
        "user_id":               user.id,
        "access_token":          access_token,
        "refresh_token":         refresh_token,
        "expires_at_ms":         expires_at_ms,
        "premium":               user.is_premium_active(),
        "premium_expires_at_ms": user.premium_expires_at if user.is_premium_active() else 0,
    }
