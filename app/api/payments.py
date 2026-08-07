"""
api/payments.py — Paystack webhook handler.

Paystack sends a POST to this endpoint after a successful charge.
We validate the HMAC-SHA512 signature, then update the user's premium status.

Setup:
  1. In your Paystack dashboard → Settings → API Keys & Webhooks
     set webhook URL to: https://your-backend.com/api/v1/payments/webhook
  2. Add PAYSTACK_SECRET_KEY to your .env / Render environment variables.
  3. Include this router in main.py:
       from app.api.payments import router as payments_router
       app.include_router(payments_router)

The webhook endpoint is intentionally excluded from X-Reelz-Token auth
(Paystack cannot send our token). It is protected by HMAC signature instead.
Add "/api/v1/payments/webhook" to _OPEN_PATHS in main.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(None, alias="x-paystack-signature"),
):
    """
    Receive and validate Paystack charge.success events.

    Paystack docs: https://paystack.com/docs/payments/webhooks/
    Signature: HMAC-SHA512 of the raw request body, keyed with PAYSTACK_SECRET_KEY.
    """
    raw_body = await request.body()

    # ── 1. Signature validation ────────────────────────────────────────────────
    if not _settings.paystack_secret_key:
        log.error("PAYSTACK_SECRET_KEY not configured — rejecting webhook")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    if not x_paystack_signature:
        log.warning("Paystack webhook received without signature header")
        raise HTTPException(status_code=400, detail="Missing signature")

    expected_sig = hmac.new(
        _settings.paystack_secret_key.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, x_paystack_signature):
        log.warning("Paystack webhook signature mismatch — possible forgery")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # ── 2. Parse event ─────────────────────────────────────────────────────────
    try:
        import orjson
        payload = orjson.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    data  = payload.get("data", {})

    log.info(f"Paystack webhook event: {event}")

    # ── 3. Handle charge.success ───────────────────────────────────────────────
    if event == "charge.success":
        customer   = data.get("customer", {})
        email      = customer.get("email", "").lower().strip()
        amount_ngn = data.get("amount", 0) // 100  # Paystack sends kobo

        if not email:
            log.error("charge.success webhook missing customer email")
            # Return 200 so Paystack doesn't retry — this is a data problem, not a server problem
            return JSONResponse({"status": "ignored", "reason": "no email"})

        # Determine plan from amount
        premium_days = _resolve_premium_days(amount_ngn)

        log.info(f"Granting {premium_days} days premium to {email} (paid ₦{amount_ngn})")
        await _grant_premium(email, premium_days)

    # Always return 200 to acknowledge receipt — Paystack retries on non-2xx
    return JSONResponse({"status": "ok"})


def _resolve_premium_days(amount_ngn: int) -> int:
    """Map payment amount to premium duration in days."""
    # Match your Paystack payment page prices (configured in reelz_config.json).
    # Amounts have a 10% tolerance for currency fluctuation / test transactions.
    if amount_ngn >= 10_000:   # ₦12,000 yearly plan
        return 365
    if amount_ngn >= 1_000:    # ₦1,500 monthly plan
        return 31
    # Default — could be a partial payment or test charge
    return 31


async def _grant_premium(email: str, days: int) -> None:
    """
    Update the user's premium status in your database.

    TODO: Wire this to your actual user/session storage.
    Currently logs only — replace with a real DB update.

    Example (if using SQLAlchemy):
        from datetime import datetime, timedelta
        user = await db.query(User).filter(User.email == email).first()
        if user:
            user.is_premium = True
            user.premium_expires_at = datetime.utcnow() + timedelta(days=days)
            await db.commit()

    Example (if using Supabase REST):
        await supabase.table("users").update({
            "is_premium": True,
            "premium_expires_at": (datetime.utcnow() + timedelta(days=days)).isoformat()
        }).eq("email", email).execute()
    """
    log.info(
        f"[WEBHOOK] grant_premium: email={email} days={days} "
        f"— STUB: wire _grant_premium() to your user database"
    )
