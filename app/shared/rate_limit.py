"""Per-caller request limits, in memory.

Every route that can spend money on someone else's behalf is behind one of
these. Starting a session puts a bot in a room — Claude, ElevenLabs and Azure
all bill for it — and a search spends a flight API budget of 200 requests an
hour shared by the whole deployment. Without a limit, one script in a loop is
an invoice.

**A sliding window, in process.** Each key keeps the timestamps of its recent
hits and is refused once it has `limit` of them inside `window` seconds. That
is exact rather than approximate, and at these volumes the memory is nothing.
The honest limit: with several API containers each keeps its own count, so the
effective ceiling multiplies by the replica count. One container is the
deployment today; past that, move this to Redis — the interface is one class.

Keys are chosen by the route: the user id where there is one, the client IP
where there is not. Behind a proxy the IP is only right if uvicorn trusts the
forwarded headers — `entrypoint.sh` starts it with `--proxy-headers`.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status


class RateLimiter:
    """At most `limit` hits per `window` seconds, per key."""

    #: Stop tracking keys that have gone quiet, so a stream of one-off callers
    #: cannot grow the table forever. Checked on write, amortised.
    _SWEEP_EVERY = 1000

    def __init__(self, *, limit: int, window: float, name: str):
        self.limit = limit
        self.window = window
        self.name = name
        self._hits: dict[str, deque[float]] = {}
        self._writes = 0

    def hit(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until it would be.

        Not async and takes no lock: it never awaits, so on one event loop it
        cannot be interleaved with itself.
        """
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(0.0, hits[0] + self.window - now)
        hits.append(now)

        self._writes += 1
        if self._writes % self._SWEEP_EVERY == 0:
            self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()

    def check(self, key: str) -> None:
        """Raise 429 if `key` is over its limit."""
        retry_after = self.hit(key)
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit_by_ip(limiter: RateLimiter) -> Callable:
    """A dependency that limits a route per client IP."""

    async def _dep(request: Request) -> None:
        limiter.check(f"ip:{client_ip(request)}")

    return _dep
