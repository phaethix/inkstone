"""Task 3: wiring vision placement into the letter stage."""

from __future__ import annotations

from PIL import Image

from core.pipelines import creative_comic as cc
from core.schemas import ComicPagePlan


def _plan() -> ComicPagePlan:
    return ComicPagePlan.model_validate(
        {"page_id": "p1", "panels": [{"panel_id": "1", "dialogue": "你好"}]}
    )


def test_letter_page_from_blank_passes_placement_boxes(tmp_path, monkeypatch):
    """placement_boxes must reach letter_finished_page unchanged."""
    blank = tmp_path / "blank.png"
    Image.new("RGB", (200, 300), (60, 80, 110)).save(blank)
    local = tmp_path / "page.png"
    captured: dict[str, object] = {}

    def _fake_letter_finished_page(img, plan, **kwargs):
        captured["placement_boxes"] = kwargs.get("placement_boxes")
        return img.copy()

    monkeypatch.setattr(cc, "letter_finished_page", _fake_letter_finished_page)
    forced = [(20, 20, 100, 40)]
    cc._letter_page_from_blank(blank, local, _plan(), source_text="原文", placement_boxes=forced)
    assert captured["placement_boxes"] == forced


def test_placement_digest_changes_letter_stage_src():
    """Different vision coordinates must produce a different letter key.

    Otherwise a changed AI layout would hit the cached overlay and be ignored.
    """
    a = cc._placement_stage_src([(20, 20, 100, 40)])
    b = cc._placement_stage_src([(60, 90, 100, 40)])
    assert a != b
    assert a == cc._placement_stage_src([(20, 20, 100, 40)])
    assert a != cc._LETTER_STAGE_SRC
