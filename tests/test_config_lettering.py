"""INKSTONE_LETTERING 配置函数的行为测试。"""

import pytest

from core import config


def test_lettering_default_is_enabled(monkeypatch):
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    assert config.lettering_enabled() is True


@pytest.mark.parametrize("raw", ["0", " false", "FALSE", "no", "OFF", ""])
def test_lettering_falsy_values_disable(monkeypatch, raw):
    monkeypatch.setenv(config.ENV_LETTERING, raw)
    assert config.lettering_enabled() is False


def test_set_lettering_env_true_sets_zero(monkeypatch):
    monkeypatch.delenv(config.ENV_LETTERING, raising=False)
    config.set_lettering_env(True)
    import os

    assert os.environ[config.ENV_LETTERING] == "0"


def test_set_lettering_env_false_pops(monkeypatch):
    monkeypatch.setenv(config.ENV_LETTERING, "1")
    config.set_lettering_env(False)
    import os

    assert config.ENV_LETTERING not in os.environ
