"""core.api.rate_limiter — Global token-bucket rate limiter (thread-safe).

Matches Agnes Free/Default RPM ceilings (official table):

- Text (chat):              20 RPM
- Image 1K (max side ≤1024): 20 RPM
- Image 2K (max side ≤2048): 10 RPM
- Image 3K/4K (larger):      1 RPM

A 0.8 safety factor is applied so we stay under the published ceiling.
Image limiters are keyed by **resolution tier** (1k/2k/3k), not by exact
``WxH`` string, so all 1K sizes share one budget and cannot stack.

Usage::

    from core.api.rate_limiter import get_rate_limiter
    get_rate_limiter().acquire()                      # image 1K default
    get_rate_limiter("2048x2048").acquire()           # image 2K tier
    get_rate_limiter(bucket="chat").acquire()         # text / chat
"""

from __future__ import annotations

import asyncio
import threading
import time

from core.config import agnes_image_2k_rpm, agnes_image_3k_rpm, agnes_rate_limit_rpm

# Stay under the published free-tier ceiling.
_SAFETY_FACTOR = 0.8

# Free/Default RPM from Agnes docs (Token Plan is higher; we target free).
# Widescreen sizes Agnes still bills under the 1K image RPM.
_ONE_K_WIDESCREEN = frozenset(
    {
        "1024x1792",
        "1792x1024",
    }
)


def _parse_dims(size: str | None) -> tuple[int, int] | None:
    """Parse ``WIDTHxHEIGHT``; return ``None`` if unparsable."""
    if not size:
        return None
    cleaned = size.lower().replace(" ", "")
    if "x" not in cleaned:
        return None
    left, _, right = cleaned.partition("x")
    try:
        return int(left), int(right)
    except ValueError:
        return None


def image_tier(size: str | None) -> str:
    """Map an image size to free-tier bucket: ``1k`` / ``2k`` / ``3k``.

    Free/Default image RPM: 1K=20, 2K=10, 3K/4K=1. Square ≤1024 and the
    documented 1024×1792 widescreen variants count as 1K; anything with a
    longer side up to 2048 is 2K; larger is 3K/4K.
    """
    dims = _parse_dims(size)
    if dims is None:
        return "1k"
    w, h = dims
    key = f"{w}x{h}"
    if key in _ONE_K_WIDESCREEN or max(w, h) <= 1024:
        return "1k"
    if max(w, h) <= 2048:
        return "2k"
    return "3k"


def select_rpm(size: str | None = None, *, bucket: str = "image") -> int:
    """Return the Free/Default RPM ceiling for this call.

    ``AGNES_RATE_LIMIT`` overrides the text and image-1K ceilings (default 20).
    2K/3K image ceilings stay at the published 10 / 1 unless overridden via
    ``AGNES_IMAGE_2K_RPM`` / ``AGNES_IMAGE_3K_RPM``.
    """
    text_or_1k = agnes_rate_limit_rpm()
    if bucket == "chat":
        return text_or_1k
    tier = image_tier(size)
    if tier == "1k":
        return text_or_1k
    if tier == "2k":
        return agnes_image_2k_rpm()
    return agnes_image_3k_rpm()


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


def get_rate_limiter(size: str | None = None, bucket: str = "image") -> RateLimiter:
    """Return the (tier-aware) global rate-limiter singleton (thread-safe).

    Chat uses an independent ``chat`` budget (text RPM). Image calls share a
    limiter per resolution tier so concurrent 1K sizes cannot exceed the 1K
    ceiling by opening separate WxH buckets.
    """
    rpm = select_rpm(size, bucket=bucket) * _SAFETY_FACTOR
    if bucket == "chat":
        key = "chat"
    else:
        key = f"image:{image_tier(size)}"
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
