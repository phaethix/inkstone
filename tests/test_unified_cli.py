"""tests/test_unified_cli.py — 统一入口 uv run inkstone 的回归约束。"""

import os
import sys

from core import cli


def _args(*argv: str):
    return cli._build_parser().parse_args(list(argv))


def test_generate_book_alias_matches_plan():
    gen = _args("generate", "--book", "novel.txt")
    assert gen.book == "novel.txt"
    assert gen.source is None
    pos = _args("generate", "novel.txt")
    assert pos.source == "novel.txt"
    assert pos.book is None


def test_model_flags_parse_on_generate_and_web():
    gen = _args(
        "generate",
        "novel.txt",
        "--chat-model",
        "agnes-x",
        "--image-model",
        "agnes-img",
        "--provider",
        "agnes",
        "--base-url",
        "https://example/v1",
    )
    assert gen.chat_model == "agnes-x"
    assert gen.image_model == "agnes-img"
    assert gen.provider == "agnes"
    assert gen.base_url == "https://example/v1"
    web = _args("web", "--host", "0.0.0.0", "--port", "9000")
    assert web.host == "0.0.0.0"
    assert web.port == 9000


def test_apply_model_flags_overrides_environment(monkeypatch):
    for key in ("AGNES_CHAT_MODEL", "AGNES_IMAGE_I2I_MODEL", "PROVIDER", "AGNES_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    args = _args(
        "generate",
        "novel.txt",
        "--chat-model",
        "m1",
        "--image-model",
        "m2",
        "--provider",
        "agnes",
        "--base-url",
        "https://x/v1",
    )
    cli._apply_model_flags(args)
    assert os.environ["AGNES_CHAT_MODEL"] == "m1"
    assert os.environ["AGNES_IMAGE_I2I_MODEL"] == "m2"
    assert os.environ["PROVIDER"] == "agnes"
    assert os.environ["AGNES_BASE_URL"] == "https://x/v1"


def test_openai_compat_flags_route_to_compat_vars(monkeypatch):
    for key in ("OPENAI_COMPAT_CHAT_MODEL", "OPENAI_COMPAT_MODEL_I2I", "OPENAI_COMPAT_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    args = _args(
        "web",
        "--chat-model",
        "c",
        "--image-model",
        "i",
        "--provider",
        "openai_compat",
        "--base-url",
        "https://y/v1",
    )
    cli._apply_model_flags(args)
    assert os.environ["OPENAI_COMPAT_CHAT_MODEL"] == "c"
    assert os.environ["OPENAI_COMPAT_MODEL_I2I"] == "i"
    assert os.environ["OPENAI_COMPAT_BASE_URL"] == "https://y/v1"


def test_web_command_is_not_rewritten_as_generate(monkeypatch):
    captured: dict[str, object] = {}

    def fake_web(args):
        captured["hit"] = args
        return 0

    monkeypatch.setattr(cli, "_run_web", fake_web)
    monkeypatch.setattr(sys, "argv", ["inkstone", "web", "--port", "9000"])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0
    assert captured["hit"].port == 9000


def test_main_loads_dotenv_before_parsing(monkeypatch):
    seen: dict[str, bool] = {"loaded": False}

    def fake_load():
        seen["loaded"] = True

    monkeypatch.setattr(cli, "load_dotenv", fake_load)
    monkeypatch.setattr(cli, "_apply_model_flags", lambda args: None)
    monkeypatch.setattr(cli, "_run_plan", lambda args: seen.setdefault("ran", True))
    monkeypatch.setattr(sys, "argv", ["inkstone", "plan", "--book", "x.txt"])
    cli.main()
    assert seen["loaded"] is True
    assert seen.get("ran") is True
