"""tests.test_density — unit tests for the D1 density estimator.

Covers all three tiers, the webtoon size guard, both billing backends, and the
empty / tiny-file boundaries, plus custom concurrency and threshold overrides.

Run with: ``python -m pytest tests/test_density.py -q`` (from the repo root).
"""

from pathlib import Path

from core.density import (
    ENV_WEBTOON_WARN_MB,
    PANELS_PER_CHUNK_A,
    PANELS_PER_CHUNK_B,
    PANELS_PER_CHUNK_C,
    PANELS_PER_PAGE,
    DensityEstimate,
    DensityPlan,
    estimate,
    get_density_plan,
)


def _write(tmp_path: Path, text: str) -> Path:
    """Write ``text`` to a temp ``.txt`` and return its path."""
    path = tmp_path / "book.txt"
    path.write_text(text, encoding="utf-8")
    return path


# A multi-paragraph body so segment_text produces more than one chunk.
_BODY = "这是一段用于密度预估测试的示例小说文本，包含人物与场景描写。\n" * 60


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #
def test_get_density_plan_constants():
    a = get_density_plan("A")
    b = get_density_plan("B")
    c = get_density_plan("C")
    assert isinstance(a, DensityPlan)
    assert a.panels_per_chunk == PANELS_PER_CHUNK_A == 14
    assert b.panels_per_chunk == PANELS_PER_CHUNK_B == 8
    assert c.panels_per_chunk == PANELS_PER_CHUNK_C == 3
    assert a.tier == "A" and a.description == "主线完备"


def test_tier_a_b_c_panel_counts(tmp_path):
    book = _write(tmp_path, _BODY)
    est_a = estimate(book, density="A")
    est_b = estimate(book, density="B")
    est_c = estimate(book, density="C")
    # Same source → same chunk count, so panels scale with panels_per_chunk.
    assert est_a.chunks == est_b.chunks == est_c.chunks
    assert est_a.panels == est_a.chunks * PANELS_PER_CHUNK_A
    assert est_b.panels == est_b.chunks * PANELS_PER_CHUNK_B
    assert est_c.panels == est_c.chunks * PANELS_PER_CHUNK_C
    # Monotonic: more panels for denser tiers.
    assert est_a.panels > est_b.panels > est_c.panels


def test_pages_follow_panels_per_page(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B")
    expected_pages = -(-est.panels // PANELS_PER_PAGE)  # ceil
    assert est.pages == expected_pages
    assert est.output_format == "page"
    assert est.output_name == "comic.pdf"


# --------------------------------------------------------------------------- #
# Webtoon size guard
# --------------------------------------------------------------------------- #
def test_webtoon_warning_triggers_with_low_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "0.01")  # almost anything triggers
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is True
    assert any("webtoon" in w for w in est.warnings)
    assert est.output_name == "webtoon.png"


def test_webtoon_warning_off_for_page_format(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "0.01")
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="page")
    assert est.webtoon_warning is False
    assert est.warnings == []  # guard is a no-op outside webtoon mode


def test_webtoon_threshold_override_suppresses_warning(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "100000")  # huge threshold
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is False


# --------------------------------------------------------------------------- #
# Cost labelling
# --------------------------------------------------------------------------- #
def test_agnes_cost_label(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="agnes")
    assert "¥0" in est.cost_label
    assert "R6" in est.cost_label


def test_openai_cost_with_price(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="openai-compat", price_per_panel=0.05)
    assert est.cost_label.startswith("约 ¥")
    expected = f"约 ¥{est.panels * 0.05:.2f}"
    assert est.cost_label == expected


def test_openai_cost_without_price(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="openai-compat", price_per_panel=None)
    assert est.cost_label == "按您的 endpoint 单价"


def test_negative_price_per_panel_rejected():
    import pytest

    from core.density import estimate as _estimate

    with pytest.raises(ValueError):
        _estimate("nonexistent_ignored.txt", api="openai-compat", price_per_panel=-0.05)


# --------------------------------------------------------------------------- #
# Boundaries: empty / tiny file
# --------------------------------------------------------------------------- #
def test_empty_file(tmp_path):
    book = _write(tmp_path, "")
    est = estimate(book, density="B")
    assert est.total_chars == 0
    assert est.chunks == 0
    assert est.panels == 0
    assert est.pages == 0
    assert est.estimated_minutes == 0
    assert est.webtoon_warning is False
    assert any("空" in w for w in est.warnings)


def test_tiny_file_advisory(tmp_path):
    book = _write(tmp_path, "很短。")
    est = estimate(book, density="A")
    assert est.chunks >= 1
    assert est.panels == est.chunks * PANELS_PER_CHUNK_A
    assert any("过小" in w for w in est.warnings)


# --------------------------------------------------------------------------- #
# Custom concurrency + threshold
# --------------------------------------------------------------------------- #
def test_custom_concurrency_changes_duration(tmp_path):
    book = _write(tmp_path, _BODY)
    fast = estimate(book, density="B", concurrency=4)
    slow = estimate(book, density="B", concurrency=1)
    # More workers → fewer estimated minutes (ceil(panels / (conc * PPM))).
    assert slow.estimated_minutes > fast.estimated_minutes
    assert fast.estimated_minutes == -(-fast.panels // (4 * 2))
    assert slow.estimated_minutes == -(-slow.panels // (1 * 2))


def test_concurrency_zero_is_safe(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", concurrency=0)  # must not divide by zero
    assert est.estimated_minutes == -(-est.panels // (1 * 2))


def test_default_threshold_from_constant(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_WEBTOON_WARN_MB, raising=False)
    book = _write(tmp_path, _BODY)
    # Default 50 MB threshold is not exceeded by a small sample in webtoon mode.
    est = estimate(book, density="A", output_format="webtoon")
    # Only assert the field exists and type is correct; threshold is the default.
    assert isinstance(est.webtoon_warning, bool)
    assert isinstance(est, DensityEstimate)
