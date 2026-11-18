"""--no-lettering: 关闭叠字时 blank 直出为成品页，letter 缓存键随开关变化。"""

import asyncio
import os
import unittest.mock
from pathlib import Path

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
    monkeypatch.setattr(
        cc,
        "letter_finished_page",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not letter")),
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


# ---------------------------------------------------------------------------
# resume 侧三个 stage_src 消费方（_page_chunk_complete 调用点 + 两处
# _letter_resume_status 预检）必须把 lettering 开关折叠进 stage_src：
# off 时收到 :nl-1 结尾的键，on 时保持 _LETTER_STAGE_SRC 原值。
# 三个消费点都藏在 _creative_comic / _render_page_node 的深嵌套闭包里，无法
# 逐一直调，因此按整管线驱动：run 1（lettering on，vision 定位对 FakeChat
# 失败 → boxes=None → plain 键）真实落盘后，resume 时用包装过的
# _letter_resume_status / _page_chunk_complete 记录到达消费方的 stage_src。
# ---------------------------------------------------------------------------

_PATCH_EXPORT = "core.comic.export.ExportEngine.export_pdf"
# 与既有 finished-page 断言（成品页 != blank）同款源文本：带场景句，plan 里
# 才有可叠的 caption/dialogue；纯"福贵在村口。"的最小源会让成品页与 blank
# 逐字节相同，无法区分"叠过字"与"没叠字"。
_SRC = "第一章\n福贵在村口。傍晚，村口。"


def _fakes():
    from tests.test_finished_page_pipeline import FakeChat, FakeImage, _fake_export_pdf

    return FakeChat, FakeImage, _fake_export_pdf


def _run_comic(tmp_path):
    FakeChat, FakeImage, fake_pdf = _fakes()
    assert os.environ.get("INKSTONE_LETTERING", "1") == "1", (
        f"INKSTONE_LETTERING leaked: {os.environ.get('INKSTONE_LETTERING')!r}"
    )
    # vision_placement_boxes 走全局 get_chat_provider()（不读注入的 chat）。
    # 若其他测试（如 CLI 帮助类用例触发 load_dotenv）把真实凭据留在
    # os.environ 里，vision 调用会真实成功并返回 :vp-* 键，键的形态随环境
    # 漂移。patch 成恒返回 []（视作 items 命中前短路），run 1 与 resume 的
    # placement 恒定，:vp-* 与 :nl-1 的组合留给单测 test_stage_src_folds_lettering_flag。
    with (
        unittest.mock.patch(_PATCH_EXPORT, fake_pdf),
        unittest.mock.patch.object(
            cc, "vision_placement_boxes", unittest.mock.AsyncMock(return_value=[])
        ),
    ):
        return asyncio.run(
            cc.creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
        )


def _run_resume(tmp_path, monkeypatch):
    """Resume with spies on the two resume-side letter consumers."""
    FakeChat, FakeImage, fake_pdf = _fakes()
    seen: list[str] = []
    real_status = cc._letter_resume_status

    def _spy_status(*args, **kwargs):
        seen.append(kwargs.get("stage_src"))
        return real_status(*args, **kwargs)

    monkeypatch.setattr(cc, "_letter_resume_status", _spy_status)

    chunk_stage_srcs: list[str] = []
    real_complete = cc._page_chunk_complete

    def _spy_complete(state, pageset, output_dir, chunk_index, source_text, **kwargs):
        chunk_stage_srcs.append(kwargs.get("stage_src"))
        return real_complete(state, pageset, output_dir, chunk_index, source_text, **kwargs)

    monkeypatch.setattr(cc, "_page_chunk_complete", _spy_complete)

    with (
        unittest.mock.patch(_PATCH_EXPORT, fake_pdf),
        unittest.mock.patch.object(
            cc, "vision_placement_boxes", unittest.mock.AsyncMock(return_value=[])
        ),
    ):
        asyncio.run(
            cc.creative_comic(_SRC, output_dir=str(tmp_path), chat=FakeChat(), image=FakeImage())
        )
    return seen, chunk_stage_srcs


def test_resume_chunk_complete_stage_src_folds_lettering_off(tmp_path, monkeypatch):
    """_page_chunk_complete 调用点在 lettering off 的 resume 上收到 :nl-1 键。"""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    _run_comic(tmp_path)
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    _, chunk_stage_srcs = _run_resume(tmp_path, monkeypatch)

    assert chunk_stage_srcs, "resume 必须到达 _page_chunk_complete 预检"
    assert all(src.endswith(":nl-1") for src in chunk_stage_srcs)


def test_resume_letter_precheck_stage_src_folds_lettering_off(tmp_path, monkeypatch):
    """两处 _letter_resume_status 预检在 lettering off 的 resume 上收到 :nl-1 键。"""
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    _run_comic(tmp_path)
    monkeypatch.setenv(config.ENV_LETTERING, "0")
    seen, _ = _run_resume(tmp_path, monkeypatch)

    assert seen, "resume 必须到达 _letter_resume_status 预检"
    assert all(src.endswith(":nl-1") for src in seen)


def test_resume_stage_src_keeps_value_when_lettering_on(tmp_path, monkeypatch):
    """lettering on 的 resume 上，到达的消费方收到与 _LETTER_STAGE_SRC 相同的键。

    完整 chunk 在 on→on 时会被正常短路（这是期望行为），因此先删除成品页
    让 chunk 不完整、预检可达（与既有 reletters 测试同款手法）。
    """
    from core.schemas import ProjectState

    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    _run_comic(tmp_path)
    state = ProjectState.load(tmp_path / "state.json")
    for rec in state.generated.pages.values():
        Path(rec.local).unlink()
    seen, chunk_stage_srcs = _run_resume(tmp_path, monkeypatch)

    assert chunk_stage_srcs and seen
    assert all(src == cc._LETTER_STAGE_SRC for src in chunk_stage_srcs + seen), (
        f"plain={cc._LETTER_STAGE_SRC!r} chunk={chunk_stage_srcs!r} seen={seen!r}"
    )


def test_on_to_off_switch_repaints_instead_of_short_circuiting(tmp_path, monkeypatch):
    """on→off 切换的后果回归：plain 键不得让 _page_chunk_complete 短路整个 chunk。

    修复前：消费方硬编码 plain 键，run 1（lettering on，plain 键）的 manifest
    命中让 page_chunk_complete 判 current → _complete_render_chunk_nodes 短路
    → letter 预检不可达，用户永远拿到旧叠字页。修复后 off 的 resume 用
    :nl-1 键判 miss，真正重出 art-only 页。
    """
    FakeChat, FakeImage, fake_pdf = _fakes()
    monkeypatch.setenv("INKSTONE_RENDER_MODE", "finished_page")
    _run_comic(tmp_path)
    blanks = {p.name: p.read_bytes() for p in (tmp_path / "pages" / "blank").glob("page_*.png")}
    lettered = {p.name: p.read_bytes() for p in (tmp_path / "pages").glob("page_*.png")}
    assert lettered and blanks
    assert any(lettered[name] != data for name, data in blanks.items() if name in lettered)

    monkeypatch.setenv(config.ENV_LETTERING, "0")
    letter_calls: list[object] = []
    monkeypatch.setattr(cc, "_letter_page_from_blank", lambda *a, **k: letter_calls.append(a))
    seen, chunk_stage_srcs = _run_resume(tmp_path, monkeypatch)

    assert chunk_stage_srcs and all(src.endswith(":nl-1") for src in chunk_stage_srcs)
    assert seen and all(src.endswith(":nl-1") for src in seen)
    assert letter_calls, "off 后 resume 必须重出 art-only 页，而非短路复用旧叠字页"
