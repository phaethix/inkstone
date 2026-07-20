"""core.api.retry — shared retry / backoff / error-collection for image providers.

Both ``AgnesImageAPI`` and ``OpenAICompatProvider`` funnel their HTTP retry loop
through :func:`retryable_post` so that retry semantics, the backoff policy
(ADR-12.4: exponential, capped at 120s, with jitter), and error collection stay
identical across providers (review P0-1 / P0-2).

Previously the ~100-line retry block was copy-pasted into each provider with
*different* error-collection behavior; that duplication is gone now.
"""

import asyncio
import logging
import random

import requests

logger = logging.getLogger(__name__)

# ADR-12.4: exponential backoff, capped at 120s, with full jitter.
BACKOFF_CAP_SECONDS = 120.0

# Transient statuses worth retrying (shared by both providers, review P0-2).
RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def compute_backoff(attempt: int, base_delay: float, cap: float = BACKOFF_CAP_SECONDS) -> float:
    """Exponential backoff with a hard cap and full jitter (ADR-12.4).

    ``attempt`` is 0-based (the current retry index). The raw delay grows as
    ``base_delay * 2**attempt``, is clamped to ``cap`` (120s), then multiplied
    by a uniform random factor in ``[0.5, 1.0]`` so that many clients do not
    synchronize their retries into a collective spike.
    """
    raw = base_delay * (2**attempt)
    capped = min(raw, cap)
    return capped * random.uniform(0.5, 1.0)


async def collect_provider_error(
    prompt: str,
    *,
    status_code: int | None,
    response: "requests.Response | None" = None,
    attempt: int = 0,
    exc: "Exception | None" = None,
    final: bool = False,
) -> None:
    """Record a transient/terminal failure identically for every provider (P0-2).

    Wrapped in ``asyncio.to_thread`` by callers so the disk write never blocks
    the event loop (review P1).
    """
    from core.api.error_collector import (
        collect_error,
        collect_error_from_exception,
    )

    if exc is not None:
        collect_error_from_exception(
            "image",
            "generate_single_image",
            exc,
            prompt=prompt,
            retry_count=attempt + 1,
        )
        return

    error_type = (
        "RateLimit429" if status_code == 429 else "HTTPError" if final else f"HTTP{status_code}"
    )
    collect_error(
        "image",
        "generate_single_image",
        prompt=prompt,
        error_type=error_type,
        error_message=f"HTTP {status_code}"
        + (": max retries exceeded" if final else ": retryable error"),
        status_code=status_code,
        response_body=response.text if response is not None else "",
        retry_count=attempt + 1,
    )


async def retryable_post(
    *,
    provider_tag: str,
    url: str,
    headers: dict,
    json_payload: dict,
    max_retries: int,
    retry_base_delay: float,
    size: str | None = None,
    collect: "callable | None" = None,
) -> "requests.Response":
    """Retryable async ``POST`` to an image-generation endpoint.

    - Acquires the (size-aware) global rate limiter before each attempt.
    - Retries on transport errors (``ConnectionError`` / ``Timeout``) and on
      retryable HTTP statuses (429 / 5xx) with exponential + jitter backoff.
    - ``collect`` (async callable) is invoked on every retryable failure so both
      providers record errors with identical semantics.
    - Raises the underlying error after exhaustion, or ``RuntimeError`` if no
      response was ever obtained.

    The ``requests`` call and the error-collection disk write are both pushed
    off the event loop via ``asyncio.to_thread`` (review P1).
    """
    from core.api.rate_limiter import get_rate_limiter

    resp: requests.Response | None = None
    for attempt in range(max_retries):
        await asyncio.to_thread(get_rate_limiter(size).acquire)
        read_timeout = 60 * (attempt + 1)
        try:
            resp = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json=json_payload,
                timeout=(30, read_timeout),
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            if collect is not None:
                await collect(status_code=None, response=None, attempt=attempt, exc=e)
            if attempt < max_retries - 1:
                delay = compute_backoff(attempt, retry_base_delay)
                logger.warning(
                    f"{provider_tag} {type(e).__name__}, "
                    f"retry {attempt + 1}/{max_retries} in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
                continue
            raise

        if resp.status_code in RETRYABLE_STATUS and attempt < max_retries - 1:
            if collect is not None:
                await collect(status_code=resp.status_code, response=resp, attempt=attempt)
            delay = compute_backoff(attempt, retry_base_delay)
            logger.warning(
                f"{provider_tag} HTTP {resp.status_code}, "
                f"retry {attempt + 1}/{max_retries} in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
            continue

        # Retryable status but no attempts left: give up with a clear error
        # (instead of leaking the raw 5xx/429 as if it were a terminal failure).
        if resp.status_code in RETRYABLE_STATUS:
            if collect is not None:
                await collect(
                    status_code=resp.status_code,
                    response=resp,
                    attempt=attempt,
                    final=True,
                )
            raise RuntimeError(
                f"{provider_tag} max retries ({max_retries}) exceeded, "
                f"last status {resp.status_code}"
            )

        # Success or a hard (non-retryable) failure: surface it to the caller.
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            if collect is not None:
                await collect(
                    status_code=resp.status_code,
                    response=resp,
                    attempt=attempt,
                    final=True,
                )
            raise
        return resp

    # Only reachable on an edge case (e.g. max_retries <= 1 with a retryable
    # status that fell through). Surface it clearly.
    last_status = resp.status_code if resp is not None else None
    raise RuntimeError(
        f"{provider_tag} max retries ({max_retries}) exceeded"
        + (f", last status {last_status}" if last_status is not None else " with no response")
    )
