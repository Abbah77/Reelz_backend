"""
api/admin.py — Full-access admin panel endpoint.

Protected by X-Admin-Token header (ADMIN_SECRET_KEY in config / .env).
This is your private remote control for everything — providers, users,
cache, config, analytics, payments, circuit breakers, and more.

All endpoints are thin routes. Business logic stays in managers / queries.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from api.envelope import ok, err
from api.cache_headers import set_cache
from config import get_settings

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
_s = get_settings()


# ── Admin token guard ─────────────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    """Dependency — blocks every admin route if the token is wrong/missing."""
    key = _s.admin_secret_key
    if not key:
        raise HTTPException(status_code=503, detail="Admin key not configured")
    token = request.headers.get("X-Admin-Token", "")
    if token != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


AdminDep = Depends(_require_admin)


# ── Pydantic bodies ───────────────────────────────────────────────────────────

class ConfigPatch(BaseModel):
    key: str
    value: Any


class UserPremiumPatch(BaseModel):
    is_premium: bool
    plan: Optional[str] = "monthly"   # none | monthly | yearly
    expires_at: Optional[int] = None  # unix ms; 0 = never expires


class UserDeleteConfirm(BaseModel):
    confirm: bool = False


class CacheDeleteBody(BaseModel):
    key: str


class ProviderMoveBody(BaseModel):
    target: str  # "active" | "disabled"


# ── Overview (dashboard aggregate) ────────────────────────────────────────────

@router.get("/overview")
async def overview(response: Response, _: None = AdminDep):
    """Single fat call — powers the admin dashboard home screen."""
    from ENGINE.providers.Stream.registry   import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as dl_all
    from ENGINE.providers.Subtitle.registry import get_all as sub_all
    from ENGINE.providers.Shorts.registry   import get_all as shorts_all
    from ENGINE.manager.health import get_stats, is_circuit_open
    from ENGINE.cache.cache    import get_stats as cache_stats
    from USERS.db              import SessionLocal
    from USERS.models          import User, Payment
    from sqlalchemy            import select, func

    # ── Providers ─────────────────────────────────────────────────────────────
    all_p = (
        [(p, "stream")   for p in stream_all()]
        + [(p, "download") for p in dl_all()]
        + [(p, "subtitle") for p in sub_all()]
        + [(p, "shorts")   for p in shorts_all()]
    )
    p_total   = len(all_p)
    p_broken  = sum(1 for p, _ in all_p if await is_circuit_open(p.id))
    p_healthy = p_total - p_broken

    # ── Users ─────────────────────────────────────────────────────────────────
    now_ms = int(time.time() * 1000)
    day_ms = 86_400_000
    try:
        async with SessionLocal() as s:
            u_total   = (await s.execute(select(func.count()).select_from(User))).scalar_one()
            u_premium = (await s.execute(select(func.count()).select_from(User).where(User.is_premium == True))).scalar_one()
            u_active24 = (await s.execute(
                select(func.count()).select_from(User).where(User.updated_at >= now_ms - day_ms)
            )).scalar_one()
            u_active7d = (await s.execute(
                select(func.count()).select_from(User).where(User.updated_at >= now_ms - 7 * day_ms)
            )).scalar_one()
            open_feedback = 0  # feedback table optional — skip if not present
    except Exception:
        u_total = u_premium = u_active24 = u_active7d = 0
        open_feedback = 0

    # ── Analytics (health events) ─────────────────────────────────────────────
    from ENGINE.manager.health import _get_raw_stats as _stats_fn  # in-memory stats store
    now = time.time()
    req_1h = req_24h = err_1h = stream_1h = dl_1h = sub_1h = shorts_1h = 0
    by_ep_1h: dict[str, int] = {}
    for pid, stat in _stats_fn().items():
        evs = [e for e in stat.get("events", []) if now - e.get("ts", 0) < 3600]
        req_1h  += len(evs)
        err_1h  += sum(1 for e in evs if e.get("outcome") == "failed")
        evs24   = [e for e in stat.get("events", []) if now - e.get("ts", 0) < 86400]
        req_24h += len(evs24)
        ptype = pid.split("-")[0] if "-" in pid else "stream"

    # Per-type breakdown from raw events across all providers
    for pid, stat in _stats_fn().items():
        for e in stat.get("events", []):
            if now - e.get("ts", 0) < 3600:
                ep = e.get("endpoint", "stream")
                by_ep_1h[ep] = by_ep_1h.get(ep, 0) + 1

    cs = await cache_stats()

    set_cache(response, None)
    return ok({
        "providers": {
            "total":   p_total,
            "healthy": p_healthy,
            "broken":  p_broken,
            "disabled": 0,
        },
        "users": {
            "total":     u_total,
            "premium":   u_premium,
            "free":      u_total - u_premium,
            "active_24h": u_active24,
            "active_7d":  u_active7d,
        },
        "analytics": {
            "requests_1h":   req_1h,
            "requests_24h":  req_24h,
            "error_rate_1h": round(err_1h / max(req_1h, 1), 4),
            "stream_1h":     by_ep_1h.get("stream", 0),
            "download_1h":   by_ep_1h.get("download", 0),
            "subtitle_1h":   by_ep_1h.get("subtitle", 0),
            "shorts_1h":     by_ep_1h.get("shorts", 0),
            "by_endpoint_1h": by_ep_1h,
        },
        "open_feedback": open_feedback,
        "cache": {
            "backend": cs.get("backend", _s.cache_backend),
            "total":   cs.get("total", 0),
            "live":    cs.get("live", 0),
        },
        "ts": int(now),
    })


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def analytics(response: Response, _: None = AdminDep):
    from ENGINE.manager.health import _get_raw_stats as _stats_fn
    now = time.time()

    by_ep: dict[str, dict] = {}
    total_1h = total_24h = 0

    for pid, stat in _stats_fn().items():
        events = stat.get("events", [])
        for e in events:
            age = now - e.get("ts", 0)
            ep  = e.get("endpoint", "stream")
            if ep not in by_ep:
                by_ep[ep] = {"count_1h": 0, "count_24h": 0, "errors_1h": 0, "ok_1h": 0}
            if age < 3600:
                by_ep[ep]["count_1h"] += 1
                total_1h += 1
                if e.get("outcome") == "failed":
                    by_ep[ep]["errors_1h"] += 1
                else:
                    by_ep[ep]["ok_1h"] += 1
            if age < 86400:
                by_ep[ep]["count_24h"] += 1
                total_24h += 1

    # Provider success rate leaderboard (last 1h)
    leaderboard = []
    for pid, stat in _stats_fn().items():
        evs = [e for e in stat.get("events", []) if now - e.get("ts", 0) < 3600]
        if not evs:
            continue
        success = sum(1 for e in evs if e.get("outcome") == "found")
        leaderboard.append({
            "id":      pid,
            "runs":    len(evs),
            "success": success,
            "rate":    round(success / len(evs), 3),
            "avg_ms":  round(sum(e.get("ms", 0) for e in evs) / len(evs)),
        })
    leaderboard.sort(key=lambda x: x["rate"], reverse=True)

    set_cache(response, None)
    return ok({
        "by_endpoint":    by_ep,
        "total_1h":       total_1h,
        "total_24h":      total_24h,
        "provider_leaderboard": leaderboard[:20],
    })


# ── Providers ─────────────────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers(response: Response, _: None = AdminDep):
    from ENGINE.providers.Stream.registry   import get_all as stream_all,   ACTIVE as sa, DISABLED as sd
    from ENGINE.providers.Download.registry import get_all as dl_all,       ACTIVE as da, DISABLED as dd
    from ENGINE.providers.Subtitle.registry import get_all as sub_all,      ACTIVE as suba, DISABLED as subd
    from ENGINE.providers.Shorts.registry   import get_all as shorts_all,   ACTIVE as sha, DISABLED as shd
    from ENGINE.manager.health import get_stats, is_circuit_open

    active_ids   = {p.id for p in sa + da + suba + sha}
    disabled_ids = {p.id for p in sd + dd + subd + shd}

    def _ptype(pid: str) -> str:
        n = int(pid.split("-")[1]) if "-" in pid else 0
        if n <= 99:   return "stream"
        if n <= 199:  return "download"
        if n <= 299:  return "subtitle"
        return "shorts"

    result = []
    for p in stream_all() + dl_all() + sub_all() + shorts_all():
        stats = await get_stats(p.id)
        broken = await is_circuit_open(p.id)
        result.append({
            "id":       p.id,
            "name":     p.name,
            "type":     _ptype(p.id),
            "active":   p.id in active_ids,
            "disabled": p.id in disabled_ids,
            "broken":   broken,
            **stats,
        })

    set_cache(response, None)
    return ok({"providers": result})


@router.post("/providers/{provider_id}/reset")
async def reset_provider(provider_id: str, response: Response, _: None = AdminDep):
    from ENGINE.manager.health import reset
    await reset(provider_id)
    set_cache(response, None)
    return ok({"provider_id": provider_id, "reset": True})


# ── Cache ─────────────────────────────────────────────────────────────────────

@router.get("/cache")
async def cache_info(response: Response, _: None = AdminDep):
    from ENGINE.cache.cache import get_stats as cs
    stats = await cs()
    set_cache(response, None)
    return ok(stats)


@router.post("/cache/flush")
async def flush_cache(response: Response, _: None = AdminDep):
    """Flush entire cache."""
    try:
        from ENGINE.cache.cache import _store
        if hasattr(_store, "flush"):
            await _store.flush()
        elif hasattr(_store, "_cache"):
            _store._cache.clear()
        flushed = True
    except Exception as exc:
        return err("Flush failed: " + str(exc))
    set_cache(response, None)
    return ok({"flushed": flushed})


@router.delete("/cache/key")
async def delete_cache_key(body: CacheDeleteBody, response: Response, _: None = AdminDep):
    from ENGINE.cache.cache import delete
    await delete(body.key)
    set_cache(response, None)
    return ok({"deleted": body.key})


@router.get("/cache/keys")
async def list_cache_keys(response: Response, _: None = AdminDep):
    """List all live cache keys (memory backend only)."""
    try:
        from ENGINE.cache.cache import _store
        if hasattr(_store, "_cache"):
            keys = list(_store._cache.keys())
        else:
            keys = []
    except Exception:
        keys = []
    set_cache(response, None)
    return ok({"keys": keys, "count": len(keys)})


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    response: Response,
    _: None = AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    premium_only: bool = Query(False),
    search: Optional[str] = Query(None),
):
    from USERS.db    import SessionLocal
    from USERS.models import User
    from sqlalchemy   import select, func

    try:
        async with SessionLocal() as s:
            q = select(User)
            if premium_only:
                q = q.where(User.is_premium == True)
            if search:
                q = q.where(
                    User.email.ilike(f"%{search}%") |
                    User.name.ilike(f"%{search}%")
                )
            total_q = select(func.count()).select_from(q.subquery())
            total   = (await s.execute(total_q)).scalar_one()
            users   = (await s.execute(q.order_by(User.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        now_ms = int(time.time() * 1000)
        rows = [
            {
                "id":          u.id,
                "email":       u.email,
                "name":        u.name,
                "photo_url":   u.photo_url,
                "is_premium":  u.is_premium,
                "premium_active": u.is_premium_active(),
                "plan":        u.plan,
                "premium_expires_at": u.premium_expires_at,
                "status":      u.status,
                "created_at":  u.created_at,
                "updated_at":  u.updated_at,
            }
            for u in users
        ]
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({"users": rows, "total": total, "offset": offset, "limit": limit})


@router.get("/users/stats")
async def user_stats(response: Response, _: None = AdminDep):
    from USERS.db    import SessionLocal
    from USERS.models import User
    from sqlalchemy   import select, func

    now_ms = int(time.time() * 1000)
    day_ms = 86_400_000
    try:
        async with SessionLocal() as s:
            total    = (await s.execute(select(func.count()).select_from(User))).scalar_one()
            premium  = (await s.execute(select(func.count()).select_from(User).where(User.is_premium == True))).scalar_one()
            act24h   = (await s.execute(select(func.count()).select_from(User).where(User.updated_at >= now_ms - day_ms))).scalar_one()
            act7d    = (await s.execute(select(func.count()).select_from(User).where(User.updated_at >= now_ms - 7 * day_ms))).scalar_one()
            new7d    = (await s.execute(select(func.count()).select_from(User).where(User.created_at >= now_ms - 7 * day_ms))).scalar_one()
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({
        "total":      total,
        "premium":    premium,
        "free":       total - premium,
        "active_24h": act24h,
        "active_7d":  act7d,
        "new_7d":     new7d,
    })


@router.get("/users/{user_id}")
async def get_user(user_id: str, response: Response, _: None = AdminDep):
    from USERS.db    import SessionLocal
    from USERS.models import User, Payment
    from sqlalchemy   import select

    try:
        async with SessionLocal() as s:
            u = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return err("User not found")
            pays = (await s.execute(
                select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc()).limit(20)
            )).scalars().all()
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({
        "user": {
            "id":          u.id,
            "google_sub":  u.google_sub,
            "email":       u.email,
            "name":        u.name,
            "photo_url":   u.photo_url,
            "is_premium":  u.is_premium,
            "premium_active": u.is_premium_active(),
            "plan":        u.plan,
            "premium_expires_at": u.premium_expires_at,
            "status":      u.status,
            "created_at":  u.created_at,
            "updated_at":  u.updated_at,
        },
        "payments": [
            {
                "id":          p.id,
                "reference":   p.reference,
                "plan":        p.plan,
                "amount":      p.amount,
                "currency":    p.currency,
                "status":      p.status,
                "created_at":  p.created_at,
                "verified_at": p.verified_at,
            }
            for p in pays
        ],
    })


@router.patch("/users/{user_id}/premium")
async def patch_user_premium(
    user_id: str,
    body: UserPremiumPatch,
    response: Response,
    _: None = AdminDep,
):
    """Grant or revoke premium for any user instantly."""
    from USERS.db    import SessionLocal
    from USERS.models import User
    from sqlalchemy   import select

    now_ms = int(time.time() * 1000)
    try:
        async with SessionLocal() as s:
            u = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return err("User not found")
            u.is_premium = body.is_premium
            u.plan        = body.plan if body.is_premium else "none"
            if body.is_premium:
                if body.expires_at is not None:
                    u.premium_expires_at = body.expires_at
                else:
                    # Default: 30 days from now
                    u.premium_expires_at = now_ms + 30 * 86_400_000
            else:
                u.premium_expires_at = now_ms - 1  # expired immediately
            u.updated_at = now_ms
            await s.commit()
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({"user_id": user_id, "is_premium": body.is_premium, "plan": body.plan})


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, body: UserDeleteConfirm, response: Response, _: None = AdminDep):
    """Permanently delete a user and all their payments."""
    if not body.confirm:
        return err("Set confirm=true to delete")
    from USERS.db    import SessionLocal
    from USERS.models import User
    from sqlalchemy   import select, delete as sql_delete

    try:
        async with SessionLocal() as s:
            u = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return err("User not found")
            await s.delete(u)
            await s.commit()
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({"deleted": user_id})


# ── Payments ──────────────────────────────────────────────────────────────────

@router.get("/payments")
async def list_payments(
    response: Response,
    _: None = AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
):
    from USERS.db    import SessionLocal
    from USERS.models import Payment, User
    from sqlalchemy   import select, func

    try:
        async with SessionLocal() as s:
            q = select(Payment)
            if status:
                q = q.where(Payment.status == status)
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            pays  = (await s.execute(q.order_by(Payment.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    except Exception as exc:
        return err("DB error: " + str(exc))

    set_cache(response, None)
    return ok({
        "payments": [
            {
                "id":          p.id,
                "user_id":     p.user_id,
                "reference":   p.reference,
                "plan":        p.plan,
                "amount":      p.amount,
                "currency":    p.currency,
                "status":      p.status,
                "created_at":  p.created_at,
                "verified_at": p.verified_at,
            }
            for p in pays
        ],
        "total":  total,
        "offset": offset,
        "limit":  limit,
    })


# ── Config (read + live patch) ────────────────────────────────────────────────

@router.get("/config")
async def admin_get_config(response: Response, _: None = AdminDep):
    """Returns the full current settings (everything in config.py)."""
    s = get_settings()
    set_cache(response, None)
    return ok({
        # Server
        "debug":                  s.debug,
        "host":                   s.host,
        "port":                   s.port,
        "cors_origins":           s.cors_origins,
        # Cache
        "cache_backend":          s.cache_backend,
        "cache_ttl_seconds":      s.cache_ttl_seconds,
        # Providers
        "provider_timeout_ms":    s.provider_timeout_ms,
        "cb_fail_threshold":      s.cb_fail_threshold,
        "cb_cooldown_seconds":    s.cb_cooldown_seconds,
        # WARP
        "warp_mode":              s.warp_mode,
        "warp_proxy_url":         s.warp_proxy_url,
        "flaresolverr_url":       s.flaresolverr_url,
        # Feature flags
        "shorts_enabled":         s.shorts_enabled,
        "downloads_enabled":      s.downloads_enabled,
        "force_maintenance":      s.force_maintenance,
        "maintenance_message":    s.maintenance_message,
        # Premium
        "premium_enabled":        s.premium_enabled,
        "premium_monthly_price":  s.premium_monthly_price,
        "premium_yearly_price":   s.premium_yearly_price,
        "paystack_monthly_url":   s.paystack_monthly_url,
        "paystack_yearly_url":    s.paystack_yearly_url,
        "premium_payment_note":   s.premium_payment_note,
        # Ads
        "ads_enabled":            s.ads_enabled,
        "applovin_sdk_key":       s.applovin_sdk_key,
        "ads_banner_id":          s.ads_banner_id,
        "ads_interstitial_id":    s.ads_interstitial_id,
        "ads_rewarded_id":        s.ads_rewarded_id,
        "ads_native_id":          s.ads_native_id,
        # App versioning
        "app_version":            s.app_version,
        "min_app_version":        s.min_app_version,
        "latest_app_version":     s.latest_app_version,
        "latest_apk_url":         s.latest_apk_url,
        # Feed
        "feed_cache_ttl_seconds": s.feed_cache_ttl_seconds,
        "feed_section_limit":     s.feed_section_limit,
        # Database
        "database_url":           s.database_url,
        # Provider-specific
        "hdrezka_base_url":       s.hdrezka_base_url,
        "anizone_base_url":       s.anizone_base_url,
        "consumet_url":           s.consumet_url,
        # Debrid
        "realdebrid_key":         "***" if s.realdebrid_key else "",
        "alldebrid_key":          "***" if s.alldebrid_key else "",
        "torbox_key":             "***" if s.torbox_key else "",
    })


@router.patch("/config")
async def admin_patch_config(body: ConfigPatch, response: Response, _: None = AdminDep):
    """
    Live-patch a config value in-memory (no restart needed for flags).
    Persists for the lifetime of the process — set it in .env for durability.
    """
    s = get_settings()
    allowed = {
        "debug", "shorts_enabled", "downloads_enabled", "force_maintenance",
        "maintenance_message", "premium_enabled", "premium_monthly_price",
        "premium_yearly_price", "ads_enabled", "warp_mode",
        "provider_timeout_ms", "cb_fail_threshold", "cb_cooldown_seconds",
        "cache_ttl_seconds", "feed_section_limit", "feed_cache_ttl_seconds",
        "app_version", "min_app_version", "latest_app_version", "latest_apk_url",
        "hdrezka_base_url", "anizone_base_url", "consumet_url",
        "paystack_monthly_url", "paystack_yearly_url", "premium_payment_note",
        "applovin_sdk_key", "ads_banner_id", "ads_interstitial_id",
        "ads_rewarded_id", "ads_native_id",
    }
    if body.key not in allowed:
        return err(f"Key '{body.key}' is not patchable live")
    try:
        setattr(s, body.key, body.value)
    except Exception as exc:
        return err(str(exc))
    set_cache(response, None)
    return ok({"key": body.key, "value": body.value, "live": True})


# ── Health / providers detail ─────────────────────────────────────────────────

@router.get("/health")
async def admin_health(response: Response, _: None = AdminDep):
    from ENGINE.providers.Stream.registry   import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as dl_all
    from ENGINE.providers.Subtitle.registry import get_all as sub_all
    from ENGINE.providers.Shorts.registry   import get_all as shorts_all
    from ENGINE.manager.health import get_stats, get_insights, is_circuit_open

    result = []
    for p in stream_all() + dl_all() + sub_all() + shorts_all():
        stats   = await get_stats(p.id)
        insight = await get_insights(p.id, p.name)
        result.append({
            "id":            p.id,
            "name":          p.name,
            "circuit_open":  await is_circuit_open(p.id),
            "score":         insight.get("score", {}),
            "explanation":   insight.get("explanation", ""),
            "anomalies":     insight.get("anomalies", []),
            **stats,
        })

    result.sort(key=lambda x: x["score"].get("composite", 0), reverse=True)
    set_cache(response, None)
    return ok({"providers": result})


# ── Logs / events ──────────────────────────────────────────────────────────────

@router.get("/logs")
async def admin_logs(
    response: Response,
    _: None = AdminDep,
    window: int = Query(3600, description="Window in seconds (default 1h)"),
    outcome: Optional[str] = Query(None, description="found|empty|failed"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Return raw provider events as a log stream."""
    from ENGINE.manager.health import _get_raw_stats as _stats_fn
    now = time.time()

    events = []
    for pid, stat in _stats_fn().items():
        for e in stat.get("events", []):
            if now - e.get("ts", 0) > window:
                continue
            if outcome and e.get("outcome") != outcome:
                continue
            events.append({
                "provider_id": pid,
                "ts":       e.get("ts"),
                "outcome":  e.get("outcome"),
                "ms":       e.get("ms"),
                "category": e.get("category", "unknown"),
                "http_status": e.get("http_status", 0),
                "quality_count": e.get("quality_count", 0),
                "has_m3u8":  e.get("has_m3u8", False),
            })

    events.sort(key=lambda x: x.get("ts", 0), reverse=True)
    events = events[:limit]

    set_cache(response, None)
    return ok({"events": events, "count": len(events), "window_seconds": window})


