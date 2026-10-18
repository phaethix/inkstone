"""Focused mutations for the finished-page output projection in project state.

``generated.pages`` and ``pages_done`` are derived output projection fields.
Recording an artifact after a successful render clears that page's stale marker;
removing a projection entry does not change stale markers or human decisions.
"""

from __future__ import annotations

from core.schemas import GeneratedPage, ProjectState


def record_page_artifact(
    state: ProjectState, page_key: str, page: GeneratedPage
) -> None:
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
