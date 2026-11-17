"""--no-lettering flag 解析测试。"""

from core.cli import _build_parser
from core.cli_generate import _parse_args as _parse_generate_args


def test_cli_generate_flag_parses_true():
    args = _build_parser().parse_args(["generate", "--no-lettering"])
    assert args.no_lettering is True


def test_cli_generate_flag_defaults_false():
    args = _build_parser().parse_args(["generate"])
    assert args.no_lettering is False


def test_cli_generate_entry_flag_parses_true():
    args = _parse_generate_args(["--no-lettering", "story.txt"])
    assert args.no_lettering is True


def test_cli_generate_entry_flag_defaults_false():
    args = _parse_generate_args(["story.txt"])
    assert args.no_lettering is False
