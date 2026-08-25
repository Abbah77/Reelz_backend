"""api/user_auth.py — Auth routes. Never cached."""
from __future__ import annotations
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from USERS.db import get_db
from USERS.jwt import verify as verify_jwt, require_user
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/auth", tags=["Auth"])

class GoogleAuthBody(BaseModel):
    id_token: str = Field(...)

class HistorySyncItem(BaseModel):
    id: str = ""
    season: int = 0
    episode: int = 0
    position_ms: int = 0
    duration_ms: int = 0
    watched_at: int = 0

class SyncBody(BaseModel):
    history: list[HistorySyncItem] = Field(default_factory=list)


@router.post("/google")
async def auth_google(body: GoogleAuthBody, response: Response, db: AsyncSession = Depends(get_db)):
    from USERS.google_auth import google_sign_in
    result = await google_sign_in(body.id_token, db)
    result.pop("ok", None)
    set_cache(response, None)
    return ok(result, cache_ttl_ms=None)


@router.post("/refresh")
async def refresh_session(response: Response, authorization: str = Header(...), db: AsyncSession = Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token   = authorization.removeprefix("Bearer ").strip()
    payload = verify_jwt(token)
    user_id = payload["sub"]
    from sqlalchemy import select
    from USERS.models import User
    from USERS.jwt import sign
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    new_token, expires_at_ms = sign(user.id, user.email)
    set_cache(response, None)
    return ok({"access_token": new_token, "expires_at_ms": expires_at_ms}, cache_ttl_ms=None)


@router.post("/sync")
async def sync_user_data(body: SyncBody, response: Response, user_id: str = Depends(require_user), db: AsyncSession = Depends(get_db)):
    from USERS.sync import sync_history
    await sync_history(user_id=user_id, history_items=[i.model_dump() for i in body.history], db=db)
    set_cache(response, None)
    return ok({}, cache_ttl_ms=None)
