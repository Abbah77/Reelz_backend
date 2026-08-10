"""
ENGINE/tools/anticaptcha.py — CAPTCHA solving plugin.

Supports AntiCaptcha and 2Captcha. Auto-selects based on which key is set.

Configure in .env:
    ANTICAPTCHA_KEY=your_key
    TWOCAPTCHA_KEY=your_key

Usage:
    from ENGINE.tools.anticaptcha import solve_recaptcha_v2, solve_hcaptcha

    token = await solve_recaptcha_v2("site_key", "https://page_url.com")
    if token:
        # submit form with token
        ...
"""
from __future__ import annotations

import asyncio
from typing import Optional
import httpx
from config import get_settings

_s = get_settings()
_POLL = 3
_MAX_POLLS = 30


async def _anticaptcha(task: dict) -> Optional[str]:
    key = _s.anticaptcha_key
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post("https://api.anti-captcha.com/createTask",
                             json={"clientKey": key, "task": task})
            data = r.json()
            if data.get("errorId"):
                return None
            task_id = data["taskId"]

        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post("https://api.anti-captcha.com/getTaskResult",
                                 json={"clientKey": key, "taskId": task_id})
                data = r.json()
            if data.get("errorId"):
                return None
            if data.get("status") == "ready":
                sol = data.get("solution", {})
                return sol.get("gRecaptchaResponse") or sol.get("token")
    except Exception:
        return None
    return None


async def _twocaptcha(params: dict) -> Optional[str]:
    key = _s.twocaptcha_key
    if not key:
        return None
    params.update({"key": key, "json": 1})
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://2captcha.com/in.php", params=params)
            data = r.json()
            if data.get("status") != 1:
                return None
            cid = data["request"]

        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://2captcha.com/res.php",
                                params={"key": key, "action": "get", "id": cid, "json": 1})
                data = r.json()
            if data.get("status") == 1:
                return data.get("request")
            if data.get("request") != "CAPCHA_NOT_READY":
                return None
    except Exception:
        return None
    return None


async def solve_recaptcha_v2(site_key: str, page_url: str) -> Optional[str]:
    """Solve reCAPTCHA v2. Returns token or None."""
    if _s.anticaptcha_key:
        return await _anticaptcha({
            "type": "NoCaptchaTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
        })
    return await _twocaptcha({"method": "userrecaptcha", "googlekey": site_key, "pageurl": page_url})


async def solve_recaptcha_v3(site_key: str, page_url: str, action: str = "verify") -> Optional[str]:
    """Solve reCAPTCHA v3. Returns token or None."""
    if _s.anticaptcha_key:
        return await _anticaptcha({
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": 0.3,
        })
    return await _twocaptcha({
        "method": "userrecaptcha", "version": "v3",
        "googlekey": site_key, "pageurl": page_url, "action": action,
    })


async def solve_hcaptcha(site_key: str, page_url: str) -> Optional[str]:
    """Solve hCaptcha. Returns token or None."""
    if _s.anticaptcha_key:
        return await _anticaptcha({
            "type": "HCaptchaTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
        })
    return await _twocaptcha({"method": "hcaptcha", "sitekey": site_key, "pageurl": page_url})
