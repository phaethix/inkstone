"""core.api.agnes_image — Agnes Image API wrapper (migrated from core/image_generator.py)."""

import asyncio
import logging

from requests import Response

from core.api.error_collector import collect_error
from core.api.image_provider import ImageOutput, ImageProvider
from core.api.retry import collect_provider_error, retryable_post
from utils.image import resolve_image_ref_async

logger = logging.getLogger(__name__)

BASE_URL = "https://apihub.agnes-ai.com/v1"


class AgnesImageAPI(ImageProvider):
    """Agnes Image generation API wrapper (t2i / i2i)."""

    def __init__(
        self,
        api_key: str,
        model: str = "agnes-image-2.1-flash",
        i2i_model: str | None = None,
        max_retries: int = 8,
        retry_base_delay: float = 15.0,
    ):
        """Initialize the image API.

        Args:
            api_key: Agnes API key.
            model: Default t2i model.
            i2i_model: Default i2i model. Defaults to ``model`` (official
                agnes-image-2.1-flash supports both t2i and i2i). Pass an
                explicit model to fall back to 2.0 for the consistency img2img
                pass.
            max_retries / retry_base_delay: Image generation on the free tier is
                frequently 503 "Service busy", so we retry patiently by default
                (8 attempts, 15s exponential backoff) instead of giving up after
                a couple of tries. Override per call or via ``AGNES_IMAGE_*`` env.
        """
        self.api_key = api_key
        self.model = model
        self.i2i_model = i2i_model or model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

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
        use_i2i = len(reference_image_paths) > 0
        model = self.i2i_model if use_i2i else self.model
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "size": size or "1024x1024",
            "n": 1,
        }

        if kwargs.get("negative_prompt"):
            payload["negative_prompt"] = kwargs["negative_prompt"]

        if reference_image_paths:
            resolved = [await resolve_image_ref_async(p) for p in reference_image_paths]
            # All official i2i examples use the image-array form
            # (extra_body.image=[url]); we always send an array, even for a
            # single image, to stay consistent with the official protocol.
            payload["extra_body"] = {
                "response_format": "url",
                "image": resolved,
            }

        logger.info(f"[AgnesImage] Generating ({'i2i' if use_i2i else 't2i'}): {prompt[:80]}...")

        async def _collect(*, status_code, response, attempt, exc=None, final=False):
            # Identical error-collection semantics for every provider (P0-2).
            await collect_provider_error(
                prompt,
                status_code=status_code,
                response=response,
                attempt=attempt,
                exc=exc,
                final=final,
            )

        resp = await retryable_post(
            provider_tag="[AgnesImage]",
            url=f"{BASE_URL}/images/generations",
            headers=self.headers,
            json_payload=payload,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_base_delay=retry_base_delay
            if retry_base_delay is not None
            else self.retry_base_delay,
            size=size,
            collect=_collect,
        )
        return await self._parse_response(resp, prompt)

    async def _parse_response(self, resp: Response, prompt: str) -> ImageOutput:
        result = resp.json()

        if "error" in result:
            err = result["error"]
            error_msg = f"Agnes image error: {err.get('message', err)}"
            await asyncio.to_thread(
                collect_error,
                "image",
                "generate_single_image",
                prompt=prompt,
                error_type="APIError",
                error_message=error_msg,
                response_body=resp.text,
                retry_count=0,
            )
            raise RuntimeError(error_msg)

        data_list = result.get("data", [])
        if not data_list:
            await asyncio.to_thread(
                collect_error,
                "image",
                "generate_single_image",
                prompt=prompt,
                error_type="NoDataError",
                error_message="Agnes image: no data returned",
                response_body=resp.text,
                retry_count=0,
            )
            raise RuntimeError("Agnes image: no data returned")

        url = data_list[0].get("url", "")
        if not url:
            b64_data = data_list[0].get("b64_json", "")
            if b64_data:
                logger.info("[AgnesImage] Got base64 response, saving...")
                return ImageOutput(fmt="b64", ext="png", data=b64_data)
            await asyncio.to_thread(
                collect_error,
                "image",
                "generate_single_image",
                prompt=prompt,
                error_type="NoOutputError",
                error_message="Agnes image: no URL or base64 in response",
                response_body=resp.text,
                retry_count=0,
            )
            raise RuntimeError("Agnes image: no URL or base64 in response")

        logger.info(f"[AgnesImage] Done: {url[:80]}...")
        return ImageOutput(fmt="url", ext="png", data=url)
