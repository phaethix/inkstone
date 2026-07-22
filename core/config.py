"""core.config — single source of truth for environment configuration.

Centralizes every ``os.environ`` read so values, defaults, and the ``.env``
entrypoint live in one place. The recommended long-term approach is
``pydantic-settings``; this dependency-free stand-in keeps the same
:class:`ImageConfig` shape so the rest of the code never depends on how the
values are populated.
"""

import os


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class ImageConfig:
    """Aggregated image-generation configuration."""

    provider: str
    agnes_api_key: str
    agnes_i2i_model: str
    openai_compat_base_url: str
    openai_compat_api_key: str
    openai_compat_model_t2i: str
    openai_compat_model_i2i: str
    rate_limit: int
    # Image generation on the free tier is frequently 503 "Service busy", so we
    # retry by default (5 attempts, 5s base) instead of giving up quickly.
    image_max_retries: int
    image_retry_base_delay: float
    image_concurrency: int
    panel_continuity: bool

    def __init__(self) -> None:
        self.provider = _get("PROVIDER", "agnes").lower()
        self.agnes_api_key = _get("AGNES_API_KEY")
        self.agnes_i2i_model = _get("AGNES_IMAGE_I2I_MODEL")
        self.openai_compat_base_url = _get("OPENAI_COMPAT_BASE_URL")
        self.openai_compat_api_key = _get("OPENAI_COMPAT_API_KEY")
        self.openai_compat_model_t2i = _get(
            "OPENAI_COMPAT_MODEL_T2I", "gemini-2.0-flash-exp-image-generation"
        )
        self.openai_compat_model_i2i = _get("OPENAI_COMPAT_MODEL_I2I")
        self.rate_limit = int(_get("AGNES_RATE_LIMIT", "20"))
        self.image_max_retries = int(_get("AGNES_IMAGE_MAX_RETRIES", "5"))
        self.image_retry_base_delay = float(_get("AGNES_IMAGE_RETRY_BASE_DELAY", "5.0"))
        self.image_concurrency = max(1, int(_get("INKSTONE_IMAGE_CONCURRENCY", "3")))
        self.panel_continuity = _get("INKSTONE_PANEL_CONTINUITY", "1").lower() in {
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
        self.provider = _get("PROVIDER", "agnes").lower()
        self.agnes_api_key = _get("AGNES_API_KEY")
        self.agnes_chat_model = _get("AGNES_CHAT_MODEL", "agnes-2.0-flash")
        self.openai_compat_chat_base_url = _get("OPENAI_COMPAT_CHAT_BASE_URL")
        self.openai_compat_chat_api_key = _get("OPENAI_COMPAT_CHAT_API_KEY")
        self.openai_compat_chat_model = _get("OPENAI_COMPAT_CHAT_MODEL")
