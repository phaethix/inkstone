"""core.comic.visual_bible — hash, reconcile apply, and ref helpers for Visual Bible."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from core.comic.identity import merge_character_alias, suggestion_from_alias
import logging

from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
    VisualBible,
    VisualBibleReconcileResult,
)

logger = logging.getLogger(__name__)

COSTUME_CHANGE_LOCK_LINE = (
    "do not change hair color, outfit colors, or skin tone across panels "
    "unless action says costume change"
)

ANTI_CHARACTER_SHEET_LINE = (
    "NO character design sheets, turnarounds, model sheets, or multi-view "
    "reference collages inside the page."
)

PERIOD_WARDROBE_LINE = (
    "Period-accurate wardrobe only; no modern hoodies, sneakers, or athleisure "
    "unless action explicitly requires costume change."
)

ANTI_MULTI_AGE_COLLAGE_LINE = (
    "Do not depict multiple age versions of the same person on one page unless "
    "layout_intent explicitly calls for a flashback split."
)

_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_PROSE_MARKER_RE = re.compile(
    r"(?i)(,|\bwith\b|\bhair\b|\bexpression\b|\bwearing\b|\bbuild\b|\beyes\b|\bage\b|\bold\b)",
)
_OUTFIT_WORD_RE = re.compile(
    r"(?i)\b("
    r"wearing|hoodie|athletic|sneakers|jacket|suit|dress|skirt|pants|boots|coat|"
    r"sweater|jeans|uniform|robe|vest|tie|blouse|shirt|trousers|athleisure"
    r")\b",
)

_MOTHER_ROLE_MARKERS = ("母", "妈", "mother", "widow", "寡妇")
_DAUGHTER_ROLE_MARKERS = ("女", "孩", "narrator", "少女", "女儿", "叙述者")
_COUNT_LOVER_ROLE_MARKERS = ("伯爵", "count", "工厂主", "情人")
_NOVELIST_ROLE_MARKERS = ("小说家", "作家", "novelist")
_SERVANT_ROLE_MARKERS = ("仆", "butler", "约翰")
_MASTER_ROLE_MARKERS = ("主人", "novelist", "作家")


def is_illegal_character_name(name: str) -> bool:
    """True when ``name`` looks like English prose description, not a character label."""
    text = (name or "").strip()
    if not text:
        return False
    ascii_count = len(_ASCII_LETTER_RE.findall(text))
    if len(text) > 40 and ascii_count >= 10:
        return True
    if text.count(",") >= 2 and ascii_count >= max(len(text) // 3, 8):
        return True
    if ascii_count > 0 and _PROSE_MARKER_RE.search(text):
        if ascii_count >= max(len(text) // 4, 6):
            return True
    return False


def _role_contains_any(role: str, markers: tuple[str, ...]) -> bool:
    lower = role.casefold()
    for marker in markers:
        if marker in role or marker.casefold() in lower:
            return True
    return False


def roles_incompatible(role_a: str, role_b: str) -> bool:
    """True when two role strings describe incompatible person identities."""
    a = (role_a or "").strip()
    b = (role_b or "").strip()
    if not a or not b:
        return False

    def _pair(left: str, right: str, markers_a: tuple[str, ...], markers_b: tuple[str, ...]) -> bool:
        return _role_contains_any(left, markers_a) and _role_contains_any(right, markers_b)

    incompatible_pairs = (
        (_MOTHER_ROLE_MARKERS, _DAUGHTER_ROLE_MARKERS),
        (_DAUGHTER_ROLE_MARKERS, _MOTHER_ROLE_MARKERS),
        (_COUNT_LOVER_ROLE_MARKERS, _NOVELIST_ROLE_MARKERS),
        (_NOVELIST_ROLE_MARKERS, _COUNT_LOVER_ROLE_MARKERS),
        (_SERVANT_ROLE_MARKERS, _MASTER_ROLE_MARKERS),
        (_MASTER_ROLE_MARKERS, _SERVANT_ROLE_MARKERS),
    )
    return any(_pair(a, b, ma, mb) for ma, mb in incompatible_pairs)


def normalize_face_lock(text: str) -> str:
    """Strip outfit-related words so ``face_lock`` stays facial-only."""
    stripped = re.sub(r",?\s*wearing[^,;]*", "", (text or "").strip(), flags=re.IGNORECASE)
    parts: list[str] = []
    for part in re.split(r"[,;]", stripped):
        chunk = part.strip()
        if not chunk or _OUTFIT_WORD_RE.search(chunk):
            continue
        parts.append(chunk)
    return ", ".join(parts).strip()


_HAIR_MARKER_RE = re.compile(
    r"(?i)\b(hair|bald|balding|curly|straight|braid|ponytail)\b|[发髻鬃]",
)


def _default_hair_lock() -> str:
    return "dark hair"


def _hair_lock_from_canon_face(canon_face: str) -> str | None:
    """Derive a short hair lock from the first ``canon_face`` clause when it mentions hair."""
    text = (canon_face or "").strip()
    if not text:
        return None
    first_clause = re.split(r"[,;]", text, maxsplit=1)[0].strip()
    if not first_clause or not _HAIR_MARKER_RE.search(first_clause):
        return None
    # Hair locks are brief identity tags, not full face prose.
    return first_clause[:80].strip()


def _default_outfit_lock(style_hint: str) -> str:
    hint = (style_hint or "").strip()
    if hint:
        return f"{hint} period clothing"
    return "early 20th century European period clothing"


def ensure_stage_locks(
    stage: CharacterStage,
    *,
    canon_face: str,
    style_hint: str = "",
    canonical_name: str = "",
) -> CharacterStage:
    """Fill empty stage locks and repair illegal ``portrait_key`` values."""
    hair_lock = (stage.hair_lock or "").strip()
    if not hair_lock:
        # Prefer a short hair hint from the canon face's first clause before generic default.
        hair_lock = _hair_lock_from_canon_face(canon_face) or _default_hair_lock()
    outfit_lock = (stage.outfit_lock or "").strip() or _default_outfit_lock(style_hint)
    portrait_key = (stage.portrait_key or "").strip()
    if canonical_name and (not portrait_key or is_illegal_character_name(portrait_key)):
        portrait_key = f"{canonical_name}@{stage.stage}"
    return CharacterStage(
        stage=stage.stage,
        appearance=stage.appearance,
        outfit_lock=outfit_lock,
        hair_lock=hair_lock,
        portrait_key=portrait_key,
    )


def ensure_canon_locks(canon: CharacterCanon, style_hint: str = "") -> CharacterCanon:
    """Normalize face lock and ensure every stage has hair/outfit/portrait locks."""
    face_lock = normalize_face_lock(canon.face_lock) or (canon.face_lock or "").strip()
    stages = [
        ensure_stage_locks(
            stage,
            canon_face=face_lock,
            style_hint=style_hint,
            canonical_name=canon.canonical_name,
        )
        for stage in canon.stages
    ]
    return canon.model_copy(update={"face_lock": face_lock, "stages": stages})


def _drop_incompatible_aliases(
    aliases: list[str],
    owner_role: str,
    state: ProjectState,
) -> list[str]:
    """Keep only aliases that are legal names and role-compatible with ``owner_role``."""
    kept: list[str] = []
    for alias in aliases:
        if is_illegal_character_name(alias):
            continue
        alias_role = _role_for_character(state, alias)
        if roles_incompatible(alias_role, owner_role):
            continue
        kept.append(alias)
    return kept


def sanitize_visual_bible_state(state: ProjectState) -> bool:
    """Clean polluted bible/character state and bump to bible_v2. Returns True if mutated."""
    bible = state.visual_bible
    if bible is None:
        return False

    mutated = False

    illegal_character_keys = [
        name for name in list(state.characters) if is_illegal_character_name(name)
    ]
    for name in illegal_character_keys:
        del state.characters[name]
        mutated = True

    for asset in state.characters.values():
        cleaned = _drop_incompatible_aliases(asset.aliases, asset.role or "", state)
        if cleaned != asset.aliases:
            asset.aliases = cleaned
            mutated = True

    style_hint = bible.style_guide or ""
    illegal_canon_keys = [
        key for key in list(bible.characters) if is_illegal_character_name(key)
    ]
    for key in illegal_canon_keys:
        del bible.characters[key]
        mutated = True

    for key, canon in list(bible.characters.items()):
        owner_role = canon.role or _role_for_character(state, key)
        cleaned_aliases = _drop_incompatible_aliases(canon.aliases, owner_role, state)
        fixed = ensure_canon_locks(
            canon.model_copy(update={"aliases": cleaned_aliases}),
            style_hint=style_hint,
        )
        if cleaned_aliases != canon.aliases or fixed.model_dump() != canon.model_dump():
            mutated = True
        bible.characters[key] = fixed

    if bible.version != "bible_v2":
        bible.version = "bible_v2"
        mutated = True

    old_hash = bible.content_hash
    state.visual_bible = refresh_bible_hash(bible)
    if state.visual_bible.content_hash != old_hash:
        mutated = True

    return mutated


def parse_stage_ref(name: str) -> tuple[str, str]:
    """Split ``Name@stage`` into base name and stage (default ``default``)."""
    text = (name or "").strip()
    if "@" in text:
        base, stage = text.split("@", 1)
        base = base.strip()
        stage = stage.strip() or "default"
        return base, stage
    return text, "default"


def _bible_hash_payload(bible: VisualBible) -> dict:
    characters: dict[str, dict] = {}
    for name, canon in sorted(bible.characters.items()):
        stages = [
            {
                "stage": stage.stage,
                "outfit_lock": stage.outfit_lock,
                "hair_lock": stage.hair_lock,
            }
            for stage in canon.stages
        ]
        characters[name] = {
            "face_lock": canon.face_lock,
            "palette_notes": canon.palette_notes,
            "stages": stages,
        }
    return {
        "style_guide": bible.style_guide,
        "color": bible.color.model_dump(),
        "characters": characters,
    }


def compute_bible_hash(bible: VisualBible) -> str:
    """SHA-256 digest of style, color, and character locks (ignores content_hash)."""
    payload = json.dumps(_bible_hash_payload(bible), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def refresh_bible_hash(bible: VisualBible) -> VisualBible:
    """Return a copy of ``bible`` with ``content_hash`` set from current locks."""
    return bible.model_copy(update={"content_hash": compute_bible_hash(bible)})


def _ensure_canon_alias(bible: VisualBible, canonical: str, alias: str) -> None:
    if bible is None:
        return
    canon = bible.characters.get(canonical)
    if canon is None:
        return
    if alias not in canon.aliases and alias != canonical:
        canon.aliases.append(alias)


def _apply_color_patches(color: ColorBible, patches: list[ColorSwatch]) -> None:
    """Append or update palette swatches by name."""
    if not patches:
        return
    by_name = {s.name: i for i, s in enumerate(color.palette) if s.name}
    for patch in patches:
        if patch.name and patch.name in by_name:
            color.palette[by_name[patch.name]] = patch
        else:
            color.palette.append(patch)
            if patch.name:
                by_name[patch.name] = len(color.palette) - 1


def _upsert_canon(existing: CharacterCanon, incoming: CharacterCanon) -> CharacterCanon:
    """Merge incoming canon fields into an existing canonical character."""
    updates: dict = {}
    if incoming.face_lock:
        updates["face_lock"] = incoming.face_lock
    if incoming.palette_notes:
        updates["palette_notes"] = incoming.palette_notes
    if incoming.role:
        updates["role"] = incoming.role
    merged = existing.model_copy(update=updates) if updates else existing.model_copy(deep=True)

    for alias in incoming.aliases:
        if alias not in merged.aliases and alias != merged.canonical_name:
            merged.aliases.append(alias)

    stage_index = {s.stage: i for i, s in enumerate(merged.stages)}
    for stage in incoming.stages:
        if stage.stage in stage_index:
            idx = stage_index[stage.stage]
            old = merged.stages[idx]
            merged.stages[idx] = CharacterStage(
                stage=stage.stage,
                outfit_lock=stage.outfit_lock or old.outfit_lock,
                hair_lock=stage.hair_lock or old.hair_lock,
                portrait_key=stage.portrait_key or old.portrait_key,
            )
        else:
            merged.stages.append(stage)
    return merged


def _install_reconcile_bible(
    out: ProjectState,
    result: VisualBibleReconcileResult,
) -> None:
    """Create or update visual bible from reconcile style, color, and canons."""
    if out.visual_bible is None:
        out.visual_bible = VisualBible(
            version="bible_v1",
            style_guide=result.style_guide or "",
            color=result.color or ColorBible(palette=[], lighting="", forbidden=[]),
            characters={c.canonical_name: c for c in result.canons},
            sheet_ref_local=None,
            content_hash="",
        )
        return

    bible = out.visual_bible
    for canon in result.canons:
        existing = bible.characters.get(canon.canonical_name)
        if existing is None:
            bible.characters[canon.canonical_name] = canon
        else:
            bible.characters[canon.canonical_name] = _upsert_canon(existing, canon)

    if not bible.style_guide and result.style_guide:
        bible.style_guide = result.style_guide

    if result.color_patches:
        _apply_color_patches(bible.color, result.color_patches)


def _ensure_canonical_character(
    out: ProjectState,
    canonical: str,
    result: VisualBibleReconcileResult,
) -> None:
    """Ensure ``canonical`` exists in ``state.characters`` before alias merge."""
    if canonical in out.characters:
        return
    canon = None
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(canonical)
    if canon is None:
        for row in result.canons:
            if row.canonical_name == canonical:
                canon = row
                break
    if canon is not None:
        l1 = l1_from_canon(canon)
        out.characters[canonical] = CharacterAsset(
            name=canonical,
            role=canon.role or "",
            l1_prompt=l1,
            portrait_prompt=l1,
        )
        return
    for merge in result.merges:
        if merge.canonical == canonical and merge.alias in out.characters:
            asset = out.characters[merge.alias]
            out.characters[canonical] = asset.model_copy(update={"name": canonical})
            return


def _append_needs_review(out: ProjectState, suggestion) -> None:
    if not any(
        s.new_name == suggestion.new_name and s.candidate == suggestion.candidate
        for s in out.needs_review
    ):
        out.needs_review.append(suggestion)


def _role_for_character(out: ProjectState, name: str) -> str:
    asset = out.characters.get(name)
    if asset is not None and (asset.role or "").strip():
        return asset.role.strip()
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(name)
        if canon is not None and (canon.role or "").strip():
            return canon.role.strip()
    return ""


def apply_reconcile(
    state: ProjectState,
    result: VisualBibleReconcileResult,
) -> ProjectState:
    """Apply reconcile merges, stage links, and low-confidence review rows."""
    out = state.model_copy(deep=True)

    _install_reconcile_bible(out, result)

    for merge in result.merges:
        if merge.confidence == "high":
            role_alias = _role_for_character(out, merge.alias)
            role_canon = _role_for_character(out, merge.canonical)
            if roles_incompatible(role_alias, role_canon):
                suggestion = suggestion_from_alias(merge.alias, merge.canonical, merge.reason)
                _append_needs_review(out, suggestion)
                continue
            _ensure_canonical_character(out, merge.canonical, result)
            try:
                merge_character_alias(out, merge.alias, merge.canonical)
            except KeyError as exc:
                logger.warning(
                    "visual bible merge skipped (%s → %s): %s",
                    merge.alias,
                    merge.canonical,
                    exc,
                )
            if out.visual_bible is not None:
                _ensure_canon_alias(out.visual_bible, merge.canonical, merge.alias)
        else:
            suggestion = suggestion_from_alias(merge.alias, merge.canonical, merge.reason)
            _append_needs_review(out, suggestion)

    if out.visual_bible is not None:
        for link in result.stages:
            canon = out.visual_bible.characters.get(link.of_canonical)
            if canon is None:
                continue
            _ensure_canon_alias(out.visual_bible, link.of_canonical, link.name)
            existing = {s.stage for s in canon.stages}
            if link.stage not in existing:
                canon.stages.append(
                    CharacterStage(
                        stage=link.stage,
                        outfit_lock="",
                        hair_lock="",
                        portrait_key=f"{link.of_canonical}@{link.stage}",
                    )
                )

    return out


def alias_to_canonical_map(bible: VisualBible) -> dict[str, str]:
    """Public alias of ``_build_alias_to_canonical_map`` for pipeline callers."""
    return _build_alias_to_canonical_map(bible)


def _build_alias_to_canonical_map(bible: VisualBible) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        for alias in canon.aliases:
            if alias and alias != canonical:
                mapping[alias] = canonical
    return mapping


def rewrite_pageset_from_bible(pageset: ComicPagePlanSet, bible: VisualBible) -> ComicPagePlanSet:
    """Rewrite panel/reference names in ``pageset`` using bible alias map."""
    mapping = _build_alias_to_canonical_map(bible)
    if not mapping:
        return pageset
    return pageset.model_copy(
        update={
            "pages": [rewrite_page_plan_names(plan, mapping) for plan in pageset.pages],
        }
    )


def ensure_stage_portrait_assets(state: ProjectState) -> None:
    """Ensure each stage ``portrait_key`` has a ``CharacterAsset`` for rendering."""
    bible = state.visual_bible
    if bible is None:
        return
    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        base_asset = state.characters.get(canonical)
        for stage in canon.stages:
            portrait_key = (stage.portrait_key or "").strip()
            if not portrait_key or portrait_key == canonical:
                continue
            if portrait_key in state.characters:
                continue
            l1 = l1_from_canon(canon, stage.stage)
            if base_asset is not None and not l1:
                state.characters[portrait_key] = base_asset.model_copy(update={"name": portrait_key})
            else:
                state.characters[portrait_key] = CharacterAsset(
                    name=portrait_key,
                    role=canon.role or (base_asset.role if base_asset else ""),
                    l1_prompt=l1,
                    portrait_prompt=l1,
                )


def resolve_canonical_name(name: str, bible: VisualBible | None) -> str:
    """Resolve ``name`` to canonical bible character name when possible."""
    if bible is None:
        return name
    base, _stage = parse_stage_ref(name)
    if base in bible.characters:
        return base
    mapping = _build_alias_to_canonical_map(bible)
    return mapping.get(base, base)


def resolve_character_asset(
    name: str,
    characters_by_name: dict[str, CharacterAsset],
    bible: VisualBible | None = None,
) -> CharacterAsset | None:
    """Look up a character asset, resolving bible aliases and stage portrait keys."""
    if name in characters_by_name:
        return characters_by_name[name]
    if bible is None:
        return None
    base, stage = parse_stage_ref(name)
    canon = bible.characters.get(base)
    if canon is None:
        canonical = resolve_canonical_name(base, bible)
        canon = bible.characters.get(canonical)
        base = canonical
    if canon is not None:
        stage_row = next((s for s in canon.stages if s.stage == stage), None)
        if stage_row is not None and stage_row.portrait_key:
            key = stage_row.portrait_key
            if key in characters_by_name:
                return characters_by_name[key]
    canonical = resolve_canonical_name(base, bible)
    return characters_by_name.get(canonical)


def sync_characters_from_bible(state: ProjectState) -> None:
    """Sync character L1 prompts from bible canons and rewrite cached page plans."""
    bible = state.visual_bible
    if bible is None:
        return

    mapping = _build_alias_to_canonical_map(bible)

    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        asset = state.characters.get(canonical)
        if asset is None:
            continue
        l1 = l1_from_canon(canon)
        if l1:
            asset.l1_prompt = l1
            asset.portrait_prompt = l1
        for alias in canon.aliases:
            if alias and alias != canonical and alias not in asset.aliases:
                asset.aliases.append(alias)

    ensure_stage_portrait_assets(state)

    if not mapping:
        return

    for cache_key, pageset in list(state.page_cache.items()):
        state.page_cache[cache_key] = rewrite_pageset_from_bible(pageset, bible)


def format_color_bible_block(bible: VisualBible) -> str:
    """Format palette, lighting, and forbidden colors for image prompts."""
    color = bible.color
    lines: list[str] = []
    for swatch in color.palette:
        if not swatch.hex:
            continue
        label = swatch.name or swatch.usage or "color"
        detail = f"{label} {swatch.hex}"
        if swatch.usage and swatch.name and swatch.usage != swatch.name:
            detail = f"{swatch.name} {swatch.hex} ({swatch.usage})"
        lines.append(detail)
    if color.lighting:
        lines.append(f"lighting: {color.lighting}")
    if color.forbidden:
        lines.append(f"forbidden: {', '.join(color.forbidden)}")
    if not lines:
        return ""
    return "Color bible:\n" + "\n".join(f"  {line}" for line in lines)


def l1_from_canon(canon: CharacterCanon, stage: str = "default") -> str:
    """Build an L1 identity string from canon face lock and stage outfit/hair locks."""
    parts: list[str] = []
    if canon.face_lock:
        parts.append(canon.face_lock)
    if canon.palette_notes:
        parts.append(canon.palette_notes)
    stage_row = next((s for s in canon.stages if s.stage == stage), None)
    if stage_row is None and canon.stages:
        stage_row = canon.stages[0]
    if stage_row is not None:
        if stage_row.outfit_lock:
            parts.append(stage_row.outfit_lock)
        if stage_row.hair_lock:
            parts.append(stage_row.hair_lock)
    return ", ".join(parts)


def _rewrite_name_list(names: list[str], mapping: dict[str, str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        mapped = mapping.get(name, name)
        if mapped not in seen:
            out.append(mapped)
            seen.add(mapped)
    return out


def backfill_panel_characters(
    plan: ComicPagePlan,
    known_names: Iterable[str],
) -> ComicPagePlan:
    """Fill empty panel ``characters`` from page refs and action substring matches."""
    known = [name for name in known_names if name]
    updated = plan.model_copy(deep=True)
    for panel in updated.panels:
        if panel.characters:
            continue
        found: list[str] = []
        seen: set[str] = set()
        for name in updated.reference_characters:
            if name not in seen:
                found.append(name)
                seen.add(name)
        action = panel.action or ""
        for name in known:
            if name in action and name not in seen:
                found.append(name)
                seen.add(name)
        panel.characters = found
    return updated


def rewrite_page_plan_names(
    plan: ComicPagePlan,
    mapping: dict[str, str],
) -> ComicPagePlan:
    """Rewrite panel and reference character names using ``mapping``."""
    updated = plan.model_copy(deep=True)
    updated.reference_characters = _rewrite_name_list(updated.reference_characters, mapping)
    for panel in updated.panels:
        panel.characters = _rewrite_name_list(panel.characters, mapping)
    return updated


def build_visual_sheet(bible: VisualBible) -> None:
    """Phase B stub — visual sheet generation is deferred to phase C."""
    return None


def _page_character_names(plan: ComicPagePlan) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for name in plan.reference_characters:
        if name not in seen:
            names.append(name)
            seen.add(name)
    for panel in plan.panels:
        for name in panel.characters:
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _portrait_path_for_name(
    name: str,
    characters_by_name: dict,
    bible: VisualBible,
) -> str | None:
    base, stage = parse_stage_ref(name)
    canonical = resolve_canonical_name(base, bible)
    canon = bible.characters.get(canonical)
    if canon is not None:
        stage_row = next((s for s in canon.stages if s.stage == stage), None)
        if stage_row is not None and stage_row.portrait_key:
            key = stage_row.portrait_key
            char = characters_by_name.get(key)
            if char is not None and char.portrait_local:
                return char.portrait_local
    char = resolve_character_asset(name, characters_by_name, bible)
    if char is not None and char.portrait_local:
        return char.portrait_local
    return None


def collect_finished_page_refs(
    plan: ComicPagePlan,
    characters_by_name: dict,
    bible: VisualBible,
    *,
    prev_blank: str | None = None,
    max_refs: int = 9,
) -> list[str]:
    """Collect i2i reference paths: sheet, portraits, then optional previous blank."""
    refs: list[str] = []
    seen: set[str] = set()

    def _add(path: str | None) -> bool:
        if not path or path in seen:
            return False
        refs.append(path)
        seen.add(path)
        return len(refs) >= max_refs

    if bible.sheet_ref_local:
        _add(bible.sheet_ref_local)

    for name in _page_character_names(plan):
        if len(refs) >= max_refs:
            break
        _add(_portrait_path_for_name(name, characters_by_name, bible))

    if len(refs) < max_refs and prev_blank:
        _add(prev_blank)

    return refs