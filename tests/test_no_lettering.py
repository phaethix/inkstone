"""--no-lettering: 关闭叠字时 blank 直出为成品页，letter 缓存键随开关变化。"""

import pytest
from PIL import Image

from core import config
from core.pipelines import creative_comic as cc


@pytest.fixture
def blank_and_plan(tmp_path):
    from core.schemas import ComicPagePlan

    blank = tmp_path / "blank.png"
    Image.new("RGB", (64, 96), (200, 200, 200)).save(blank)
    # PagePanelSpec 必填仅 panel_id（schemas.py:805），ComicPagePlan 必填仅
    # page_id（schemas.py:923），dialogue/caption/sfx 均为可空 str|None。
    plan = ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "panels": [{"panel_id": "g1", "caption": "n", "dialogue": "hi"}],
        }
    )
    return blank, plan


def test_lettering_off_copies_blank_as_finished_page(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    calls: list[object] = []
    monkeypatch.setattr(
        cc,
        "letter_finished_page",
        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("must not letter")),
    )
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert finished.is_file()
    assert finished.read_bytes() == blank.read_bytes()


def test_lettering_on_keeps_current_behavior(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    finished = tmp_path / "finished.png"
    seen: dict[str, bool] = {"called": False}

    def _fake_letter(blank_img, plan_arg, **kwargs):
        seen["called"] = True
        return blank_img.copy()

    monkeypatch.setattr(cc, "letter_finished_page", _fake_letter)
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert seen["called"] is True
    assert finished.is_file()


def test_explicit_lettering_param_overrides_env(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    seen: dict[str, bool] = {"called": False}

    def _fake_letter(blank_img, plan_arg, **kwargs):
        seen["called"] = True
        return blank_img.copy()

    monkeypatch.setattr(cc, "letter_finished_page", _fake_letter)
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path, lettering=True)
    assert seen["called"] is True


def test_stage_src_folds_lettering_flag():
    on = cc._placement_stage_src(None, lettering=True)
    off = cc._placement_stage_src(None, lettering=False)
    assert on == cc._placement_stage_src(None, lettering=True)  # 稳定可复算
    assert off == cc._placement_stage_src(None, lettering=False)
    assert off != on
    assert off.startswith(on) or on.startswith(off)


def test_default_lettering_arg_reads_env(tmp_path, blank_and_plan, monkeypatch):
    blank, plan = blank_and_plan
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    finished = tmp_path / "finished.png"
    cc._letter_page_from_blank(blank, finished, plan, output_dir=tmp_path)
    assert finished.read_bytes() == blank.read_bytes()
