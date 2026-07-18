from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class InMemoryRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, identity: str, now: float | None = None) -> tuple[bool, int]:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.window_seconds
        with self._lock:
            requests = self._requests[identity]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (timestamp - requests[0])))
                return False, retry_after
            requests.append(timestamp)
        return True, 0


def access_code_is_valid(provided: str | None, expected: str | None = None) -> bool:
    configured = os.environ.get("DEMO_ACCESS_CODE", "") if expected is None else expected
    if not configured:
        return True
    return bool(provided) and secrets.compare_digest(provided, configured)


def request_identity(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


class DemoSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, rate_limiter: InMemoryRateLimiter) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)

        if not access_code_is_valid(request.headers.get("x-demo-access-code")):
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing demo access code."})

        allowed, retry_after = self.rate_limiter.check(request_identity(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again shortly."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

