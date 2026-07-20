import asyncio
import os
import sys

import pytest

# Ensure the repo root is on sys.path so top-level `core` / `utils` packages import in tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def patch_async(monkeypatch):
    """Neutralize backoff sleeps and rate limiting, and silence error logging, for unit tests."""

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    class _FastLimiter:
        def acquire(self):
            return None

    monkeypatch.setattr("core.api.rate_limiter.get_rate_limiter", lambda *a, **k: _FastLimiter())

    def _noop_collect(*_a, **_k):
        return None

    monkeypatch.setattr("core.api.error_collector.collect_error", _noop_collect)
    monkeypatch.setattr("core.api.error_collector.collect_error_from_exception", _noop_collect)
