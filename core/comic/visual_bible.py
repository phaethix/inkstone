"""core.comic.visual_bible — hash, reconcile apply, and ref helpers for Visual Bible."""

from __future__ import annotations

import hashlib
import json
from core.comic.identity import merge_character_alias, suggestion_from_alias
from core.schemas import (
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ProjectState,
    VisualBible,
    VisualBibleReconcileResult,
)

COSTUME_CHANGE_LOCK_LINE = (
    "do not change hair color, outfit colors, or skin tone across panels "
    "unless action says costume change"
)


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
    elif result.color is not None:
        bible.color = result.color


def apply_reconcile(
    state: ProjectState,
    result: VisualBibleReconcileResult,
) -> ProjectState:
    """Apply reconcile merges, stage links, and low-confidence review rows."""
    out = state.model_copy(deep=True)

    _install_reconcile_bible(out, result)

    for merge in result.merges:
        if merge.confidence == "high":
            merge_character_alias(out, merge.alias, merge.canonical)
            if out.visual_bible is not None:
                _ensure_canon_alias(out.visual_bible, merge.canonical, merge.alias)
        else:
            suggestion = suggestion_from_alias(merge.alias, merge.canonical, merge.reason)
            if not any(
                s.new_name == suggestion.new_name and s.candidate == suggestion.candidate
                for s in out.needs_review
            ):
                out.needs_review.append(suggestion)

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
    canon = bible.characters.get(base)
    if canon is not None:
        stage_row = next((s for s in canon.stages if s.stage == stage), None)
        if stage_row is not None and stage_row.portrait_key:
            key = stage_row.portrait_key
            char = characters_by_name.get(key)
            if char is not None and char.portrait_local:
                return char.portrait_local
    char = characters_by_name.get(base)
    if char is not None and char.portrait_local:
        return char.portrait_local
    char = characters_by_name.get(name)
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
