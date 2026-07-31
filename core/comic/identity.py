"""core.comic.identity — character/setting identity ledger helpers.

Owns Appearance→L1 derivation, project-level settings merge, alias
merge/dismiss (never silent), and force-regen bookkeeping for selective redraw.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.schemas import (
    Appearance,
    CharacterAliasSuggestion,
    CharacterAsset,
    ProjectState,
    Setting,
)

_HIGH_CONFIDENCE_MARKERS = (
    "name variant",
    "normalized/substring",
    "substring match",
)

# Chinese literary nicknames often embed animal/plant glyphs metaphorically
# (虎妞, 凤姐). Image models literalize those glyphs unless prompts forbid it.
_ANIMAL_METAPHOR_CHARS = frozenset("虎龙凤豹狼狮猴蛇鹤狐兔熊鹰")

_HUMAN_LOCK = (
    "human person only — the name is metaphorical; not an animal, no animal head, "
    "no fur, no tail, no snout (not a tiger/dragon/phoenix creature)"
)


def name_suggests_animal_metaphor(name: str) -> bool:
    """True when ``name`` contains a common animal-metaphor ideograph."""
    return any(ch in _ANIMAL_METAPHOR_CHARS for ch in name or "")


def harden_human_identity_prompt(name: str, prompt: str) -> str:
    """Append an anti-literalization lock for metaphorical animal names.

    Non-metaphor names are returned unchanged. Idempotent if the lock is already
    present.
    """
    text = (prompt or "").strip()
    if not name_suggests_animal_metaphor(name):
        return text
    if "not an animal" in text.lower() and "human" in text.lower():
        return text
    base = text or name
    return f"{base}, {_HUMAN_LOCK}"


def build_l1_from_appearance(
    name: str,
    appearance: Appearance,
    role: str = "",
) -> str:
    """Build a hardened English-ish L1 identity string from structured appearance."""
    parts: list[str] = []
    if name:
        parts.append(name)
    if role:
        parts.append(role)
    for attr in (
        appearance.hair,
        appearance.eyewear,
        appearance.outfit_top,
        appearance.outfit_bottom,
        appearance.shoes,
        appearance.body_type,
        appearance.distinguishing,
    ):
        value = (attr or "").strip()
        if value:
            parts.append(value)
    return ", ".join(parts)


def ensure_character_l1(char: CharacterAsset) -> CharacterAsset:
    """Fill ``l1_prompt`` from Appearance when appearance has content.

    Structured Appearance is the authority whenever any appearance field is set.
    Otherwise keep an existing LLM ``l1_prompt``, or fall back to name/role only.
    """
    has_appearance = any(
        (getattr(char.appearance, field) or "").strip()
        for field in (
            "hair",
            "eyewear",
            "outfit_top",
            "outfit_bottom",
            "shoes",
            "body_type",
            "distinguishing",
        )
    )
    derived = build_l1_from_appearance(char.name, char.appearance, role=char.role)
    if has_appearance:
        char.l1_prompt = derived
    elif not (char.l1_prompt or "").strip() and derived:
        char.l1_prompt = derived
    if char.l1_prompt:
        char.l1_prompt = harden_human_identity_prompt(char.name, char.l1_prompt)
    if char.portrait_prompt:
        char.portrait_prompt = harden_human_identity_prompt(char.name, char.portrait_prompt)
    return char


def merge_settings(
    existing: dict[str, Setting],
    new: Iterable[Setting],
) -> dict[str, Setting]:
    """Merge settings by exact name; keep first non-empty field values."""
    merged = {k: v.model_copy(deep=True) for k, v in existing.items()}
    for setting in new:
        if setting.name not in merged:
            merged[setting.name] = setting.model_copy(deep=True)
            continue
        cur = merged[setting.name]
        if not (cur.description or "").strip() and (setting.description or "").strip():
            cur.description = setting.description
        if not (cur.scene_prompt or "").strip() and (setting.scene_prompt or "").strip():
            cur.scene_prompt = setting.scene_prompt
    return merged


def is_high_confidence_alias(reason: str) -> bool:
    """Return True for substring / normalized variant reasons (suggested merge)."""
    lower = (reason or "").lower()
    return any(marker in lower for marker in _HIGH_CONFIDENCE_MARKERS)


def _rewrite_names(names: list[str], old: str, new: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        mapped = new if n == old else n
        if mapped not in seen:
            out.append(mapped)
            seen.add(mapped)
    return out


def _panel_keys_referencing(
    state: ProjectState,
    character_name: str,
) -> list[str]:
    """Collect panel state keys whose storyboard lists ``character_name``."""
    keys: list[str] = []
    for chunk_key, cache in state.chunk_cache.items():
        try:
            chunk_index = int(chunk_key)
        except ValueError:
            continue
        board = cache.storyboard
        if board is None:
            continue
        for panel_index, panel in enumerate(board.panels):
            present = set(panel.characters_present) | set(panel.reference_characters)
            if character_name in present:
                keys.append(f"c{chunk_index:04d}-p{panel_index:04d}")
    # Also include generated panel records that still point at this name via source.
    for key, gen in state.generated.panels.items():
        if key not in keys and character_name in (gen.source_panel_id or ""):
            # source_panel_id is LLM id — unreliable; skip name match here.
            pass
    return keys


def merge_character_alias(
    state: ProjectState,
    new_name: str,
    keep_name: str,
) -> list[str]:
    """Merge ``new_name`` into ``keep_name`` and mark affected panels stale.

    Never runs automatically — callers invoke this from review UI/API.
    Returns the list of panel state keys marked stale.
    """
    if keep_name not in state.characters:
        raise KeyError(f"keep character not found: {keep_name!r}")
    if new_name == keep_name:
        return []

    keep = state.characters[keep_name]
    incoming = state.characters.get(new_name)

    if incoming is not None:
        # Fill empty appearance / prompt fields on keep from the alias row.
        for field in (
            "hair",
            "eyewear",
            "outfit_top",
            "outfit_bottom",
            "shoes",
            "body_type",
            "distinguishing",
        ):
            if not (getattr(keep.appearance, field) or "").strip():
                setattr(keep.appearance, field, getattr(incoming.appearance, field))
        if not (keep.role or "").strip() and (incoming.role or "").strip():
            keep.role = incoming.role
        if not (keep.portrait_prompt or "").strip() and (incoming.portrait_prompt or "").strip():
            keep.portrait_prompt = incoming.portrait_prompt
        if not keep.portrait_local and incoming.portrait_local:
            keep.portrait_local = incoming.portrait_local
            state.generated.portraits[keep_name] = incoming.portrait_local
        for alias in incoming.aliases:
            if alias not in keep.aliases and alias != keep_name:
                keep.aliases.append(alias)
        del state.characters[new_name]
        state.generated.portraits.pop(new_name, None)

    if new_name not in keep.aliases:
        keep.aliases.append(new_name)
    ensure_character_l1(keep)

    stale = _panel_keys_referencing(state, new_name)
    # Rewrite cached storyboards after collecting keys.
    for cache in state.chunk_cache.values():
        board = cache.storyboard
        if board is None:
            continue
        for panel in board.panels:
            panel.characters_present = _rewrite_names(panel.characters_present, new_name, keep_name)
            panel.reference_characters = _rewrite_names(
                panel.reference_characters, new_name, keep_name
            )

    state.needs_review = [
        s
        for s in state.needs_review
        if not (
            (s.new_name == new_name and s.candidate == keep_name)
            or (s.new_name == keep_name and s.candidate == new_name)
        )
    ]

    done = set(state.panels_done)
    stale_set = set(state.stale_panels)
    for key in stale:
        done.discard(key)
        stale_set.add(key)
    state.panels_done = [k for k in state.panels_done if k in done]
    state.stale_panels = sorted(stale_set)
    return list(stale)


def dismiss_character_alias(
    state: ProjectState,
    new_name: str,
    candidate: str,
) -> None:
    """Remove a review suggestion without merging identities."""
    state.needs_review = [
        s for s in state.needs_review if not (s.new_name == new_name and s.candidate == candidate)
    ]


def force_regen_panels(state: ProjectState, keys: list[str]) -> None:
    """Mark panel keys for regeneration (clears done/skipped; adds stale)."""
    key_set = set(keys)
    state.panels_done = [k for k in state.panels_done if k not in key_set]
    state.skipped = [k for k in state.skipped if k not in key_set]
    stale = set(state.stale_panels)
    stale.update(key_set)
    state.stale_panels = sorted(stale)


def suggestion_from_alias(
    new_name: str,
    candidate: str,
    reason: str,
) -> CharacterAliasSuggestion:
    """Build a review row with ``suggested`` from the detector reason."""
    return CharacterAliasSuggestion(
        new_name=new_name,
        candidate=candidate,
        reason=reason,
        suggested=is_high_confidence_alias(reason),
    )
