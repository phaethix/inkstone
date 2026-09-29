"""core.config — single source of truth for environment configuration.

Centralizes every ``os.environ`` read so values, defaults, and the ``.env``
entrypoint live in one place. The recommended long-term approach is
``pydantic-settings``; this dependency-free stand-in keeps the same
:class:`ImageConfig` shape so the rest of the code never depends on how the
values are populated.
"""

import os

# Env var name constants (for tests, docs, and monkeypatch targets).
# Every ``os.environ`` read below must reference one of these; a bare string
# literal would be invisible to ALL_ENV_VARS and to the h_env registry, so
# tests/test_config.py fails if one reappears.
ENV_WEBTOON_WARN_MB = "INKSTONE_WEBTOON_WARN_MB"
ENV_WEBTOON_MAX_PIXELS = "INKSTONE_WEBTOON_MAX_PIXELS"
ENV_FONT_PATH = "INKSTONE_FONT_PATH"
ENV_PAGE_SCRIPT = "INKSTONE_PAGE_SCRIPT"
ENV_L3 = "INKSTONE_L3"
ENV_ERROR_LOG = "INKSTONE_ERROR_LOG"
ENV_RUN_DEADLINE_HOURS = "INKSTONE_RUN_DEADLINE_HOURS"
ENV_SUPERVISOR_BACKOFF_BASE = "INKSTONE_SUPERVISOR_BACKOFF_BASE"
ENV_SUPERVISOR_BACKOFF_CAP = "INKSTONE_SUPERVISOR_BACKOFF_CAP"
ENV_RENDER_MODE = "INKSTONE_RENDER_MODE"
ENV_PAGE_SIZE = "INKSTONE_PAGE_SIZE"
ENV_PROVIDER = "PROVIDER"
ENV_AGNES_API_KEY = "AGNES_API_KEY"
ENV_AGNES_IMAGE_I2I_MODEL = "AGNES_IMAGE_I2I_MODEL"
ENV_AGNES_CHAT_MODEL = "AGNES_CHAT_MODEL"
ENV_AGNES_IMAGE_MAX_RETRIES = "AGNES_IMAGE_MAX_RETRIES"
ENV_AGNES_IMAGE_RETRY_BASE_DELAY = "AGNES_IMAGE_RETRY_BASE_DELAY"
ENV_AGNES_RATE_LIMIT = "AGNES_RATE_LIMIT"
ENV_AGNES_IMAGE_2K_RPM = "AGNES_IMAGE_2K_RPM"
ENV_AGNES_IMAGE_3K_RPM = "AGNES_IMAGE_3K_RPM"
ENV_INKSTONE_IMAGE_CONCURRENCY = "INKSTONE_IMAGE_CONCURRENCY"
ENV_INKSTONE_PANEL_CONTINUITY = "INKSTONE_PANEL_CONTINUITY"
ENV_OPENAI_COMPAT_BASE_URL = "OPENAI_COMPAT_BASE_URL"
ENV_OPENAI_COMPAT_API_KEY = "OPENAI_COMPAT_API_KEY"
ENV_OPENAI_COMPAT_MODEL_T2I = "OPENAI_COMPAT_MODEL_T2I"
ENV_OPENAI_COMPAT_MODEL_I2I = "OPENAI_COMPAT_MODEL_I2I"
ENV_OPENAI_COMPAT_CHAT_BASE_URL = "OPENAI_COMPAT_CHAT_BASE_URL"
ENV_OPENAI_COMPAT_CHAT_API_KEY = "OPENAI_COMPAT_CHAT_API_KEY"
ENV_OPENAI_COMPAT_CHAT_MODEL = "OPENAI_COMPAT_CHAT_MODEL"


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Parse an integer env var; blank or invalid values fall back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    """Parse a float env var; blank or invalid values fall back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        return max(minimum, value)
    return value


def env_bool(name: str, default: bool = False) -> bool:
    """Truthiness for ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def agnes_rate_limit_rpm() -> int:
    return env_int(ENV_AGNES_RATE_LIMIT, 20)


def agnes_image_2k_rpm() -> int:
    return env_int(ENV_AGNES_IMAGE_2K_RPM, 10)


def agnes_image_3k_rpm() -> int:
    return env_int(ENV_AGNES_IMAGE_3K_RPM, 1)


def page_script_enabled() -> bool:
    return env_bool(ENV_PAGE_SCRIPT, default=False)


def l3_enabled() -> bool:
    return env_bool(ENV_L3, default=False)


def webtoon_warn_mb(*, default: float = 50.0) -> float:
    raw = os.environ.get(ENV_WEBTOON_WARN_MB)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Upper bound for a single webtoon canvas, in pixels. A webtoon strip is one
