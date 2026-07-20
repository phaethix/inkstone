"""core.api.rate_limiter — Global token-bucket rate limiter (thread-safe).

All Agnes API calls share a token bucket so the total call rate never exceeds
the upstream free-tier limit. The limit is **size-aware** (free-tier RPM
1K ≈ 20 / 2K ≈ 10) — generating larger images automatically downshifts to a
lower RPM to avoid 429s.

Usage::

    from core.api.rate_limiter import get_rate_limiter
    get_rate_limiter().acquire()                 # blocking acquire (size-aware)
    await get_rate_limiter("1024x1024").acquire_async()
"""

import asyncio
import threading
import time

# Safety factor so we stay comfortably under the upstream ceiling.
_SAFETY_FACTOR = 0.8

# Free-tier RPM by output size (1K ≈ 20/min, 2K ≈ 10/min).
_RPM_BY_SIZE = {
    "1024x1024": 20,
    "1792x1024": 20,
    "1024x1792": 20,
    "1536x1024": 10,
    "1024x1536": 10,
    "2048x2048": 10,
    "2048x1024": 10,
    "1024x2048": 10,
}
_DEFAULT_RPM = 20


def select_rpm(size: str | None, default: int = _DEFAULT_RPM) -> int:
    """Return the RPM ceiling for ``size`` (defaults to the 1K tier)."""
    if not size:
        return default
    return _RPM_BY_SIZE.get(size, default)


class RateLimiter:
    """Thread-safe token-bucket rate limiter, safe under multithreading and asyncio.to_thread."""

    def __init__(self, rate_per_minute: float, max_burst: int = 4):
        self.max_tokens = min(max_burst, max(1, int(rate_per_minute)))
        self.refill_rate = rate_per_minute / 60.0  # tokens per second
        self.tokens = float(self.max_tokens)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Blockingly acquire one token; sleep until available if the bucket is empty."""
        with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.max_tokens,
                self.tokens + (now - self.last_refill) * self.refill_rate,
            )
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            wait = (1.0 - self.tokens) / self.refill_rate
            self.tokens = 0.0
            self.last_refill = now + wait
        if wait > 0.05:
            time.sleep(wait)

    async def acquire_async(self) -> None:
        await asyncio.to_thread(self.acquire)


_instances: dict[str, RateLimiter] = {}
_instances_lock = threading.Lock()


def get_rate_limiter(size: str | None = None) -> RateLimiter:
    """Return the (size-aware) global rate-limiter singleton (thread-safe)."""
    rpm = select_rpm(size) * _SAFETY_FACTOR
    key = size or "<default>"
    with _instances_lock:
        inst = _instances.get(key)
        if inst is None:
            inst = RateLimiter(rate_per_minute=rpm)
            _instances[key] = inst
        return inst


def reset_rate_limiter() -> None:
    """Reset all rate-limiter singletons (tests only)."""
    with _instances_lock:
        _instances.clear()
