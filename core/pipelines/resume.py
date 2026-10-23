"""core.pipelines.resume — what a resumed run may skip, and what it must redo.

Every function here answers one question about work already recorded, and none
of them calls a provider. They were inline in the pipeline body, where the same
three-way rule (stale wins, then done or skipped, else work) was written out four
times and never tested on its own.

The rule is deliberately asymmetric:

- **stale wins over done.** A page marked for redraw must be redone even though a
  projection record still says it finished. Anything else makes an alias merge a
  no-op.
- **skipped is not stale.** A content-policy rejection is a terminal answer about
  the source text, which did not change; retrying it only burns quota.
- **missing beats recorded.** A record with no file behind it is not done. That
  check lives with the file sweep in ``reconcile``, not here.

The lettering half takes the stage source as an argument rather than importing
``_LETTER_STAGE_SRC``. That constant belongs to the pipeline, and reaching back
for it would make this module and the pipeline import each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.comic.artifact_projection import drop_page_artifact
from core.comic.cas import letter_action_key, read_manifest_entry, stored_output
from core.comic.env_snapshot import h_env
from core.comic.identity import page_state_key
from core.comic.lettering_lang import scrub_lettering_panel
from core.comic.visual_bible import (
    backfill_panel_characters,
    canonicalize_page_plan,
    resolve_panel_stage_refs,
)
from core.comic.voice import sanitize_plan_voice
from core.pipelines.reconcile import is_within
from core.schemas import ComicPagePlan, ComicPagePlanSet, ProjectState


def plan_json(plan: ComicPagePlan) -> str:
    """Canonical bytes of a plan, so two equal plans cannot hash differently."""
    payload = plan.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def page_asset_path(pages_dir: Path, chunk_index: int, page_index: int) -> Path:
    """Deterministic ``page_XX.png``-style path for one (chunk, page) position.

    Position-derived (not a running counter) so regenerating a stale/missing
    page always rewrites the same file — a counter would risk colliding with
    an unrelated page's filename when only some pages are redone on resume.
    The ``page_`` prefix and zero-padding keep ``finished_page_files`` in
    correct reading order.
    """
    return pages_dir / f"page_c{chunk_index:04d}_p{page_index:04d}.png"


def known_character_names(state: ProjectState) -> list[str]:
    """Every name a plan may use: the extracted cast plus the bible's aliases."""
    names: list[str] = list(state.characters.keys())
    bible = state.visual_bible
    if bible is None:
        return names
    for canon_name, canon in bible.characters.items():
        names.append(canon_name)
        names.extend(canon.aliases)
    return names


