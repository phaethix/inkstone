"""tests/test_rate_limiter.py — free-tier RPM mapping (no network)."""

from core.api.rate_limiter import (
    RateLimiter,
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


def test_default_2k_bucket_stays_under_ten_calls_in_an_open_minute(monkeypatch):
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr("core.api.rate_limiter.time.monotonic", monotonic)
    monkeypatch.setattr("core.api.rate_limiter.time.sleep", sleep)
    limiter = RateLimiter(10 * 0.8)
    assert limiter.max_tokens == 1
    stamps = []
    for _ in range(12):
        limiter.acquire()
        stamps.append(clock["now"])
    assert sum(1 for stamp in stamps if stamp < 60) <= 10

    monkeypatch.delenv("AGNES_IMAGE_2K_RPM", raising=False)
    monkeypatch.delenv("AGNES_RATE_LIMIT", raising=False)
    reset_rate_limiter()
    assert get_rate_limiter("1024x1536").max_tokens == 1
    # The same gap rule gives 1K (20 published, 16 scaled) a burst of 3.
    reset_rate_limiter()
    assert get_rate_limiter("1024x1024").max_tokens == 3
