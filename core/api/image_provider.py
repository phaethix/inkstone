"""core.api.image_provider — Image Provider abstraction layer.

Centralizes all image-generation calls behind a single interface so that the
free Agnes API is the zero-config default, while any OpenAI-compatible image
endpoint can be swapped in seamlessly to hedge against the single-provider
dependency risk.

- ``ImageOutput``: generation result (url / b64); ``.save(path)`` persists to
  disk (**downloads carry no auth header**).
- ``ImageProvider``: abstract base class defining the ``generate_single_image`` contract.
- ``AgnesImageAPI``: default implementation (see ``agnes_image.py``).
- ``OpenAICompatProvider``: fallback for Agnes-protocol-compatible image endpoints.
- ``get_image_provider``: factory that picks a provider from config / args.

Reliability (retry, exponential backoff + jitter, error collection) is shared
across both providers via :mod:`core.api.retry` so their semantics never drift.
"""

import asyncio
import base64
import logging
from abc import ABC, abstractmethod

from requests import Response

from core.api.retry import collect_provider_error, retryable_post
from utils.image import download_image, resolve_image_ref

logger = logging.getLogger(__name__)


class ImageOutput:
    """Image generation result. ``fmt`` is ``url`` or ``b64``."""

    def __init__(self, fmt: str, ext: str, data: str):
        self.fmt = fmt
        self.ext = ext
        self.data = data

    def save(self, path: str) -> None:
        """Persist to disk. ``url`` is fetched via a bare request (no auth header);
        ``b64`` is base64-decoded and written directly."""
        if self.fmt == "url":
            download_image(self.data, path)
        else:
            raw = self.data.split(",")[1] if "," in self.data else self.data
            with open(path, "wb") as f:
                f.write(base64.b64decode(raw))


class ImageProvider(ABC):
    """Image Provider abstraction (the core abstraction of this module).

    All image generation (text-to-image / image-to-image) goes through this
    interface; callers do not care which backend model serves the request.
    Both ``AgnesImageAPI`` and ``OpenAICompatProvider`` implement this interface.
    """

    # Subclasses declare these model identifiers for the consistency engine / config display.
    model: str = ""
    i2i_model: str = ""

    @abstractmethod
    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        size: str | None = None,
        max_retries: int | None = None,
        retry_base_delay: float | None = None,
        **kwargs,
    ) -> ImageOutput:
        """Generate a single image (async, same as ``AgnesImageAPI``).

        Args:
            prompt: Text prompt for image generation.
            reference_image_paths: List of reference image paths/URLs (non-empty => i2i).
            size: Output size, e.g. ``1024x1024``.
            max_retries / retry_base_delay: Retry count and backoff base for transient
                errors. The free-tier image service is frequently 503 "Service busy",
                so the default is patient (8 attempts, 15s exponential backoff); tune via
                ``AGNES_IMAGE_MAX_RETRIES`` / ``AGNES_IMAGE_RETRY_BASE_DELAY``.
        Returns:
            An ``ImageOutput``; call ``.save(path)`` to persist.
        """
        ...