def panel_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a panel must be (re)generated this run."""
    if state_key in state.stale_panels:
        return True
    if state_key in state.panels_done or state_key in state.skipped:
        return False
    return True


def page_needs_generation(state: ProjectState, state_key: str) -> bool:
    """True when a finished page must be (re)generated this run."""
    if state_key in state.stale_pages:
        return True
    if state_key in state.pages_done or state_key in state.skipped_pages:
        return False
    return True


def drop_pages_outside_plan(state: ProjectState, *, chunk_index: int, page_count: int) -> None:
    """Remove obsolete positional page projections from one replanned chunk.

    Only the current chunk's ``cNNNN-pNNNN`` keys past its new page range are
    derived artifacts. Stale markers, tombstones, and other chunks' state stay
    untouched so a shorter plan cannot discard a human-visible decision.
    """
    prefix = f"c{chunk_index:04d}-p"
    known = set(state.generated.pages) | set(state.pages_done)
    for page_key in sorted(known):
        suffix = page_key.removeprefix(prefix)
        if (
            not page_key.startswith(prefix)
            or len(suffix) != 4
            or not suffix.isdecimal()
            or int(suffix) < page_count
        ):
            continue
        drop_page_artifact(state, page_key)


def scrub_chunk_lettering(state: ProjectState, key: str, chunk: str) -> bool:
    """Drop stored lettering that is not a contiguous span of this chunk."""
    changed = False
    pageset = state.page_cache.get(key)
    if pageset is not None:
        for page in pageset.pages:
            for panel in page.panels:
                changed = scrub_lettering_panel(panel, chunk) or changed
    cached = state.chunk_cache.get(key)
    board = cached.storyboard if cached is not None else None
    if board is not None:
        for panel in board.panels:
            changed = scrub_lettering_panel(panel, chunk) or changed
    return changed


def stamp_action_key(state: ProjectState, state_path: Path, target, key: str) -> bool:
    """Stamp a cached plan's missing action key and persist it immediately.

    A resume path changes only this stamp. Waiting for the end-of-run save loses
    it on a budget/continuity pause or a crash, and the next resume re-derives
    it. Persist at the mutation, as storyboard already does.
    """
    if target is None or target.action_key:
        return False
    target.action_key = key
    state.save(state_path)
    return True


def letter_key(
    blank: bytes,
    plan: ComicPagePlan,
    source_text: str,
    *,
    resolved_size: str | None,
    stage_src: str,
) -> tuple[str, str, str]:
    """Return the letter key, its environment digest, and the canonical plan.

    ``resolved_size`` is the size the provider accepted. A square fallback
    therefore misses a key that was stored for the configured size.
    """
    plan_text = plan_json(plan)
    env = h_env(
        text=source_text or plan_text,
        resolved_size=resolved_size or None,
        scope="letter",
    )
    key = letter_action_key(blank=blank, plan_json=plan_text, env=env, stage_src=stage_src)
    return key, env, plan_text


def letter_resume_status(
    blank_path: Path,
    local_path: Path,
    plan: ComicPagePlan,
    source_text: str,
    output_dir: Path,
    *,
    resolved_size: str | None,
    stage_src: str,
) -> str:
    """``current`` when the lettered file is the stored hit for this environment.

    ``tombstone`` is a non-ok hit: do not letter and do not paint. ``miss``
    means the blank can be lettered again, including after a font change.
    """
    key, _env, _plan_text = letter_key(
        blank_path.read_bytes(),
        plan,
        source_text,
        resolved_size=resolved_size,
        stage_src=stage_src,
    )
    recorded = read_manifest_entry(output_dir, key, stage="letter")
    if recorded is not None and recorded.outcome != "ok":
        return "tombstone"
    cached = stored_output(output_dir, key, stage="letter")
    if cached is None or not local_path.is_file() or local_path.read_bytes() != cached:
        return "miss"
    return "current"


def plan_ready_to_letter(state: ProjectState, plan: ComicPagePlan) -> ComicPagePlan:
    """Apply the same plan rewrite the page loop uses before lettering."""
    if state.visual_bible is not None:
        plan = backfill_panel_characters(plan, known_character_names(state))
        plan = resolve_panel_stage_refs(plan, state.visual_bible)
        plan = sanitize_plan_voice(plan, state.visual_bible)
    else:
        plan = sanitize_plan_voice(plan, None)
    return canonicalize_page_plan(
        plan,
        known_names=known_character_names(state),
        visual_bible=state.visual_bible,
    )


def page_chunk_complete(
    state: ProjectState,
    pageset: ComicPagePlanSet,
    output_dir: Path,
    chunk_index: int,
    source_text: str,
    *,
    stage_src: str,
) -> bool:
    """True when every planned page is generated or policy-skipped.

    A finished page whose letter key misses — a font change, for example — is
    not complete, so the blank is lettered again. A rejected letter stays
    complete: it is not retried from this check.
    """
    pages_dir = output_dir / "pages"
    for page_index, plan in enumerate(pageset.pages):
        state_key = page_state_key(chunk_index, page_index)
        if state_key in state.stale_pages:
            return False
        if state_key in state.skipped_pages:
            continue
        rec = state.generated.pages.get(state_key)
        if (
            rec is None
            or state_key not in state.pages_done
            or not is_within(rec.local, output_dir)
            or not Path(rec.local).exists()
        ):
            return False
        if not rec.blank_local or not Path(rec.blank_local).is_file():
            continue
        local = page_asset_path(pages_dir, chunk_index, page_index)
        status = letter_resume_status(
            Path(rec.blank_local),
            local,
            plan_ready_to_letter(state, plan),
            source_text,
            output_dir,
            resolved_size=rec.resolved_size or None,
            stage_src=stage_src,
        )
        if status == "miss":
            return False
    return True
