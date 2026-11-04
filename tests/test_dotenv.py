"""tests/test_dotenv.py — 统一 CLI 的 .env 加载约束。"""

from core.dotenv import load_dotenv


def test_loads_keys_into_empty_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nAGNES_API_KEY=sk-abc\nAGNES_CHAT_MODEL = 'agnes-x'\nINVALID\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {}
    count = load_dotenv(path=path, environ=env)
    assert count == 2
    assert env["AGNES_API_KEY"] == "sk-abc"
    assert env["AGNES_CHAT_MODEL"] == "agnes-x"


def test_never_overrides_existing_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text("AGNES_API_KEY=from-file\nOTHER=v\n", encoding="utf-8")
    env = {"AGNES_API_KEY": "already-exported"}
    count = load_dotenv(path=path, environ=env)
    assert count == 1
    assert env["AGNES_API_KEY"] == "already-exported"
    assert env["OTHER"] == "v"


def test_missing_file_is_silent(tmp_path):
    env: dict[str, str] = {}
    assert load_dotenv(path=tmp_path / "nope.env", environ=env) == 0
    assert env == {}


def test_does_not_execute_shell_substitutions(tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEY=$(echo pwned)\n", encoding="utf-8")
    env: dict[str, str] = {}
    load_dotenv(path=path, environ=env)
    assert env["KEY"] == "$(echo pwned)"
