"""
USERS/google_auth.py — Verify a Google ID token and upsert the User row.

Flow:
  1. Verify the Firebase/Google ID token against Google's public certs.
  2. Extract sub, email, name, picture.
  3. Upsert into users table (creates on first sign-in).
  4. Issue our own JWT.

We verify tokens using Google's tokeninfo endpoint for simplicity.
For production scale, swap to google-auth-library which verifies offline
against cached public keys (no network round-trip per request).
"""
from __future__ import annotations

import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.models import User
from USERS.jwt import sign
from config import get_settings

_s = get_settings()

_GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


async def _verify_google_token(id_token: str) -> dict:
    """
    Verify a Google ID token.
    Returns decoded claims or raises HTTPException 401.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(_GOOGLE_TOKENINFO, params={"id_token": id_token})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google ID token")
        claims = r.json()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Could not reach Google auth servers")

    # Validate audience matches our client ID (if configured)
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
    Verify Google ID token, upsert user, issue JWT.
    Returns the full auth response dict.
    """
    claims = await _verify_google_token(id_token)

    google_sub = claims["sub"]
    email      = claims.get("email", "")
    name       = claims.get("name", "")
    photo_url  = claims.get("picture")

    # Upsert: find by google_sub, fall back to email
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user is None:
        # Check if email already exists (e.g. from a different Google account)
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
        # Update profile info
        user.google_sub = google_sub
        user.name       = name or user.name
        user.photo_url  = photo_url or user.photo_url

    await db.flush()

    token, expires_at_ms = sign(user.id, email)

    return {
        "ok":           True,
        "user_id":      user.id,
        "access_token": token,
        "premium":      user.is_premium_active(),
        "status":       user.status,
        "expires_at_ms": expires_at_ms,
        "name":         user.name,
        "email":        user.email,
        "photo_url":    user.photo_url,
    }
