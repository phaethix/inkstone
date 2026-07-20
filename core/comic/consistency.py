"""core.comic.consistency — character-consistency engine.

Implements the L1 prompt-hardening lever first: the highest-value, zero-cost
consistency mechanism. L2 (multi-image reference) and L3
(PIL/OpenCV feature compositing fallback) are added in subsequent steps; this
module starts with L1 so the core contract is testable in isolation.

L1 rule: every panel containing a character must inline that character's
hardened ``l1_prompt``; a panel with several characters inlines each one, joined
in order. This is the consistency backbone and costs no extra API call.
"""

from collections.abc import Iterable

from core.schemas import CharacterAsset, Setting


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
    """Character-consistency engine. Starts with the L1 prompt-hardening lever."""

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
        """Build an L1-hardened panel prompt.

        Composition: ``scene_prompt + (each character's l1_prompt) + action + style``.
        The character descriptions are inlined verbatim so panel identity does not
        depend on reference images — this is the consistency backbone and costs
        no extra API call. Empty segments are dropped so partial inputs still read
        as a clean comma-separated prompt.
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
