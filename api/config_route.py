"""
api/config_route.py — GET /config

Returns AppConfigDto consumed by the Android app on every launch.
No auth required — the app fetches this before it has a token.
Response is wrapped in the standard envelope and cached at the HTTP level.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import get_settings
from api.envelope import ok

router = APIRouter(tags=["Config"])
_s = get_settings()

_CONFIG_TTL_MS = 3_600_000  # 1 hour


@router.get("/config")
async def get_config():
    """
    App configuration gate. The Android app calls this first on every launch.
    Controls feature flags, ad unit IDs, premium pricing, and app versioning.
    """
    payload = ok(
        data={
            "version":                 _s.app_version,
            "min_app_version":         _s.min_app_version,
            "latest_app_version":      _s.latest_app_version,
            "latest_apk_url":          _s.latest_apk_url,
            "force_maintenance":       _s.force_maintenance,
            "maintenance_message":     _s.maintenance_message,
            "shorts_enabled":          _s.shorts_enabled,
            "downloads_enabled":       _s.downloads_enabled,
            "search_min_chars":        2,
            "guest_streaming_enabled": True,
            "premium": {
                "enabled":              _s.premium_enabled,
                "monthly_price":        _s.premium_monthly_price,
                "paystack_monthly_url": _s.paystack_monthly_url,
                "paystack_yearly_url":  _s.paystack_yearly_url,
            },
            "ads": {
                "enabled":          _s.ads_enabled,
                "applovin_sdk_key": _s.applovin_sdk_key,
                "banner_id":        _s.ads_banner_id,
                "interstitial_id":  _s.ads_interstitial_id,
                "rewarded_id":      _s.ads_rewarded_id,
                "native_id":        _s.ads_native_id,
                "placements": {
                    "banner_enabled":       True,
                    "interstitial_enabled": True,
                    "native_enabled":       True,
                    "preroll_enabled":      False,
                },
                "frequency": {
                    "content_opens_before_first": 3,
                    "every_n_plays":              3,
                    "min_ms_between":             60_000,
                    "max_per_session":            10,
                },
            },
        },
        cache_ttl_ms=_CONFIG_TTL_MS,
    )

    response = JSONResponse(content=payload)
    # Cache for 1 hour at CDN/browser level; stale-while-revalidate 10 min
    response.headers["Cache-Control"] = "no-store"
    return response
