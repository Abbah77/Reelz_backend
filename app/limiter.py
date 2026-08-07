"""
limiter.py — shared slowapi rate limiter instance.

Defined here (not in main.py) to avoid circular imports:
  main.py imports routers → routers need limiter → limiter must not import main.py.

Usage in routers:
    from app.limiter import limiter

    @router.post("/streams")
    @limiter.limit("10/minute")
    async def post_streams(request: Request, ...):
        ...

main.py attaches it to app.state and registers the exception handler:
    from app.limiter import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Key by real client IP. Works behind Render's proxy because Starlette/slowapi
# reads X-Forwarded-For when the ASGI scope has the correct client address.
limiter = Limiter(key_func=get_remote_address)
