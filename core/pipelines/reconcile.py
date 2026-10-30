"""core.pipelines.reconcile — make a resumed project agree with what is on disk.

A ``state.json`` records that a panel or a page exists. Between two runs the
files under the project directory can disappear: a manual delete, a partial copy,
a pruned cache, or a path from a different machine. Before a resume trusts the
checkpoint, every recorded asset is checked for existence and containment.

None of this reads a provider, so it can run before the first billable call and
it costs nothing to run on every start. It was inline in the pipeline, where it
was the single largest block that never issued a call and never touched a
fingerprint.

``soft_invalidate_render`` is the deliberate opposite: drop the render-owned
assets but keep the chat-derived plans, because re-planning costs a call while
re-drawing is the thing the source change actually requires.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.schemas import ProjectState
from core.screenwriter import is_content_policy_rejection

logger = logging.getLogger(__name__)


def is_within(path: str | Path, root: Path) -> bool:
    """Whether ``path`` resolves inside ``root``.

    ``resolve()`` on both sides, so a symlink or a ``..`` segment cannot be used
    to point a recorded asset outside the project directory.
    """
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def soft_invalidate_render(state: ProjectState) -> None:
    """Drop render-owned assets while keeping structural chat caches.

    Preserves ``chunk_cache`` (extract/storyboard) and ``page_cache`` (finished
    page plans). Content-policy ``skipped`` / ``skipped_pages`` are kept: the
    source text did not change, so re-attempting those assets only burns quota.
    """
    state.panels_done = []
    state.stale_panels = []
    state.generated.panels = {}
    state.generated.portraits = {}
    state.pages_done = []
    state.stale_pages = []
    state.generated.pages = {}
    for asset in state.characters.values():
        asset.portrait_local = None


def is_unsupported_image_size_error(exc: Exception) -> bool:
    """Return True when a provider likely rejected the requested output size.

    Used to fall back from portrait sizes (e.g. ``1024x1536``) to ``1024x1024``
    once. Content-policy rejects are excluded so they stay on the skip path.
    """
    if is_content_policy_rejection(exc):
        return False
    text = _provider_error_text(exc)
    size_tokens = ("size", "resolution", "dimension", "1024x1536", "aspect")
    reject_tokens = (
        "invalid",
        "unsupported",
        "not supported",
        "not allow",
        "not allowed",
        "unknown",
        "bad request",
    )
    return any(t in text for t in size_tokens) and any(t in text for t in reject_tokens)


def _provider_error_text(exc: Exception) -> str:
    """Status line plus the HTTP body.

    ``raise_for_status`` puts only ``400 Client Error: Bad Request for url: …``
    on the exception. A size rejection names ``1024x1536`` in the body.
    """
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.text
        except Exception:  # noqa: BLE001 — a body we cannot read is not evidence
            body = ""
        if isinstance(body, str) and body:
            parts.append(body)
        reason = getattr(response, "reason", None)
        if isinstance(reason, str) and reason:
            parts.append(reason)
    return "\n".join(parts).lower()


def reconcile_state(state: ProjectState, state_path: Path, output_dir: Path) -> None:
    """Remove every missing or escaped persisted asset before a resume trusts it.

    A page whose lettered file is gone but whose blank art survives keeps its
    projection entry and loses its ``pages_done`` record. That is deliberate: the
    page is not finished, so the run must visit it again, but the stored blank
    means visiting it costs a local re-letter instead of an image call. Keeping
    it in ``pages_done`` would mark a page done with no readable file. This is
    why ``pages_missing_lettered`` is collected separately from ``invalid_pages``.
    """
    changed = False
    invalid_panels = [
        panel_id
        for panel_id, generated in state.generated.panels.items()
        if not is_within(generated.local, output_dir) or not Path(generated.local).is_file()
    ]
    for panel_id in invalid_panels:
        state.generated.panels.pop(panel_id, None)
        changed = True
    stale_done = [
        panel_id for panel_id in state.panels_done if panel_id not in state.generated.panels
    ]
    for panel_id in stale_done:
        state.panels_done.remove(panel_id)
        changed = True

    invalid_pages = []
    pages_missing_lettered = []
    for page_id, generated in state.generated.pages.items():
        local_valid = is_within(generated.local, output_dir) and Path(generated.local).is_file()
        blank_valid = bool(
            generated.blank_local
            and is_within(generated.blank_local, output_dir)
            and Path(generated.blank_local).is_file()
        )
        if not local_valid:
            pages_missing_lettered.append(page_id)
        if not local_valid and not blank_valid:
            invalid_pages.append(page_id)
    for page_id in invalid_pages:
        state.generated.pages.pop(page_id, None)
        changed = True
    stale_pages_done = [
        page_id
        for page_id in state.pages_done
        if page_id not in state.generated.pages or page_id in pages_missing_lettered
    ]
    for page_id in stale_pages_done:
        state.pages_done.remove(page_id)
        changed = True

    orphan_portraits = set(state.generated.portraits) - set(state.characters)
    for name in orphan_portraits:
        state.generated.portraits.pop(name, None)
        changed = True
    for name, asset in state.characters.items():
        portrait = asset.portrait_local
        if portrait and is_within(portrait, output_dir) and Path(portrait).is_file():
            continue
        if portrait:
            logger.warning("reconcile: discarding invalid portrait for %s", name)
        asset.portrait_local = None
        state.generated.portraits.pop(name, None)
        changed = True

    if not changed:
        return
    if invalid_panels or stale_done:
        logger.warning(
            "reconcile: removed %d invalid panel record(s)", len(invalid_panels) + len(stale_done)
        )
    if invalid_pages or stale_pages_done:
        logger.warning(
            "reconcile: removed %d invalid page record(s)",
            len(invalid_pages) + len(stale_pages_done),
        )
    state.save(state_path)
