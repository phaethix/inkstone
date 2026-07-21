"""core.comic.consistency — character-consistency engine.

Three complementary techniques keep a character looking the same across panels:

- **Prompt hardening** (zero cost): every panel inlines each character's
  hardened English description (``l1_prompt``) so identity does not depend on
  reference images.
- **Multi-image reference** (auxiliary): ``collect_reference_images`` assembles
  the reference paths (character portraits + previous panel) fed into the image
  provider's ``generate_single_image(reference_image_paths=[...])``.
- **Feature compositing** (best-effort fallback): ``apply_l3`` transplants the
  portrait face onto the generated panel using **Poisson seamless cloning**
  (``cv2.seamlessClone``) with **eye-angle alignment** and **strict quality
  guards**. It is deliberately conservative: it only composites when the panel
  face is a reasonably sized, comparably shaped, near-frontal region — exactly
  the cases where a swap helps. On far/wide shots (tiny panel face), pose
  mismatch, missing ``cv2``, or any failure, it **returns the panel unchanged**
  so it never introduces a visible "pasted-on" seam. This is an engineering
  safety net, not a strong consistency solution.
"""

import logging
import math
import os
from collections.abc import Iterable

from PIL import Image

from core.schemas import CharacterAsset, Panel, Setting

logger = logging.getLogger(__name__)

# Below this absolute panel-face size (px, min side), a face swap on a far/wide
# shot produces a visible seam and Haar detection is unreliable, so L3 is skipped
# and the t2i/i2i result is kept as-is.
MIN_PANEL_FACE_PX = 80
# The portrait and panel face boxes must have similar aspect (Haar boxes are
# ~square; a big aspect gap implies different pose) — else skip to avoid distortion.
MAX_ASPECT_MISMATCH = 0.35
# The two faces must be within this size-ratio window; extreme up/downscaling of
# the source face blurs detail and looks wrong, so skip instead.
MIN_SIZE_RATIO = 0.35
MAX_SIZE_RATIO = 3.0


def _truthy_env(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


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

    def __init__(self, style_guide: str = "", enable_l3: bool | None = None):
        self.style_guide = style_guide or ""
        # L3 face compositing is **opt-in and OFF by default** (``INKSTONE_L3=1``
        # or ``enable_l3=True`` to turn on). It is a cv2 Haar-based face-swap that
        # pastes a close-up portrait face onto the generated panel; on stylized
        # comic art the pose/angle/lighting rarely match, so it frequently
        # *deforms* the face and looks worse than the raw generation. Character
        # consistency is therefore carried by L1 (prompt hardening) + L2
        # (reference-image conditioning of the generation model), which is the
        # robust path. Keep L3 only for users who want to experiment.
        self.enable_l3 = _truthy_env("INKSTONE_L3", default="0") if enable_l3 is None else enable_l3

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
        """Transplant the portrait face onto the generated panel (seamless).

        Detects the largest face in both images, aligns the portrait face to the
        panel face (scale + eye-angle rotation), and blends it in with **Poisson
        seamless cloning** (``cv2.seamlessClone``) through a feathered elliptical
        mask, so lighting/color match across the boundary and there is no visible
        "pasted-on" seam.

        It is deliberately conservative. Any of these causes a **graceful skip**
        (the original panel object is returned unchanged), because compositing
        would look worse than the raw generation:

        - ``cv2`` not installed, or L3 disabled (``INKSTONE_L3=0``);
        - no face detected in the portrait or the panel;
        - the panel face is tiny (far/wide shot) — see ``MIN_PANEL_FACE_PX``;
        - the two face boxes differ too much in aspect or size (pose mismatch);
        - the face occupies an implausible fraction of its image;
        - any runtime failure during blending.

        Args:
            panel_img: a ``PIL.Image`` or a path to the generated panel.
            portrait_img: a ``PIL.Image`` or a path to the character portrait.
            min_face_ratio / max_face_ratio: skip if a face occupies less than
                2% or more than 60% of its image (anti-miscomposite guard).
            feather: gaussian sigma (px) for the mask edge.

        Returns:
            The (possibly) composited ``PIL.Image`` in RGB mode. When skipped,
            the input ``PIL.Image`` object is returned unchanged.
        """
        panel_pil = _to_pil(panel_img)
        portrait_pil = _to_pil(portrait_img)
        if not self.enable_l3:
            logger.info("face compositing disabled (INKSTONE_L3=0); panel unchanged")
            return panel_pil
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


def _load_cascade(cv2, name: str):
    """Load a bundled Haar cascade; return ``None`` if unavailable."""
    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + name)
    except Exception:  # noqa: BLE001 — cv2 build without data / older API
        return None
    return None if cascade.empty() else cascade


