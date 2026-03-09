"""
Shared slowapi rate limiter instance.

Import this in routers that need rate limiting:
    from app.limiter import limiter

Then decorate endpoints:
    @limiter.limit("10/minute")
    def my_endpoint(request: Request, ...):
        ...

The app must have `app.state.limiter = limiter` and the
RateLimitExceeded exception handler registered (done in main.py).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