class OpenAICompatProvider(ImageProvider):
    """OpenAI-compatible image endpoint fallback (hedges Agnes single-provider risk).

    Targets any service exposing ``POST {base_url}/images/generations`` that
    follows the **Agnes image protocol**: i2i references are sent as
    ``extra_body.image=[data_uri]`` (matching Agnes's protocol). This holds for
    Agnes-compatible gateways; a vanilla OpenAI / Gemini endpoint ignores or
    rejects ``extra_body.image``, so for those, subclass and override
    ``_build_payload`` — which is exactly why this abstraction exists.

    t2i and i2i use the same model by default. Retry / backoff / error
    collection are identical to ``AgnesImageAPI`` (shared via ``core.api.retry``).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        i2i_model: str | None = None,
        max_retries: int = 8,
        retry_base_delay: float = 15.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.i2i_model = i2i_model or model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, prompt: str, resolved_refs: list[str], size: str | None) -> dict:
        use_i2i = len(resolved_refs) > 0
        model = self.i2i_model if use_i2i else self.model
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size or "1024x1024",
            "n": 1,
        }
        if resolved_refs:
            payload["extra_body"] = {"response_format": "url", "image": resolved_refs}
        return payload

    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        size: str | None = None,
        max_retries: int | None = None,
        retry_base_delay: float | None = None,
        **kwargs,
    ) -> ImageOutput:
        reference_image_paths = reference_image_paths or []
        # Resolve refs off the event loop.
        resolved = [await asyncio.to_thread(resolve_image_ref, p) for p in reference_image_paths]
        payload = self._build_payload(prompt, resolved, size)
        logger.info(
            f"[OpenAICompat] Generating "
            f"({'i2i' if reference_image_paths else 't2i'}): "
            f"{prompt[:80]}..."
        )

        async def _collect(*, status_code, response, attempt, exc=None, final=False):
            await collect_provider_error(
                prompt,
                status_code=status_code,
                response=response,
                attempt=attempt,
                exc=exc,
                final=final,
            )

        resp = await retryable_post(
            provider_tag="[OpenAICompat]",
            url=f"{self.base_url}/images/generations",
            headers=self.headers,
            json_payload=payload,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_base_delay=retry_base_delay
            if retry_base_delay is not None
            else self.retry_base_delay,
            size=size,
            collect=_collect,
        )
        return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp: Response) -> ImageOutput:
        result = resp.json()
        if "error" in result:
            raise RuntimeError(
                f"OpenAICompat image error: {result['error'].get('message', result['error'])}"
            )
        data_list = result.get("data", [])
        if not data_list:
            raise RuntimeError("OpenAICompat image: no data returned")
        url = data_list[0].get("url", "")
        if not url:
            b64_data = data_list[0].get("b64_json", "")
            if b64_data:
                return ImageOutput(fmt="b64", ext="png", data=b64_data)
            raise RuntimeError("OpenAICompat image: no URL or base64 in response")
        return ImageOutput(fmt="url", ext="png", data=url)


def get_image_provider(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    i2i_model: str | None = None,
    **_,
) -> ImageProvider:
    """Factory: return an image Provider based on configuration.

    Precedence: explicit args > environment variables > default (agnes).
    Configuration is read centrally from :class:`core.config.ImageConfig`.

    Env (ordinary users only need ``AGNES_API_KEY``)::

        AGNES_API_KEY=sk-xxx
        PROVIDER=agnes                      # agnes | openai_compat
        OPENAI_COMPAT_BASE_URL=https://...
        OPENAI_COMPAT_API_KEY=...
        OPENAI_COMPAT_MODEL_T2I=...
        OPENAI_COMPAT_MODEL_I2I=...
        AGNES_IMAGE_MAX_RETRIES=8          # image service is often 503 "Service busy"
        AGNES_IMAGE_RETRY_BASE_DELAY=15.0  # backoff base (s); capped at 120s

    Default path: when ``PROVIDER`` is unset or empty => ``agnes``, using only
    ``AGNES_API_KEY`` — realizing the "fill one line and it just works" goal.
    """
    from core.config import ImageConfig

    cfg = ImageConfig()
    provider = (provider or cfg.provider or "agnes").lower()
    api_key = api_key or cfg.agnes_api_key

    if provider == "agnes":
        if not api_key:
            raise RuntimeError("PROVIDER=agnes but no AGNES_API_KEY found (env or config)")
        from core.api.agnes_image import AgnesImageAPI

        return AgnesImageAPI(
            api_key=api_key,
            model=model or "agnes-image-2.1-flash",
            i2i_model=i2i_model or cfg.agnes_i2i_model,
            max_retries=cfg.image_max_retries,
            retry_base_delay=cfg.image_retry_base_delay,
        )

    if provider in ("openai_compat", "openai-compatible", "openai", "gemini"):
        base_url = base_url or cfg.openai_compat_base_url
        key = api_key or cfg.openai_compat_api_key
        if not base_url or not key:
            raise RuntimeError(
                "PROVIDER=openai_compat requires OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_API_KEY"
            )
        return OpenAICompatProvider(
            api_key=key,
            base_url=base_url,
            model=model or cfg.openai_compat_model_t2i,
            i2i_model=i2i_model or cfg.openai_compat_model_i2i,
            max_retries=cfg.image_max_retries,
            retry_base_delay=cfg.image_retry_base_delay,
        )

    raise ValueError(f"Unknown PROVIDER={provider!r} (supported: agnes | openai_compat)")
