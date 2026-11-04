"""core.dotenv — 统一 CLI 的 repo-root .env 加载。

纯 stdlib 解析 ``KEY=value`` 行，绝不 ``source``/执行文件（避免复制来的 .env
里命令替换在启动时被执行）。已存在于环境中的变量优先，不被覆盖。

只认 repo 根目录（本文件的上一级的上级，即 ``core/dotenv.py`` → 项目根）的
``.env``。由 ``core.cli.main()`` 在解析参数之前调用，因此所有子命令与
``web`` 子命令都能读到凭据，无需再手工 ``export``。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / ".env"


def load_dotenv(*, path: Path | None = None, environ: dict[str, str] | None = None) -> int:
    """把 .env 里尚未设置的键写入环境，返回实际设置的键数量。

    ``path`` / ``environ`` 仅供测试注入，默认作用于 repo 根 ``.env`` 与
    ``os.environ``。文件不存在或不可读时静默返回 0。
    """
    env = environ if environ is not None else os.environ
    target = path if path is not None else _ENV_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    count = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in env:
            env[key] = val
            count += 1
    return count
