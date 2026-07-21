"""core.comic.consistency — character-consistency engine.

Three complementary techniques keep a character looking the same across panels:

- **Prompt hardening** (zero cost): every panel inlines each character's
  hardened English description (``l1_prompt``) so identity does not depend on
  reference images.
- **Multi-image reference** (auxiliary): ``collect_reference_images`` assembles
  the reference paths (character portraits + previous panel) fed into the image
  provider's ``generate_single_image(reference_image_paths=[...])``.
- **Feature compositing** (best-effort fallback): ``apply_l3`` pastes the
  portrait face back onto the generated panel. It is **optional** — ``cv2`` is
  lazily imported and, if absent or if the quality guards fail, the panel is
  returned unchanged. This is an engineering safety net, not a strong
  consistency solution; it only guarantees the face "looks like" the portrait.
"""

import logging
from collections.abc import Iterable

from PIL import Image

from core.schemas import CharacterAsset, Panel, Setting

logger = logging.getLogger(__name__)


def _scene_prompt_of(setting) -> str:
    """Resolve a setting-like input to its ``scene_prompt`` string."""
    if setting is None:
        return ""
    if isinstance(setting, Setting):
        return setting.scene_prompt or ""
    if isinstance(setting, dict):
        return setting.get("scene_prompt", "") or ""
    if isinstance(setting, str):
        return setting
    return ""


class ConsistencyEngine:
    """Character-consistency engine. Builds panel prompts by inlining each
    character's hardened description, and can composite portrait faces back
    onto generated panels as a fallback."""

    def __init__(self, style_guide: str = ""):
        self.style_guide = style_guide or ""

    def build_panel_prompt(
        self,
        *,
        characters: "CharacterAsset | Iterable[CharacterAsset]",
        setting: "Setting | dict | str | None",
        action: str,
        style_guide: str | None = None,
    ) -> str:
        """Build a panel prompt by inlining each character's hardened description.

        Composition: ``scene_prompt + (each character's l1_prompt) + action + style``.
        The character descriptions are inlined verbatim so panel identity does not
        depend on reference images — this costs no extra API call. Empty segments
        are dropped so partial inputs still read as a clean comma-separated prompt.
        """
        scene = _scene_prompt_of(setting)
        if isinstance(characters, CharacterAsset):
            chars = [characters]
        else:
            chars = list(characters)
        l1_parts = [c.l1_prompt for c in chars if getattr(c, "l1_prompt", "")]
        style = style_guide if style_guide is not None else self.style_guide

        parts = [p for p in [scene, *l1_parts, action, style] if p]
        return ", ".join(parts)

    # ------------------------------------------------------------------ #
    # Multi-image reference collection
    # ------------------------------------------------------------------ #
    def collect_reference_images(
        self,
        *,
        panel: "Panel | dict",
        characters_by_name: "dict[str, CharacterAsset]",
        prev_panel_local: str | None = None,
        max_refs: int = 9,
    ) -> list[str]:
        """Assemble the reference-image paths for one panel.

        Returns the local paths of each referenced character's portrait plus the
        previous panel (if given), in order, de-duplicated, and capped at
        ``max_refs`` (the image provider accepts at most nine references).
        Paths whose character has no ``portrait_local`` are skipped. Existence
        filtering is left to the caller (the pipeline only passes freshly
        generated portraits).

        Typical use::

            refs = engine.collect_reference_images(
                panel=panel,
                characters_by_name=state.characters,
                prev_panel_local=prev_local,
            )
            img = provider.generate_single_image(panel_prompt, refs, size)
        """
        if isinstance(panel, Panel):
            names = list(panel.reference_characters)
        elif isinstance(panel, dict):
            names = list(panel.get("reference_characters", []) or [])
        else:
            names = []

        paths: list[str] = []
        seen: set[str] = set()
        for name in names:
            asset = characters_by_name.get(name)
            loc = getattr(asset, "portrait_local", None) if asset else None
            if loc and loc not in seen:
                paths.append(loc)
                seen.add(loc)
        if prev_panel_local and prev_panel_local not in seen:
            paths.append(prev_panel_local)
            seen.add(prev_panel_local)
        return paths[:max_refs]

    # ------------------------------------------------------------------ #
    # Feature compositing fallback
    # ------------------------------------------------------------------ #
    def apply_l3(
        self,
        panel_img,
        portrait_img,
        *,
        min_face_ratio: float = 0.02,
        max_face_ratio: float = 0.60,
        feather: int = 12,
    ) -> Image.Image:
        """Composite the portrait face onto the generated panel.

        A best-effort fallback: detect the largest face in both images, resize
        the portrait face to the panel face box, match its lighting, and blend
        with a feathered alpha edge. Quality guards (face-ratio bounds, missing
        detections, any failure) cause a **graceful skip** — the original panel
        is returned unchanged. ``cv2`` is optional: without it this step is a no-op.

        Args:
            panel_img: a ``PIL.Image`` or a path to the generated panel.
            portrait_img: a ``PIL.Image`` or a path to the character portrait.
            min_face_ratio / max_face_ratio: skip if a face occupies less than
                2% or more than 60% of its image (anti-miscomposite guard).
            feather: gaussian sigma (px) for the alpha edge blend.

        Returns:
            The (possibly) composited ``PIL.Image`` in RGB mode.
        """
        panel_pil = _to_pil(panel_img)
        portrait_pil = _to_pil(portrait_img)
        try:
            import cv2  # lazy: optional dependency
            import numpy as np
        except ImportError:
            logger.warning("face compositing skipped: cv2 not installed; panel unchanged")
            return panel_pil
        try:
            return _apply_l3_cv2(
                panel_pil,
                portrait_pil,
                cv2,
                np,
                min_face_ratio,
                max_face_ratio,
                feather,
            )
        except Exception as exc:  # noqa: BLE001 — compositing is best-effort
            logger.warning("face compositing aborted (%s); returning panel unchanged", exc)
            return panel_pil