def _detect_faces(cascade, rgb, cv2) -> list[tuple[int, int, int, int]]:
    """Return faces ``(x, y, w, h)`` on an RGB array, largest first."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    faces = [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in found]
    faces.sort(key=lambda f: f[2] * f[3], reverse=True)
    return faces


def _eye_angle(rgb, face, cv2) -> float | None:
    """Roll angle (degrees) of a face from its two largest detected eyes.

    Returns ``None`` when fewer than two eyes are found (angle unknown).
    """
    eye_cascade = _load_cascade(cv2, "haarcascade_eye.xml")
    if eye_cascade is None:
        return None
    (fx, fy, fw, fh) = face
    roi = cv2.cvtColor(rgb[fy : fy + fh, fx : fx + fw], cv2.COLOR_RGB2GRAY)
    eyes = eye_cascade.detectMultiScale(
        roi, scaleFactor=1.1, minNeighbors=6, minSize=(max(8, fw // 8), max(8, fh // 8))
    )
    if len(eyes) < 2:
        return None
    # Two biggest eyes, ordered left-to-right, then the angle of the line joining them.
    biggest = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = sorted(biggest, key=lambda e: e[0])
    c1 = (lx + lw / 2, ly + lh / 2)
    c2 = (rx + rw / 2, ry + rh / 2)
    return math.degrees(math.atan2(c2[1] - c1[1], c2[0] - c1[0]))


def _rotate(img, angle: float, cv2):
    """Rotate ``img`` about its center by ``angle`` degrees, keeping its size."""
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def _guards_pass(port_face, panel_face, port_area, panel_area, min_face_ratio, max_face_ratio):
    """Return ``True`` only when a face swap is safe (else L3 should skip).

    The decisive "is the face big enough to swap" signal is the **absolute**
    panel-face size (``MIN_PANEL_FACE_PX``), not a fraction of the full frame:
    comic panels show the subject small in-frame, so a perfectly usable 115px
    face would otherwise read as a ~1% sliver and get rejected. Relative ratios
    are only used as *upper* bounds (anti-misdetect) and to block *upscaling*
    blur — downscaling the (hi-res) portrait onto a smaller in-scene face is the
    normal, safe case and is explicitly allowed.
    """
    (_, _, xw, xh) = port_face
    (_, _, pw, ph) = panel_face
    # Upper-bound only: a face filling most of the frame is almost certainly a
    # Haar misdetection (on the panel) or a face-only crop (portrait) — skip.
    if xw * xh / port_area > max_face_ratio or pw * ph / panel_area > max_face_ratio:
        logger.info("face compositing skipped: face ratio above guard bound")
        return False
    # Absolute-size gate: only composite when the panel face carries enough
    # pixels for a clean swap (tiny far/wide-shot faces look wrong AND defeat
    # Haar alignment).
    if min(pw, ph) < MIN_PANEL_FACE_PX:
        logger.info("face compositing skipped: panel face too small (%dpx)", min(pw, ph))
        return False
    # Aspect-compatibility guard: very different aspect => different pose/angle.
    a_src, a_dst = xw / xh, pw / ph
    if abs(a_src - a_dst) / max(a_src, a_dst) > MAX_ASPECT_MISMATCH:
        logger.info("face compositing skipped: face aspect mismatch")
        return False
    # Upscale guard only: block when the panel face is far LARGER than the
    # portrait face (enlarging the source would blur). Downscaling the portrait
    # is fine, so there is intentionally no lower bound here.
    ratio = (pw * ph) / (xw * xh)
    if ratio > MAX_SIZE_RATIO:
        logger.info("face compositing skipped: panel face far larger than portrait (%.2f)", ratio)
        return False
    return True


def _apply_l3_cv2(
    panel_pil, portrait_pil, cv2, np, min_face_ratio, max_face_ratio, feather
) -> Image.Image:
    cascade = _load_cascade(cv2, "haarcascade_frontalface_default.xml")
    if cascade is None:
        logger.warning("face compositing skipped: haar cascade XML not found")
        return panel_pil

    panel_rgb = np.array(panel_pil.convert("RGB"))
    port_rgb = np.array(portrait_pil.convert("RGB"))

    p_faces = _detect_faces(cascade, panel_rgb, cv2)
    x_faces = _detect_faces(cascade, port_rgb, cv2)
    if not x_faces:
        logger.info("face compositing skipped: no face detected in portrait")
        return panel_pil
    if not p_faces:
        logger.info("face compositing skipped: no face detected in panel")
        return panel_pil

    port_face, panel_face = x_faces[0], p_faces[0]
    port_area = port_rgb.shape[0] * port_rgb.shape[1]
    panel_area = panel_rgb.shape[0] * panel_rgb.shape[1]
    if not _guards_pass(
        port_face, panel_face, port_area, panel_area, min_face_ratio, max_face_ratio
    ):
        return panel_pil

    (x0, y0, xw, xh) = port_face
    (p0, p1, pw, ph) = panel_face

    # Source patch: portrait face, roll-aligned to the panel face, resized to fit.
    src = port_rgb[y0 : y0 + xh, x0 : x0 + xw]
    src_angle = _eye_angle(port_rgb, port_face, cv2)
    dst_angle = _eye_angle(panel_rgb, panel_face, cv2)
    if src_angle is not None and dst_angle is not None:
        delta = dst_angle - src_angle
        # Only correct meaningful, plausible tilt; ignore detection noise/outliers.
        if 3.0 <= abs(delta) <= 35.0:
            src = _rotate(src, delta, cv2)
    src = cv2.resize(src, (pw, ph), interpolation=cv2.INTER_CUBIC)

    # Feathered elliptical mask over the face region.
    mask = np.zeros((ph, pw), np.uint8)
    cv2.ellipse(mask, (pw // 2, ph // 2), (int(pw * 0.46), int(ph * 0.46)), 0, 0, 360, 255, -1)
    sigma = max(1, min(feather, min(pw, ph) // 6))
    mask = cv2.GaussianBlur(mask, (0, 0), sigma)

    # Poisson seamless clone: gradient-domain blend matches lighting/color across
    # the boundary, eliminating the visible seam of a plain alpha composite.
    center = (int(p0 + pw / 2), int(p1 + ph / 2))
    blended = cv2.seamlessClone(src, panel_rgb, mask, center, cv2.NORMAL_CLONE)
    return Image.fromarray(blended)
