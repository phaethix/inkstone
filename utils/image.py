"""utils.image — image download and reference resolution helpers.

- :func:`download_image` fetches a URL to disk with only a ``User-Agent``
  header (no auth), a size cap, and tenacity retries.
- :func:`resolve_image_ref` converts a local image path into a base64 data URI,
  leaving URLs / existing data URIs untouched. This is the single, shared
  implementation that both providers use — the previous four copy-pasted
  variants (including the dead ``image_path_to_b64``) are gone.
"""

import asyncio
import base64
import ipaddress
import logging
import mimetypes
import os
import socket
from urllib.parse import urljoin, urlparse

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt

logger = logging.getLogger(__name__)

# Guard against filling disk: cap download size.
_MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50 MB

# Downloads carry only a User-Agent header, never auth.
USER_AGENT = "Inkstone/0.1 (+https://github.com/phaethix/inkstone)"

# A public URL that 302s into the metadata service is the usual SSRF shape.
_MAX_REDIRECTS = 3

# Names that are metadata endpoints even when DNS is attacker-controlled.
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.com",
        "metadata.tencentyun.com",
        "metadata.aliyun.com",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

# Cloud metadata addresses that are not covered by ``ip.is_global`` alone.
# 100.100.100.200 is Alibaba's metadata service and is a public unicast address.
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class UnsafeImageUrl(ValueError):
    """The image URL is not safe to fetch from this machine."""


def _log_target(url: str) -> str:
    """Host and path only. Query strings on CDN URLs are often signed."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return f"{parsed.scheme}://{host}{parsed.path}"


def _parse_ipv4_part(part: str) -> int | None:
    try:
        if part.lower().startswith("0x"):
            value = int(part, 16)
        elif len(part) > 1 and part.startswith("0") and part.isdigit():
            value = int(part, 8)
        elif part.isdigit():
            value = int(part, 10)
        else:
            return None
    except ValueError:
        return None
    if 0 <= value <= 255:
        return value
    return None


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Interpret a host that is already an address, including decimal and octal forms."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host.isdigit():
        value = int(host)
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.ip_address(value)
        return None
    parts = host.split(".")
    if len(parts) == 4:
        numbers = [_parse_ipv4_part(part) for part in parts]
        if all(number is not None for number in numbers):
            return ipaddress.ip_address(".".join(str(number) for number in numbers))
    return None


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_ip(ip.ipv4_mapped)
    if ip in _METADATA_IPS:
        return True
    # ``is_global`` is false for loopback, private, link-local, multicast,
    # reserved, and documentation ranges.
    return not ip.is_global


def _addresses_for(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    literal = _literal_ip(host)
    if literal is not None:
        return [literal]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeImageUrl(f"cannot resolve image host {host}") from exc
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        found.append(ipaddress.ip_address(info[4][0]))
    if not found:
        raise UnsafeImageUrl(f"cannot resolve image host {host}")
    return found


def assert_image_url(url: str, *, allow_private: bool = False) -> None:
    """Reject unsafe schemes, credentials, metadata names, and non-public addresses.

    ``allow_private`` is a test seam for the local HTTP server that proves
    downloads ignore shell proxies. Production callers must leave it false.
    """
    if not url or len(url) > 2048 or any(ord(char) < 32 or char.isspace() for char in url):
        raise UnsafeImageUrl("image URL is empty or contains whitespace")
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise UnsafeImageUrl("image URL must not carry credentials")
    scheme = (parsed.scheme or "").lower()
    if allow_private:
        if scheme not in {"http", "https"}:
            raise UnsafeImageUrl(f"unsupported image URL scheme {scheme or '(none)'}")
        return
    if scheme != "https":
        raise UnsafeImageUrl("image URL must use https")
    host = parsed.hostname
    if not host or "%" in host:
        raise UnsafeImageUrl("image URL is missing a host")
    normalized = host.lower().rstrip(".")
    if normalized in _METADATA_HOSTS:
        raise UnsafeImageUrl(f"refusing metadata host {normalized}")
    # Imported here, not at module scope: ``core.api`` imports this module.
    from core.config import image_download_hosts

    allowed = image_download_hosts()
    if allowed and normalized not in allowed:
        raise UnsafeImageUrl(f"image host {normalized} is not in the download allowlist")
    port = parsed.port or 443
    for ip in _addresses_for(normalized, port):
        if _blocked_ip(ip):
            raise UnsafeImageUrl(f"refusing non-public address {ip} for {normalized}")


def _download_once(url: str, save_path: str, max_size: int, *, allow_private: bool) -> None:
    # Imported here, not at module scope: ``core.api`` imports this module, so a
    # top-level import would be a cycle.
    from core.api.retry import upstream_session

    # Ignore shell proxies for the same reason upstream POSTs do: a stale
    # ``HTTPS_PROXY`` aimed at a dead proxy raises ``ProxyError`` and all three
    # attempts fail, so a generated image never reaches disk.
    session = upstream_session()
    current = url
    for hop in range(_MAX_REDIRECTS + 1):
        assert_image_url(current, allow_private=allow_private)
        resp = session.get(
            current,
            headers={"User-Agent": USER_AGENT},
            timeout=(30, 120),
            stream=True,
            allow_redirects=False,
        )
        try:
            if resp.is_redirect:
                if hop == _MAX_REDIRECTS:
                    raise UnsafeImageUrl("too many image URL redirects")
                location = resp.headers.get("Location")
                if not location:
                    raise UnsafeImageUrl("image URL redirect is missing Location")
                current = urljoin(current, location)
                continue
            resp.raise_for_status()
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > max_size:
                raise ValueError(f"Image too large: {content_length} bytes > max {max_size} bytes")
            downloaded = 0
            with open(save_path, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        raise ValueError(
                            f"Image exceeded max_size {max_size} bytes during download"
                        )
                    handle.write(chunk)
        finally:
            resp.close()
        logger.info(f"Image saved to {save_path} ({downloaded} bytes)")
        return
    raise UnsafeImageUrl("too many image URL redirects")


@retry(
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def download_image(
    url: str,
    save_path: str,
    max_size: int = _MAX_IMAGE_SIZE,
    *,
    allow_private: bool = False,
) -> None:
    """Download ``url`` to ``save_path`` with a size cap (no auth header).

    Redirects are followed manually and each hop is checked again. Private,
    loopback, link-local, and metadata addresses are refused. ``http`` and
    other non-https schemes are refused unless ``allow_private`` is set.
    """
    logger.info(f"Downloading image from {_log_target(url)} to {save_path}")
    _download_once(url, save_path, max_size, allow_private=allow_private)


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
    """Async variant that reads local files off the event loop."""
    return await asyncio.to_thread(resolve_image_ref, ref)
