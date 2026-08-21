"""Small dependency-free sliding-window rate limiter.

Applied to mutating/expensive endpoints only (starting a debate).  Keyed by
client IP.  Good enough for a local/self-hosted MVP; swap for Redis when this
grows past one process.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, request: Request) -> str:
        client = request.client
        return client.host if client else "unknown"

    def check(self, request: Request) -> None:
        if self.max_requests <= 0:
            return
        key = self._key(request)
        now = time.monotonic()
        window = self._hits[key]
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.max_requests:
            retry_after = int(self.window_seconds - (now - window[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {self.max_requests} requests per "
                    f"{self.window_seconds}s. Try again in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)

    def reset(self) -> None:
        self._hits.clear()
