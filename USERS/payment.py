"""
USERS/payment.py — Paystack payment initialisation + webhook verification.

Response: { ok, authorization_url, reference }
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.models import User, Payment
from config import get_settings

_s = get_settings()

_PLAN_AMOUNTS: dict[str, int] = {
    "monthly": _s.premium_monthly_price or 200_000,
    "yearly":  (_s.premium_monthly_price or 200_000) * 10,
}

_PLAN_DURATION_MS: dict[str, int] = {
    "monthly": 30  * 24 * 3600 * 1000,
    "yearly":  365 * 24 * 3600 * 1000,
}


async def init_payment(user_id: str, user_email: str, plan: str, db: AsyncSession) -> dict:
    """Initialize a Paystack transaction. Returns schema v3 response."""
    if plan not in _PLAN_AMOUNTS:
        raise HTTPException(status_code=400, detail=f"Invalid plan '{plan}'. Use 'monthly' or 'yearly'.")

    if not _s.paystack_secret_key:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    amount    = _PLAN_AMOUNTS[plan]
    reference = f"reelz_txn_{uuid.uuid4().hex[:16]}"

    payload = {
        "email":     user_email,
        "amount":    amount,
        "currency":  "NGN",
        "reference": reference,
        "metadata":  {"user_id": user_id, "plan": plan},
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{_s.paystack_base_url}/transaction/initialize",
                json=payload,
                headers={
                    "Authorization": f"Bearer {_s.paystack_secret_key}",
                    "Content-Type":  "application/json",
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Payment gateway unreachable: {e}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Paystack initialization failed")

    data    = r.json().get("data", {})
    auth_url = data.get("authorization_url", "")

    payment = Payment(
        user_id=user_id,
        reference=reference,
        plan=plan,
        amount=amount,
        status="pending",
    )
    db.add(payment)
    await db.flush()

    return {
        "ok":                True,
        "authorization_url": auth_url,
        "reference":         reference,
    }


def _verify_paystack_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        _s.paystack_webhook_secret.encode(),
        body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(body: bytes, signature: str, db: AsyncSession) -> dict:
    if not _verify_paystack_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json
    event      = json.loads(body)
    event_type = event.get("event", "")

    if event_type != "charge.success":
        return {"ok": True, "handled": False}

    data      = event.get("data", {})
    reference = data.get("reference", "")
    status    = data.get("status", "")

    if status != "success" or not reference:
        return {"ok": True, "handled": False}

    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()
    if payment is None:
        return {"ok": True, "handled": False, "reason": "unknown reference"}

    if payment.status == "success":
        return {"ok": True, "handled": True, "reason": "already processed"}

    payment.status      = "success"
    payment.verified_at = int(time.time() * 1000)

    user_result = await db.execute(select(User).where(User.id == payment.user_id))
    user = user_result.scalar_one_or_none()

    if user:
        now = int(time.time() * 1000)
        current_expiry = max(user.premium_expires_at or 0, now)
        user.is_premium         = True
        user.plan               = payment.plan
        user.premium_expires_at = current_expiry + _PLAN_DURATION_MS.get(payment.plan, 0)

    await db.flush()
    return {"ok": True, "handled": True}
