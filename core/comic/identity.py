"""core.comic.identity — character/setting identity ledger helpers.

Owns Appearance→L1 derivation, project-level settings merge, alias
merge/dismiss (never silent), and force-regen bookkeeping for selective redraw.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.comic.ledger import ConsistencyLedger
from core.schemas import (
    Appearance,
    CharacterAliasSuggestion,
    CharacterAsset,
    ComicPagePlan,
    ProjectState,
    Setting,
    StoryElements,
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


def page_state_key(chunk_index: int, page_index: int) -> str:
    """Return the positional identity for one finished-page position.

    Phase 0b (§4/§9): a page's identity is its position — ``c{ci:04d}-p{idx:04d}``
    — matching the convention panel keys already use. The model-generated
    ``ComicPagePlan.page_id`` is deliberately **not** part of this key: a replan
    that renames pages would otherwise orphan every recorded page.
    """
    return f"c{chunk_index:04d}-p{page_index:04d}"


def name_suggests_animal_metaphor(name: str) -> bool:
    """True when ``name`` contains a common animal-metaphor ideograph."""
    return any(ch in _ANIMAL_METAPHOR_CHARS for ch in name or "")


def metaphor_identity_lock_line(name: str) -> str:
    """One-line human-only lock for finished-page / portrait prompts."""
    return (
        f"- {name}: human person with a normal human face; Chinese nickname only — "
        f"NOT a literal animal; NO tiger/dragon head, NO fur, NO snout, NO anthropomorphic beast"
    )


def metaphor_names_on_page(
    plan: ComicPagePlan,
    characters_by_name: dict[str, CharacterAsset],
) -> list[str]:
    """Collect metaphorical animal-glyph names referenced on a finished page."""
    found: set[str] = set()
    for name in plan.reference_characters or []:
        if name_suggests_animal_metaphor(name):
            found.add(name)
    for panel in plan.panels:
        for name in panel.characters:
            if name_suggests_animal_metaphor(name):
                found.add(name)
        action = panel.action or ""
        for name in characters_by_name:
            if name_suggests_animal_metaphor(name) and name in action:
                found.add(name)
    return sorted(found)


def harden_human_identity_prompt(name: str, prompt: str) -> str:
    """Prefix an anti-literalization lock for metaphorical animal names.

    Non-metaphor names are returned unchanged. Idempotent if the lock is already
    present.
    """
    text = (prompt or "").strip()
    if not name_suggests_animal_metaphor(name):
        return text
    lower = text.lower()
    if "metaphorical" in lower and "not an animal" in lower and "human character" in lower:
        return text
    core = text
    if core.startswith(name):
        core = core[len(name) :].lstrip(" ,;—-")
    lead = (
        f"human character — {name} is a metaphorical Chinese nickname only "
        f"(NOT a literal tiger/dragon/animal); draw a normal human face and body"
    )
    if core:
        return f"{lead}; {core}; {_HUMAN_LOCK}"
    return f"{lead}; {_HUMAN_LOCK}"


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


def verify_evidence_against_source(
    appearance: Appearance,
    source_text: str | None,
) -> list[str]:
    """Return the list of evidence field names whose quote is NOT in ``source_text``.

    An empty list means all evidence passed. A ``None`` ``source_text`` skips
    verification and returns an empty list (legacy callers remain safe).
    Quotes found verbatim in the source are considered verified; anything else
    is treated as unverified so downstream prompts can flag it.
    """
    if not source_text:
        return []
    unverified: list[str] = []
    for item in appearance.appearance_evidence or []:
        if item.quote not in source_text:
            unverified.append(item.field)
    return unverified


def _evidence_suffix(quotes: Iterable[str]) -> str:
    """Format verbatim evidence quotes as a compact ``; source: “…”`` suffix."""
    fragments = [f"“{q}”" for q in quotes if (q or "").strip()]
    if not fragments:
        return ""
    return "; source: " + "; ".join(fragments)


def ensure_character_l1(
    char: CharacterAsset,
    source_text: str | None = None,
) -> CharacterAsset:
    """Fill ``l1_prompt`` from Appearance when appearance has content.

    Structured Appearance is the authority whenever any appearance field is set.
    Otherwise keep an existing LLM ``l1_prompt``, or fall back to name/role only.

    When ``source_text`` is provided, every appearance_evidence quote is checked
    against the source text; unverified entries are appended to the prompt as
    ``⚠ unverified`` warnings so the image model (and humans reviewing state)
    can see which appearance claims are not grounded in the source excerpt.
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

        evidence = char.appearance.appearance_evidence or []
        if source_text is not None:
            # Verification path: inject ONLY quotes found verbatim in the
            # source; fabricated/unverified quotes are excluded from the prompt
            # and surfaced as visible ⚠ warnings instead.
            unverified = verify_evidence_against_source(char.appearance, source_text)
            verified_quotes = [it.quote for it in evidence if it.quote in source_text]
            suffix = _evidence_suffix(verified_quotes)
            if suffix:
                char.l1_prompt = f"{char.l1_prompt}{suffix}"
            if not evidence:
                char.l1_prompt = (
                    f"{char.l1_prompt}; ⚠ no source evidence; using generic placeholder"
                )
            elif unverified:
                char.l1_prompt = (
                    f"{char.l1_prompt}; ⚠ unverified evidence for: {', '.join(unverified)}"
                )
        else:
            # Legacy / no-source callers: no verification is possible, but we
            # still carry the evidence quotes into the prompt so the source
            # fidelity grounding actually reaches the image model.
            suffix = _evidence_suffix(it.quote for it in evidence)
            if suffix:
                char.l1_prompt = f"{char.l1_prompt}{suffix}"

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
    ledger: ConsistencyLedger | None = None,
) -> list[str]:
    """Merge ``new_name`` into ``keep_name`` and mark affected panels stale.

    Never runs from the public review UI without an explicit merge action.
    The visual-bible sanitizer may also call this to collapse duplicate canons.
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
    # §12 consequence 3: prefer the ledger's precise page set for the alias;
    # fall back to walking page_cache when the ledger has no entry for it, so a
    # project that has not yet rebuilt its ledger still invalidates correctly.
    ledger_pages = ledger.pages_for(new_name) if ledger is not None else []
    stale_pages: list[str] = list(ledger_pages)
    for cache_key, pageset in state.page_cache.items():
        try:
            chunk_index = int(cache_key)
        except ValueError:
            continue
        for page_index, plan in enumerate(pageset.pages):
            names = set(plan.reference_characters)
            for panel in plan.panels:
                names.update(panel.characters)
            if new_name in names:
                key = page_state_key(chunk_index, page_index)
                if key not in stale_pages:
                    stale_pages.append(key)
        for plan in pageset.pages:
            plan.reference_characters = _rewrite_names(
                plan.reference_characters, new_name, keep_name
            )
            for panel in plan.panels:
                panel.characters = _rewrite_names(panel.characters, new_name, keep_name)
    if ledger is not None:
        ledger.rename_character(new_name, keep_name)
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
    if stale_pages:
        done_pages = set(state.pages_done)
        stale_page_set = set(state.stale_pages)
        for key in stale_pages:
            done_pages.discard(key)
            stale_page_set.add(key)
        state.pages_done = [k for k in state.pages_done if k in done_pages]
        state.stale_pages = sorted(stale_page_set)
    return list(stale)


def _same_alias(suggestion: CharacterAliasSuggestion, new_name: str, candidate: str) -> bool:
    """True when both rows name the same two people, in either order.

    A later extract can introduce the canonical name first. The decision stays
    the one that was recorded; only a merge's stored direction says which name
    is kept.
    """
    return {suggestion.new_name, suggestion.candidate} == {new_name, candidate}


def offer_alias_suggestion(state: ProjectState, suggestion: CharacterAliasSuggestion) -> None:
    """Queue an alias pair unless a person already decided that pair."""
    pair = (suggestion.new_name, suggestion.candidate)
    dismissed = any(_same_alias(s, *pair) for s in state.dismissed_aliases)
    merged = any(_same_alias(s, *pair) for s in state.merged_aliases)
    queued = any(_same_alias(s, *pair) for s in state.needs_review)
    if dismissed or merged or queued:
        return
    state.needs_review.append(suggestion)


def remember_merged_alias(state: ProjectState, new_name: str, candidate: str) -> None:
    """Record a human merge so a later extract folds the same pair again."""
    if not any(_same_alias(s, new_name, candidate) for s in state.merged_aliases):
        kept = next(
            (s for s in state.needs_review if _same_alias(s, new_name, candidate)),
            None,
        )
        record = kept
        if record is None:
            record = CharacterAliasSuggestion(
                new_name=new_name,
                candidate=candidate,
                reason="merged",
            )
        state.merged_aliases.append(record)
    state.needs_review = [s for s in state.needs_review if not _same_alias(s, new_name, candidate)]
    state.dismissed_aliases = [
        s for s in state.dismissed_aliases if not _same_alias(s, new_name, candidate)
    ]


def apply_recorded_merges(
    state: ProjectState,
    ledger: ConsistencyLedger | None = None,
) -> None:
    """Fold aliases a person already merged, when both names are present again."""
    for record in list(state.merged_aliases):
        if record.new_name not in state.characters or record.candidate not in state.characters:
            continue
        merge_character_alias(state, record.new_name, record.candidate, ledger=ledger)
        remember_merged_alias(state, record.new_name, record.candidate)


def elements_with_recorded_merges(elements: StoryElements, state: ProjectState) -> StoryElements:
    """Return a plan-facing copy whose alias names use the merged canonical."""
    alias_to_keep = {s.new_name: s.candidate for s in state.merged_aliases}
    if not alias_to_keep:
        return elements
    rewritten: list[CharacterAsset] = []
    seen: set[str] = set()
    changed = False
    for character in elements.characters:
        name = alias_to_keep.get(character.name, character.name)
        if name != character.name:
            changed = True
            character = character.model_copy(update={"name": name})
        if name in seen:
            changed = True
            continue
        seen.add(name)
        rewritten.append(character)
    if not changed:
        return elements
    return elements.model_copy(update={"characters": rewritten})


def dismiss_character_alias(
    state: ProjectState,
    new_name: str,
    candidate: str,
) -> None:
    """Remove a review suggestion without merging identities.

    The pair is recorded so a later extract cannot put it back on the queue.
    """
    if not any(_same_alias(s, new_name, candidate) for s in state.dismissed_aliases):
        kept = next(
            (s for s in state.needs_review if _same_alias(s, new_name, candidate)),
            None,
        )
        record = kept
        if record is None:
            record = CharacterAliasSuggestion(
                new_name=new_name,
                candidate=candidate,
                reason="dismissed",
            )
        state.dismissed_aliases.append(record)
    state.needs_review = [
        s for s in state.needs_review if not _same_alias(s, new_name, candidate)
    ]


def force_regen_panels(state: ProjectState, keys: list[str]) -> None:
    """Mark panel keys for regeneration (clears done/skipped; adds stale)."""
    key_set = set(keys)
    state.panels_done = [k for k in state.panels_done if k not in key_set]
    state.skipped = [k for k in state.skipped if k not in key_set]
    stale = set(state.stale_panels)
    stale.update(key_set)
    state.stale_panels = sorted(stale)


def clear_tombstones(state: ProjectState, keys: list[str]) -> list[str]:
    """Release tombstoned pages so they are retried on the next run.

    Design §6: a tombstone's key never changes, so without this override a page
    rejected in error would be unretryable forever. §7 ``rebuild --stage render
    --key <k>`` is the CLI face of this function, and §9 requires the tombstone
    and its override to ship in the same phase — never ship tombstones first.

    Returns the keys that actually had a tombstone, so a caller can report what
    changed rather than claiming success for keys that were never tombstones.
    """
    key_set = set(keys)
    released = [k for k in keys if k in state.tombstones]
    for key in released:
        del state.tombstones[key]
    # ``skipped_pages`` is the legacy skip set the pipeline still consults when
    # deciding whether a page needs generating; leaving the key there would keep
    # the page skipped and make the override a no-op.
    state.skipped_pages = [k for k in state.skipped_pages if k not in key_set]
    state.stale_pages = [k for k in state.stale_pages if k not in key_set]
    return released


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
