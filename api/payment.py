"""api/payment.py — Payment routes. Never cached."""
from __future__ import annotations
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from USERS.db import get_db
from USERS.jwt import require_user
from USERS.models import User
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/payment", tags=["Payment"])

@router.post("/init")
async def init_payment(response: Response, plan: str = Form(...), user_id: str = Depends(require_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    from USERS.payment import init_payment as _init
    data = await _init(user_id=user.id, user_email=user.email, plan=plan, db=db)
    data.pop("ok", None)
    set_cache(response, None)
    return ok(data, cache_ttl_ms=None)

@router.post("/webhook")
async def paystack_webhook(request: Request, response: Response, x_paystack_signature: str = Header(..., alias="x-paystack-signature"), db: AsyncSession = Depends(get_db)):
    body = await request.body()
    from USERS.payment import handle_webhook
    result = await handle_webhook(body=body, signature=x_paystack_signature, db=db)
    result.pop("ok", None)
    set_cache(response, None)
    return ok(result, cache_ttl_ms=None)