# ── Requests (recent activity) ────────────────────────────────────────────────

@router.get("/requests")
async def recent_requests(response: Response, _: None = AdminDep):
    """Summarised per-provider request history for the last hour."""
    from ENGINE.manager.health import _get_raw_stats as _stats_fn
    now = time.time()

    rows = []
    for pid, stat in _stats_fn().items():
        evs = [e for e in stat.get("events", []) if now - e.get("ts", 0) < 3600]
        if not evs:
            continue
        found  = sum(1 for e in evs if e.get("outcome") == "found")
        failed = sum(1 for e in evs if e.get("outcome") == "failed")
        empty  = sum(1 for e in evs if e.get("outcome") == "empty")
        avg_ms = sum(e.get("ms", 0) for e in evs) / len(evs)
        rows.append({
            "provider_id": pid,
            "total": len(evs),
            "found": found,
            "empty": empty,
            "failed": failed,
            "success_rate": round(found / len(evs), 3),
            "avg_ms": round(avg_ms),
            "last_ts": max(e.get("ts", 0) for e in evs),
        })

    rows.sort(key=lambda x: x["last_ts"], reverse=True)
    set_cache(response, None)
    return ok({"requests": rows})


# ── Insights ──────────────────────────────────────────────────────────────────

@router.get("/insights")
async def admin_insights(
    response: Response,
    _: None = AdminDep,
    category: Optional[str] = Query(None),
):
    from ENGINE.providers.Stream.registry   import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as dl_all
    from ENGINE.providers.Subtitle.registry import get_all as sub_all
    from ENGINE.manager.health import get_insights, ContentCategory

    cat: Optional[ContentCategory] = category if category in ("anime", "asian", "bollywood", "movie", "tv") else None  # type: ignore[assignment]

    all_p = list(stream_all()) + list(dl_all()) + list(sub_all())
    results = []
    for p in all_p:
        insight = await get_insights(p.id, p.name, cat)
        results.append(insight)

    results.sort(key=lambda x: x["score"]["composite"], reverse=True)
    set_cache(response, None)
    return ok({"providers": results, "category_filter": cat})


# ── Server info ────────────────────────────────────────────────────────────────

@router.get("/server")
async def server_info(response: Response, _: None = AdminDep):
    import sys, platform
    set_cache(response, None)
    return ok({
        "python":   sys.version,
        "platform": platform.platform(),
        "pid":      __import__("os").getpid(),
        "uptime":   int(time.time()),
        "backend":  _s.cache_backend,
        "db":       _s.database_url.split("://")[0] if "://" in _s.database_url else _s.database_url,
        "warp":     _s.warp_mode,
        "debug":    _s.debug,
    })
