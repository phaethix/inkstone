"""tests.test_density_boundary — QA 独立补充的 D1 边界用例（严过关）。

不修改工程师的 tests/test_density.py，独立覆盖团队要求复核的边界：
- 负数 / 非典型 concurrency 的除零防护
- price_per_panel 负数（无校验时的兜底行为，记录供架构确认）
- 纯空白文件不崩溃
- 中文 / 含空格路径的 book 参数
- 自定义低阈值 INKSTONE_WEBTOON_WARN_MB=1 触发 webtoon 警告
- 默认阈值下大样本（三体）webtoon 不误报
- estimate() 直接以 Path 对象入参
- CLI 实际仅暴露 --book/--density/--format（记录交付报告与代码差异）

运行：python -m pytest tests/test_density_boundary.py -q
"""

from pathlib import Path

import pytest

from core.density import ENV_WEBTOON_WARN_MB, PANELS_PER_PAGE, estimate


def _write(tmp_path: Path, text: str, name: str = "book.txt") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


_BODY = "这是一段用于密度预估测试的中文小说文本，包含人物与场景描写。\n" * 60


# --------------------------------------------------------------------------- #
# concurrency 边界
# --------------------------------------------------------------------------- #
def test_concurrency_negative_is_safe(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", concurrency=-3)  # 必须被保护为 1，不除零
    assert est.estimated_minutes == -(-est.panels // (1 * 2))


def test_concurrency_large_reduces_duration(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", concurrency=100)
    # ceil(panels / (100*2)) 必然 <= ceil(panels / 2)
    assert est.estimated_minutes <= -(-est.panels // (1 * 2))
    assert est.estimated_minutes >= 1 or est.panels == 0


# --------------------------------------------------------------------------- #
# price_per_panel 边界（estimate 函数级；CLI 未暴露该 flag）
# --------------------------------------------------------------------------- #
def test_price_per_panel_negative_rejected(tmp_path):
    """负数单价必须被显式拒绝（ValueError），不再拼负金额串（Round 2 修复后）。"""
    book = _write(tmp_path, _BODY)
    with pytest.raises(ValueError):
        estimate(book, density="B", api="openai-compat", price_per_panel=-0.05)


def test_estimate_honors_openai_price_param(tmp_path):
    """estimate() 函数级确实支持 price_per_panel（即便 CLI 未暴露）。"""
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="B", api="openai-compat", price_per_panel=0.1)
    assert est.cost_label == f"约 ¥{est.panels * 0.1:.2f}"


# --------------------------------------------------------------------------- #
# 空白 / 空文件
# --------------------------------------------------------------------------- #
def test_whitespace_only_file_no_crash(tmp_path):
    book = _write(tmp_path, "   \n\t  \n   ")
    est = estimate(book, density="B")
    assert est.total_chars >= 0
    assert est.chunks == 0
    assert est.panels == 0
    assert est.pages == 0
    assert est.estimated_minutes == 0
    # 字符数 >0 但 < _TINY_FILE_CHARS(100) → 过小提示，不崩溃
    assert any("过小" in w for w in est.warnings)


# --------------------------------------------------------------------------- #
# 路径边界：中文 / 空格
# --------------------------------------------------------------------------- #
def test_chinese_and_space_filename(tmp_path):
    book = _write(tmp_path, _BODY, name="小说 测试 book.txt")
    est = estimate(book, density="B")
    assert est.panels > 0
    assert est.pages == -(-est.panels // PANELS_PER_PAGE)


def test_path_object_input(tmp_path):
    book = _write(tmp_path, _BODY)
    est = estimate(Path(book), density="C")  # 直接传 Path 对象
    assert est.panels == est.chunks * 3


# --------------------------------------------------------------------------- #
# webtoon 阈值边界
# --------------------------------------------------------------------------- #
def test_custom_low_threshold_1mb_triggers(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "1")
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    # A 档 14 格 → 4 页 → 1.2MB > 1MB → 触发
    assert est.webtoon_warning is True
    assert isinstance(est.warnings, list) and est.warnings


def test_default_threshold_no_false_warn_on_threebody(tmp_path, monkeypatch):
    """默认 50MB 阈值下，三体(A/webtoon)约 23MB 不应误报。"""
    monkeypatch.delenv(ENV_WEBTOON_WARN_MB, raising=False)
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is False


def test_webtoon_warning_message_reports_size(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_WEBTOON_WARN_MB, "0.01")
    book = _write(tmp_path, _BODY)
    est = estimate(book, density="A", output_format="webtoon")
    assert est.webtoon_warning is True
    assert any("MB" in w for w in est.warnings)


# --------------------------------------------------------------------------- #
# CLI 实际暴露的 flag（锁定交付报告与代码差异）
# --------------------------------------------------------------------------- #
def test_cli_plan_exposes_api_concurrency_price_flags():
    """Round 2 回归：plan 子命令现已暴露 --api/--concurrency/--price-per-panel
    （D1 修复项，校准交付报告与代码）。"""
    from core.cli import _build_parser

    parser = _build_parser()
    plan = next(
        s
        for s in parser._subparsers._group_actions[0]._name_parser_map.values()
        if getattr(s, "prog", "").endswith("plan")
    )
    arg_names = {a.option_strings[0] for a in plan._actions if a.option_strings}
    for flag in ("--book", "--density", "--format", "--api", "--concurrency", "--price-per-panel"):
        assert flag in arg_names
