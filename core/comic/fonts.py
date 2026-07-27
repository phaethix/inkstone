"""core.comic.fonts — dialogue font resolution with CJK fallback.

Pillow's built-in default font contains no CJK glyphs, so Chinese / Japanese /
Korean dialogue silently renders as hollow tofu boxes (□□□). Resolution order:

1. explicit ``font_path`` argument (per-engine override);
2. ``INKSTONE_FONT_PATH`` environment variable;
3. platform-known CJK font locations (PingFang / Microsoft YaHei / Noto CJK …);
4. Pillow's default font as last resort — the caller logs a loud warning,
   because silent degradation is worse than no degradation.

Fonts are cached module-level: the pipeline renders hundreds of bubbles and
must not rescan the filesystem per bubble.
"""

import logging
import os
import threading

from PIL import Image, ImageDraw, ImageFont

from core.config import font_path as env_font_path

logger = logging.getLogger(__name__)

# Size used when a CJK-capable truetype font replaces Pillow's small builtin
# bitmap font. Bubble boxes expand vertically to fit, so a slightly larger
# face only makes bubbles taller, never clipped.
DEFAULT_CJK_FONT_SIZE = 14

# Private-use codepoint: virtually every font maps it to .notdef, so it works
# as a "tofu probe" when comparing against a real Han glyph.
_NOTDEF_PROBE = "\U000e0001"
_HAN_PROBE = "汉"

# Platform-known CJK font candidates, tried in order.
_CJK_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    # Linux (Noto CJK packaging varies by distro)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

_cache: dict[tuple[str, int], tuple[ImageFont.FreeTypeFont, str]] = {}
_negative_cache: set[tuple[str, int]] = set()
_warned_no_cjk = False
_lock = threading.Lock()


def text_requires_cjk(text: str) -> bool:
    """True if the text contains CJK ideographs, kana, hangul, or fullwidth forms."""
    for ch in text or "":
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= o <= 0x4DBF  # Extension A
            or 0xF900 <= o <= 0xFAFF  # Compatibility Ideographs
            or 0x3040 <= o <= 0x30FF  # Hiragana + Katakana
            or 0xAC00 <= o <= 0xD7AF  # Hangul syllables
            or 0x3000 <= o <= 0x303F  # CJK punctuation (。「」…)
            or 0xFF01 <= o <= 0xFF60  # fullwidth forms (！（）：)
        ):
            return True
    return False


def _mask_bytes(font, ch: str) -> bytes:
    """Render one glyph onto a small normalized canvas and return its bytes.

    Works for both bitmap and FreeType fonts (``ImagingCore`` from ``getmask``
    has no portable byte interface, so we render explicitly).
    """
    try:
        bbox = font.getbbox(ch)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= 0 or h <= 0:
            return b""
        canvas = Image.new("L", (w + 4, h + 4), 0)
        ImageDraw.Draw(canvas).text((2 - bbox[0], 2 - bbox[1]), ch, fill=255, font=font)
        return canvas.tobytes()
    except Exception:  # noqa: BLE001 — font backends vary; treat failure as no coverage
        return b""


def font_covers_cjk(font) -> bool:
    """Heuristic: True if the font renders a Han glyph differently from .notdef.

    Tofu boxes are glyphs too — width/pixel checks alone cannot detect them.
    Comparing a Han render against a private-use codepoint's render can:
    identical bytes mean both came out as the same .notdef box.
    """
    han = _mask_bytes(font, _HAN_PROBE)
    if not han or not any(han):
        return False
    return han != _mask_bytes(font, _NOTDEF_PROBE)


def _try_load(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        font = ImageFont.truetype(path, size)
    except OSError:
        return None
    return font if font_covers_cjk(font) else None


def _find_cjk_font(size: int, font_path: str | None) -> tuple[ImageFont.FreeTypeFont, str] | None:
    """Locate a CJK-capable font. Positive and negative results are cached."""
    candidates: list[tuple[str, str]] = []
    if font_path:
        candidates.append((font_path, f"font_path:{font_path}"))
    env_path = env_font_path()
    if env_path:
        candidates.append((env_path, f"INKSTONE_FONT_PATH:{env_path}"))
    candidates.extend((p, p) for p in _CJK_FONT_CANDIDATES)

    with _lock:
        for path, _source in candidates:
            key = (path, size)
            if key in _negative_cache:
                continue
            if key in _cache:
                return _cache[key]
        for path, source in candidates:
            key = (path, size)
            if key in _negative_cache or key in _cache:
                continue
            font = _try_load(path, size)
            if font is not None:
                _cache[key] = (font, source)
                logger.info("CJK dialogue font resolved: %s", source)
                return _cache[key]
            _negative_cache.add(key)
    return None


def _warn_no_cjk_font() -> None:
    global _warned_no_cjk
    with _lock:
        if _warned_no_cjk:
            return
        _warned_no_cjk = True
    logger.warning(
        "No CJK-capable font found; CJK dialogue will render as empty boxes. "
        "Install a CJK font (e.g. Noto Sans CJK) or set INKSTONE_FONT_PATH to a "
        ".ttf/.ttc file."
    )


def resolve_font(
    text: str,
    *,
    size: int = DEFAULT_CJK_FONT_SIZE,
    font_path: str | None = None,
) -> tuple[object, str]:
    """Return ``(font, source)`` suitable for drawing ``text``.

    Non-CJK text keeps Pillow's builtin default font (current bubble look is
    unchanged). CJK text gets the first CJK-capable font from the resolution
    chain, or the default font plus a one-time loud warning when none exists.
    """
    if not text_requires_cjk(text):
        return ImageFont.load_default(), "default"
    found = _find_cjk_font(size, font_path)
    if found is not None:
        return found
    _warn_no_cjk_font()
    return ImageFont.load_default(), "default-no-cjk"


def reset_caches() -> None:
    """Clear font caches and the warning latch. Intended for tests."""
    global _warned_no_cjk
    with _lock:
        _cache.clear()
        _negative_cache.clear()
        _warned_no_cjk = False