def _to_pil(img) -> Image.Image:
    """Accept a ``PIL.Image`` or a path and return a ``PIL.Image``."""
    if isinstance(img, Image.Image):
        return img
    return Image.open(img)


def _detect_faces(cascade, rgb, cv2) -> list[tuple[int, int, int, int]]:
    """Return faces ``(x, y, w, h)`` on an RGB array, largest first."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    faces = [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in found]
    faces.sort(key=lambda f: f[2] * f[3], reverse=True)
    return faces


def _apply_l3_cv2(
    panel_pil, portrait_pil, cv2, np, min_face_ratio, max_face_ratio, feather
) -> Image.Image:
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        logger.warning("face compositing skipped: haar cascade XML not found")
        return panel_pil

    panel_rgb = np.array(panel_pil.convert("RGB"))
    port_rgb = np.array(portrait_pil.convert("RGB"))
    panel_bgr = cv2.cvtColor(panel_rgb, cv2.COLOR_RGB2BGR)
    port_bgr = cv2.cvtColor(port_rgb, cv2.COLOR_RGB2BGR)

    p_faces = _detect_faces(cascade, panel_rgb, cv2)
    x_faces = _detect_faces(cascade, port_rgb, cv2)
    if not x_faces:
        logger.info("face compositing skipped: no face detected in portrait")
        return panel_pil
    if not p_faces:
        logger.info("face compositing skipped: no face detected in panel")
        return panel_pil

    # Quality guard: face must occupy a sane fraction of each image.
    (x0, y0, xw, xh) = x_faces[0]
    (p0, p1, pw, ph) = p_faces[0]
    port_area = port_rgb.shape[0] * port_rgb.shape[1]
    panel_area = panel_rgb.shape[0] * panel_rgb.shape[1]
    if not (
        min_face_ratio <= xw * xh / port_area <= max_face_ratio
        and min_face_ratio <= pw * ph / panel_area <= max_face_ratio
    ):
        logger.info("face compositing skipped: face ratio outside guard bounds")
        return panel_pil

    # Align: resize portrait face to the panel face box.
    src = cv2.resize(port_bgr[y0 : y0 + xh, x0 : x0 + xw], (pw, ph))
    # Color match: shift mean per-channel toward the panel face lighting.
    src = src.astype(np.float32)
    dst_roi = panel_bgr[p1 : p1 + ph, p0 : p0 + pw].astype(np.float32)
    src += dst_roi.reshape(-1, 3).mean(0) - src.reshape(-1, 3).mean(0)
    src = np.clip(src, 0, 255).astype(np.uint8)

    # Feathered alpha blend.
    mask = np.zeros((ph, pw), np.float32)
    cv2.ellipse(mask, (pw // 2, ph // 2), (pw // 2, ph // 2), 0, 0, 360, 1, -1)
    sigma = max(1, min(feather, min(pw, ph) // 4))
    mask = cv2.GaussianBlur(mask, (0, 0), sigma)[..., None]
    blended = dst_roi * (1 - mask) + src * mask
    panel_bgr[p1 : p1 + ph, p0 : p0 + pw] = blended.astype(np.uint8)

    return Image.fromarray(cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2RGB))
