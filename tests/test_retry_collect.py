"""tests/test_retry_collect.py — retryable_post offloads error collection to a thread."""

import asyncio
from unittest.mock import MagicMock, patch

import requests

from core.api.retry import retryable_post


def test_retryable_post_collect_on_retryable_status_uses_to_thread():
    collected: list[tuple] = []

    def collect(*, status_code, response, attempt, exc=None, final=False):
        collected.append((status_code, attempt, final))

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.text = "unavailable"

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.raise_for_status = MagicMock()

    to_thread_calls: list[tuple] = []

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append((fn, args, kwargs))
        return fn(*args, **kwargs)

    async def run():
        with (
            patch("core.api.rate_limiter.get_rate_limiter") as mock_limiter,
            patch("requests.post", side_effect=[bad, good]),
            patch("core.api.retry.asyncio.to_thread", side_effect=fake_to_thread),
            patch("core.api.retry.asyncio.sleep", new=asyncio.sleep),
        ):
            mock_limiter.return_value.acquire = MagicMock()
            await retryable_post(
                provider_tag="[Test]",
                url="http://example.test/v1/images",
                headers={},
                json_payload={},
                max_retries=2,
                retry_base_delay=0.01,
                collect=collect,
            )

    asyncio.run(run())

    assert collected == [(503, 0, False)]
    collect_calls = [call for call in to_thread_calls if call[0] is collect]
    assert len(collect_calls) == 1
    assert collect_calls[0][2] == {
        "status_code": 503,
        "response": bad,
        "attempt": 0,
    }
