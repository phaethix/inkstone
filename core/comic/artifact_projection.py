"""Focused mutations for the finished-page output projection in project state.

``generated.pages`` and ``pages_done`` are derived output projection fields.
Recording an artifact after a successful render clears that page's stale marker;
removing a projection entry does not change stale markers or human decisions.

``check_page_projection`` is the read-only counterpart: it reports where the
projection and the checkpoint disagree. Nothing calls it on the write path, and
it changes no state.

Why this exists. "This page is finished" is stored three times: ``pages_done``,
``generated.pages``, and a ``render.page`` manifest under ``index/``. Resume
reads all three and each has a different failure mode — a page recorded in one
but not the others is either repainted for pay or skipped while missing. The
first two live in the same document and are written together, so a disagreement
between them is provable without touching the store. The manifest cannot be
cross-checked this way yet: it records ``inputs`` and ``outputs`` but not the
page key, and re-deriving the key would be the second derivation path the
content-addressed design forbids.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from core.schemas import GeneratedPage, ProjectState

PASS = "pass"
#: Nothing to compare. Never a pass: a check with no records proves nothing.
ABSENT = "absent"
#: The records exist and contradict each other.
MISMATCH = "mismatch"


@dataclass(frozen=True)
class ProjectionCheck:
    name: str
    status: str
    detail: str = ""


def _done_vs_projection(state: ProjectState) -> ProjectionCheck:
    done = state.pages_done
    recorded = state.generated.pages
    if not done and not recorded:
        return ProjectionCheck("page projection", ABSENT, "no finished pages recorded")
    if done and not recorded:
        # Both fields are set on one ``state`` object and saved as one document,
        # and the artifact is written before the done entry. "Every done page has
        # no artifact" therefore cannot come from a crash between two writes: it
        # means the project predates the projection. Reporting it as a mismatch
        # would make ``verify`` fail on a healthy legacy checkout, which is worse
        # than saying plainly that there is nothing to compare.
        return ProjectionCheck(
            "page projection",
            ABSENT,
            f"{len(done)} page(s) marked done but no artifact records; "
            "the project predates the output projection",
        )
    only_done = sorted(set(done) - set(recorded))
    only_recorded = sorted(set(recorded) - set(done))
    if only_done or only_recorded:
        parts = []
        if only_done:
            parts.append(f"done without an artifact: {', '.join(only_done[:5])}")
        if only_recorded:
            parts.append(f"artifact without a done record: {', '.join(only_recorded[:5])}")
        return ProjectionCheck("page projection", MISMATCH, "; ".join(parts))
    return ProjectionCheck(
        "page projection", PASS, f"{len(done)} page(s) agree with the artifact records"
    )


def _disjoint(state: ProjectState, name: str, other: set[str], what: str) -> ProjectionCheck:
    """A page cannot be both finished and awaiting a decision."""
    if not state.pages_done:
        return ProjectionCheck(name, ABSENT, "no finished pages recorded")
    overlap = sorted(set(state.pages_done) & other)
    if overlap:
        return ProjectionCheck(name, MISMATCH, f"also {what}: {', '.join(overlap[:5])}")
    return ProjectionCheck(name, PASS, f"no finished page is {what}")


def check_page_projection(state: ProjectState) -> list[ProjectionCheck]:
    """Report where the finished-page projection contradicts the checkpoint.

    Read-only and dependency-free: it reads fields that live in ``state.json``,
    so a corrupt ``cache.json`` cannot block it (load with ``caches=False``).

    The checks are about mutual exclusion, not about art. A page that is done and
    also tombstoned, or done and also queued for redraw, has no defined meaning on
    resume: the branches disagree and whichever runs first wins.
    """
    checks = [_done_vs_projection(state)]

    counts = Counter(state.pages_done)
    duplicates = sorted(key for key, times in counts.items() if times > 1)
    if not state.pages_done:
        checks.append(ProjectionCheck("done records", ABSENT, "no finished pages recorded"))
    elif duplicates:
        checks.append(
            ProjectionCheck(
                "done records",
                MISMATCH,
                f"duplicate done entries: {', '.join(duplicates[:5])}",
            )
        )
    else:
        checks.append(ProjectionCheck("done records", PASS, "done entries are unique"))

    checks.append(_disjoint(state, "done vs tombstones", set(state.tombstones), "tombstoned"))
    checks.append(_disjoint(state, "done vs stale", set(state.stale_pages), "queued for redraw"))
    return checks


def projection_problems(state: ProjectState) -> list[str]:
    """One line per mismatch, in the shape ``verify`` reports CAS problems."""
    return [
        f"{check.name}: {check.detail}"
        for check in check_page_projection(state)
        if check.status == MISMATCH
    ]


def record_page_artifact(state: ProjectState, page_key: str, page: GeneratedPage) -> None:
    """Record a successfully completed page in the derived output projection."""
    # Dict replacement preserves the key's insertion position when replacing an
    # existing record, while a newly completed page is appended deterministically.
    state.generated.pages[page_key] = page

    # Preserve the first-seen order and remove any historical duplicate keys.
    seen: set[str] = set()
    pages_done: list[str] = []
    for key in state.pages_done:
        if key not in seen:
            pages_done.append(key)
            seen.add(key)
    if page_key not in seen:
        pages_done.append(page_key)
    state.pages_done = pages_done

    # A stale marker is cleared only after successful artifact recording.
    state.stale_pages = [key for key in state.stale_pages if key != page_key]


def drop_page_artifact(state: ProjectState, page_key: str) -> None:
    """Drop one page's output projection without changing decisions or tombstones."""
    state.generated.pages.pop(page_key, None)
    state.pages_done = [key for key in state.pages_done if key != page_key]
