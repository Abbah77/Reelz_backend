"""
api/payment.py — Payment routes.

POST /payment/init    — initialize a Paystack transaction (requires JWT)
POST /payment/webhook — Paystack webhook (verified by HMAC signature, NOT JWT)

Response: { ok, authorization_url, reference }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from USERS.db import get_db
from USERS.jwt import require_user
from USERS.models import User

router = APIRouter(prefix="/payment", tags=["Payment"])


@router.post("/init")
async def init_payment(
    plan:    str = Form(..., description="monthly | yearly"),
    user_id: str = Depends(require_user),
    db:      AsyncSession = Depends(get_db),
):
    """
    Initialise a Paystack transaction for the authenticated user.
    Returns { ok, authorization_url, reference }.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    from USERS.payment import init_payment as _init
    data = await _init(user_id=user.id, user_email=user.email, plan=plan, db=db)
    return data


@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(..., alias="x-paystack-signature"),
    db: AsyncSession = Depends(get_db),
):
    """
    Paystack calls this after a successful payment.
    Verified by HMAC-SHA512 signature — grants premium only here.
    """
    body = await request.body()
    from USERS.payment import handle_webhook
    return await handle_webhook(body=body, signature=x_paystack_signature, db=db)
