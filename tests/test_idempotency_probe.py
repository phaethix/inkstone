"""Guards for the idempotency probe (§13 resolved item 27).

The probe's network half cannot be tested without spending quota, so the tests
cover the half that decides the answer: the byte comparison, and the fact that
the harness compares persisted bytes rather than response URLs (a URL comparison
would report "different" for two byte-identical images served from two URLs).
"""

import asyncio
import hashlib

from core.api import ImageProvider
from core.api.idempotency_probe import compare_outputs, probe_image_idempotency


class _FakeOutput:
    """Minimal ``ImageOutput`` stand-in; ``save`` persists the payload."""

    def __init__(self, payload: bytes) -> None:
        self.fmt = "b64"
        self.data = payload.decode()
        self.ext = "png"
        self._payload = payload

    def save(self, path) -> None:
        with open(path, "wb") as handle:
            handle.write(self._payload)


class _DeterministicImage(ImageProvider):
    """Writes the same bytes every call, like a provider with stable semantics."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        self.calls += 1
        return _FakeOutput(b"identical-bytes")


class _StochasticImage(ImageProvider):
    """Writes different bytes each call, like an ordinary sampler."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_single_image(self, prompt, reference_image_paths=None, size=None, **kwargs):
        self.calls += 1
        return _FakeOutput(f"sample-{self.calls}".encode())


def test_compare_outputs_reports_identical_bytes():
    verdict = compare_outputs(b"same", b"same")

    assert verdict.identical is True
    assert verdict.digest_a == hashlib.sha256(b"same").hexdigest()
    assert verdict.digest_a == verdict.digest_b


def test_compare_outputs_reports_different_bytes():
    verdict = compare_outputs(b"one", b"two")

    assert verdict.identical is False
    assert verdict.digest_a != verdict.digest_b
    assert verdict.size_a == 3 and verdict.size_b == 3


def test_verdict_summary_names_the_consequence():
    assert "deterministic" in compare_outputs(b"x", b"x").summary()
    assert "different bytes" in compare_outputs(b"x", b"y").summary()


def test_probe_issues_exactly_two_calls(tmp_path):
    provider = _DeterministicImage()

    verdict = asyncio.run(
        probe_image_idempotency(provider, prompt="a cat", size="1024x1024", workdir=tmp_path)
    )

    assert provider.calls == 2
    assert verdict.identical is True


def test_probe_reports_a_stochastic_provider_as_different(tmp_path):
    verdict = asyncio.run(
        probe_image_idempotency(
            _StochasticImage(), prompt="a cat", size="1024x1024", workdir=tmp_path
        )
    )

    assert verdict.identical is False
