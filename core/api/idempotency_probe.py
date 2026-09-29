"""core.api.idempotency_probe — the two-call idempotency measurement (§13 item 27).

§8 accepts a duplicate-charge window because "no current provider honours an
idempotency key". That premise was inferred backwards from a single incident, so
it is an assumption. This module turns it into evidence: issue the same request
twice and compare the resulting bytes.

Two details are load-bearing:

- The comparison is over **persisted bytes**, not response URLs. Providers may
  serve byte-identical images from two different URLs, so comparing URLs would
  report "different" and wrongly leave the window open.
- The verdict is stated as a consequence, not a score. Identical bytes mean the
  window can be closed; different bytes leave §8's conclusion unchanged, and the
  measurement still upgrades it from assumption to evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.api.image_provider import ImageProvider


@dataclass(frozen=True)
class IdempotencyVerdict:
    """The outcome of comparing two responses to one identical request."""

    identical: bool
    digest_a: str
    digest_b: str
    size_a: int
    size_b: int

    def summary(self) -> str:
        """State what the bytes mean for §8's duplicate-charge window."""
        if self.identical:
            return (
                "identical bytes — the provider is deterministic; the duplicate-charge "
                "window can be closed (§8)"
            )
        return (
            "different bytes — the duplicate-charge window stays open; it remains "
            "'shrinkable, not closable' (§8)"
        )


def compare_outputs(first: bytes, second: bytes) -> IdempotencyVerdict:
    """Compare two persisted responses byte-for-byte."""
    return IdempotencyVerdict(
        identical=first == second,
        digest_a=hashlib.sha256(first).hexdigest(),
        digest_b=hashlib.sha256(second).hexdigest(),
        size_a=len(first),
        size_b=len(second),
    )


async def probe_image_idempotency(
    provider: ImageProvider,
    *,
    prompt: str,
    size: str,
    workdir: Path,
) -> IdempotencyVerdict:
    """Issue one request twice and compare the persisted bytes.

    Costs exactly two image calls. The two artifacts are written into
    ``workdir`` so the comparison sees the bytes a caller would actually use,
    rather than a base64 string or a URL.

    Args:
        provider: the live provider; the caller owns the quota cost.
        prompt / size: the request, held identical across both calls.
        workdir: where the two response artifacts are written.

    Returns:
        The verdict from :func:`compare_outputs` over the two files.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    first_path = workdir / "idempotency-first.png"
    second_path = workdir / "idempotency-second.png"

    first = await provider.generate_single_image(prompt, size=size)
    first.save(str(first_path))
    second = await provider.generate_single_image(prompt, size=size)
    second.save(str(second_path))

    return compare_outputs(first_path.read_bytes(), second_path.read_bytes())
