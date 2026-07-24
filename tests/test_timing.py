# tests/test_timing.py
from core.pipelines.timing import estimate_remaining


def test_estimate_remaining_linear():
    assert estimate_remaining(100.0, 0.25) == 300.0


def test_estimate_remaining_too_early():
    assert estimate_remaining(100.0, 0.04) is None


def test_estimate_remaining_done():
    assert estimate_remaining(100.0, 1.0) == 0.0