# giant RGB buffer (3 bytes/px), so an unbounded strip OOMs on long books.
DEFAULT_WEBTOON_MAX_PIXELS = 200_000_000


def webtoon_max_pixels(*, default: int = DEFAULT_WEBTOON_MAX_PIXELS) -> int:
    raw = _get(ENV_WEBTOON_MAX_PIXELS, "").strip()
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return default


def font_path() -> str:
    return _get(ENV_FONT_PATH, "").strip()


def error_log_name() -> str | None:
    name = os.environ.get(ENV_ERROR_LOG)
    return name if name else None


def run_deadline_hours() -> float:
    return env_float(ENV_RUN_DEADLINE_HOURS, 24.0, minimum=0.0)


def supervisor_backoff_base() -> float:
    return env_float(ENV_SUPERVISOR_BACKOFF_BASE, 30.0, minimum=0.1)


def supervisor_backoff_cap() -> float:
    return env_float(ENV_SUPERVISOR_BACKOFF_CAP, 300.0, minimum=0.1)


def render_mode() -> str:
    raw = _get(ENV_RENDER_MODE, "finished_page").strip().lower()
    if raw in {"panel_compose", "panel", "compose"}:
        return "panel_compose"
    return "finished_page"


def finished_page_size() -> str:
    raw = _get(ENV_PAGE_SIZE, "1024x1536").strip()
    return raw or "1024x1536"


class ImageConfig:
    """Aggregated image-generation configuration."""

    provider: str
    agnes_api_key: str
    agnes_i2i_model: str
    openai_compat_base_url: str
    openai_compat_api_key: str
    openai_compat_model_t2i: str
    openai_compat_model_i2i: str
    # Image generation on the free tier is frequently 503 "Service busy", so we
    # retry by default (5 attempts, 5s base) instead of giving up quickly.
    image_max_retries: int
    image_retry_base_delay: float
    image_concurrency: int
    panel_continuity: bool

    def __init__(self) -> None:
        self.provider = _get(ENV_PROVIDER, "agnes").lower()
        self.agnes_api_key = _get(ENV_AGNES_API_KEY)
        self.agnes_i2i_model = _get(ENV_AGNES_IMAGE_I2I_MODEL)
        self.openai_compat_base_url = _get(ENV_OPENAI_COMPAT_BASE_URL)
        self.openai_compat_api_key = _get(ENV_OPENAI_COMPAT_API_KEY)
        self.openai_compat_model_t2i = _get(
            ENV_OPENAI_COMPAT_MODEL_T2I, "gemini-2.0-flash-exp-image-generation"
        )
        self.openai_compat_model_i2i = _get(ENV_OPENAI_COMPAT_MODEL_I2I)
        self.image_max_retries = env_int(ENV_AGNES_IMAGE_MAX_RETRIES, 5)
        self.image_retry_base_delay = env_float(ENV_AGNES_IMAGE_RETRY_BASE_DELAY, 5.0)
        self.image_concurrency = max(1, env_int(ENV_INKSTONE_IMAGE_CONCURRENCY, 3, minimum=1))
        self.panel_continuity = _get(ENV_INKSTONE_PANEL_CONTINUITY, "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


class ChatConfig:
    """Aggregated chat / function-calling configuration."""

    provider: str
    agnes_api_key: str
    agnes_chat_model: str
    openai_compat_chat_base_url: str
    openai_compat_chat_api_key: str
    openai_compat_chat_model: str

    def __init__(self) -> None:
        self.provider = _get(ENV_PROVIDER, "agnes").lower()
        self.agnes_api_key = _get(ENV_AGNES_API_KEY)
        self.agnes_chat_model = _get(ENV_AGNES_CHAT_MODEL, "agnes-3.0-flash")
        self.openai_compat_chat_base_url = _get(ENV_OPENAI_COMPAT_CHAT_BASE_URL)
        self.openai_compat_chat_api_key = _get(ENV_OPENAI_COMPAT_CHAT_API_KEY)
        self.openai_compat_chat_model = _get(ENV_OPENAI_COMPAT_CHAT_MODEL)


# Every environment variable this module reads, collected from the ENV_*
# constants above. Collected by name prefix rather than listed by hand so a new
# constant cannot be forgotten; tests/test_env_snapshot.py asserts that the
# h_env registry partitions this set exactly. The list() snapshot avoids
# mutating globals() during iteration.
ALL_ENV_VARS: frozenset[str] = frozenset(
    value
    for name, value in list(globals().items())
    if name.startswith("ENV_") and isinstance(value, str)
)
