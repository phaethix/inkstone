"""tests/test_rate_limiter.py — free-tier RPM mapping (no network)."""

from core.api.rate_limiter import (
    get_rate_limiter,
    image_tier,
    reset_rate_limiter,
    select_rpm,
)


def test_image_tier_by_resolution_class():
    assert image_tier(None) == "1k"
    assert image_tier("1024x1024") == "1k"
    assert image_tier("1024x1792") == "1k"
    assert image_tier("1792x1024") == "1k"
    assert image_tier("1536x1024") == "2k"
    assert image_tier("2048x2048") == "2k"
    assert image_tier("3072x3072") == "3k"
    assert image_tier("4096x4096") == "3k"


def test_select_rpm_matches_free_tier_table(monkeypatch):
    monkeypatch.delenv("AGNES_RATE_LIMIT", raising=False)
    monkeypatch.delenv("AGNES_IMAGE_2K_RPM", raising=False)
    monkeypatch.delenv("AGNES_IMAGE_3K_RPM", raising=False)
    reset_rate_limiter()

    assert select_rpm(bucket="chat") == 20
    assert select_rpm("1024x1024") == 20
    assert select_rpm("2048x2048") == 10
    assert select_rpm("4096x4096") == 1


def test_agnes_rate_limit_overrides_text_and_1k(monkeypatch):
    monkeypatch.setenv("AGNES_RATE_LIMIT", "15")
    reset_rate_limiter()
    assert select_rpm(bucket="chat") == 15
    assert select_rpm("1024x1024") == 15
    assert select_rpm("2048x2048") == 10


def test_same_1k_sizes_share_one_limiter(monkeypatch):
    monkeypatch.delenv("AGNES_RATE_LIMIT", raising=False)
    reset_rate_limiter()
    a = get_rate_limiter("1024x1024")
    b = get_rate_limiter("1024x1792")
    c = get_rate_limiter("2048x2048")
    assert a is b
    assert a is not c
    chat = get_rate_limiter(bucket="chat")
    assert chat is not a
