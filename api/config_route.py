"""
api/config_route.py — GET /config

Returns AppConfigDto consumed by the Android app on every launch.
No auth required — the app fetches this before it has a token.
Response is cached at the HTTP level via Cache-Control headers.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import get_settings

router = APIRouter(tags=["Config"])
_s = get_settings()


@router.get("/config")
async def get_config():
    """
    App configuration gate. The Android app calls this first on every launch.
    Controls feature flags, ad unit IDs, premium pricing, and app versioning.
    """
    payload = {
        "version":             _s.app_version,
        "backend_token":       _s.app_secret_token,   # app injects this as X-Reelz-Token
        "shorts_enabled":      _s.shorts_enabled,
        "downloads_enabled":   _s.downloads_enabled,
        "force_maintenance":   _s.force_maintenance,
        "maintenance_message": _s.maintenance_message,
        "min_app_version":     _s.min_app_version,
        "latest_app_version":  _s.latest_app_version,
        "latest_apk_url":      _s.latest_apk_url,
        "premium": {
            "enabled":               _s.premium_enabled,
            "monthly_price":         _s.premium_monthly_price,
            "paystack_monthly_url":  _s.paystack_monthly_url,
            "paystack_yearly_url":   _s.paystack_yearly_url,
        },
        "ads": {
            "enabled":               _s.ads_enabled,
            "applovin_sdk_key":      _s.applovin_sdk_key,
            "mediation_provider":    _s.ads_mediation_provider,
            "banner_id":             _s.ads_banner_id,
            "interstitial_id":       _s.ads_interstitial_id,
            "rewarded_id":           _s.ads_rewarded_id,
            "native_id":             _s.ads_native_id,
            "app_open_id":           _s.ads_app_open_id,
            "vast_tag_url":          _s.ads_vast_tag_url,
            "placements": {
                "banner_enabled":       True,
                "interstitial_enabled": True,
                "rewarded_enabled":     True,
                "native_enabled":       True,
                "app_open_enabled":     False,
                "preroll_enabled":      False,
            },
            "interstitial_frequency": {
                "min_content_opens":        3,
                "min_interval_ms":          60_000,
                "content_opens_before_first": 3,
                "every_n_plays":            3,
                "min_ms_between":           60_000,
                "max_per_session":          10,
            },
            "preroll": {
                "skip_on_resume":         True,
                "skip_on_quality_switch": True,
                "show_on_movies_only":    False,
                "min_minutes_between":    30,
            },
            "network": {
                "banner_id":       _s.ads_banner_id,
                "interstitial_id": _s.ads_interstitial_id,
                "rewarded_id":     _s.ads_rewarded_id,
                "native_id":       _s.ads_native_id,
                "app_open_id":     _s.ads_app_open_id,
                "vast_tag_url":    _s.ads_vast_tag_url,
            },
        },
    }

    response = JSONResponse(content=payload)
    # Cache for 1 hour at CDN/browser level; stale-while-revalidate 10 min
    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=600"
    return response
