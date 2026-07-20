"""utils.image — image download and reference resolution helpers.

- :func:`download_image` fetches a URL to disk (ADR-12.2: only a ``User-Agent``
  header, no auth) with a size cap and tenacity retries.
- :func:`resolve_image_ref` converts a local image path into a base64 data URI,
  leaving URLs / existing data URIs untouched. This is the single, shared
  implementation that both providers use (review P0-3) — the previous four
  copy-pasted variants (including the dead ``image_path_to_b64``) are gone.
"""

import asyncio
import base64
import logging
import mimetypes
import os

import requests
from tenacity import retry, stop_after_attempt

logger = logging.getLogger(__name__)

# B6: download size cap (guard against filling disk)
_MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50 MB

# ADR-12.2: download carries only a User-Agent, never auth (review P2-3).
USER_AGENT = "Inkstone/0.1 (+https://github.com/phaethix/inkstone)"


@retry(stop=stop_after_attempt(3))
def download_image(url: str, save_path: str, max_size: int = _MAX_IMAGE_SIZE) -> None:
    """Download ``url`` to ``save_path`` with a size cap (no auth header)."""
    logger.info(f"Downloading image from {url} to {save_path}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(30, 120), stream=True)
    resp.raise_for_status()
    # Prefer checking Content-Length first
    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > max_size:
        raise ValueError(f"Image too large: {content_length} bytes > max {max_size} bytes")
    downloaded = 0
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > max_size:
                raise ValueError(f"Image exceeded max_size {max_size} bytes during download")
            f.write(chunk)
    logger.info(f"Image saved to {save_path} ({downloaded} bytes)")


def resolve_image_ref(ref: str) -> str:
    """Resolve a reference image for embedding in a request.

    Remote URLs and existing ``data:`` URIs are returned as-is; a local path is
    read and returned as a base64 ``data:`` URI.
    """
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    if os.path.exists(ref):
        with open(ref, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        mime = mimetypes.guess_type(ref)[0] or "image/png"
        return f"data:{mime};base64,{b64}"
    return ref


async def resolve_image_ref_async(ref: str) -> str:
    """Async variant that reads local files off the event loop (review P1)."""
    return await asyncio.to_thread(resolve_image_ref, ref)
