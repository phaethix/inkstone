"""tests/test_progress_portrait_edge.py — a drawn portrait is progress even alone.

Review finding: ``_planning_fraction`` computed its portrait denominator from
``visual_bible.characters`` or ``state.characters``. When a checkpoint records
a drawn portrait but neither name map survived (a legacy or partially written
state), the denominator was 0 and the portrait earned nothing, so a project with
real artwork still reported 0.0 — the original bug in miniature.
"""

from core.pipelines.progress import estimate_progress
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    GeneratedAssets,
    ProjectState,
    VisualBible,
)


def test_portrait_without_character_registry_still_counts():
    """Artwork on disk is progress, even when the name map is missing."""
    state = ProjectState(
        project_id="p",
        generated=GeneratedAssets(portraits={"A": "/tmp/a.png"}),
    )
    assert estimate_progress(state, total_chunks=1) > 0.0


def test_portraits_are_partial_until_every_canonical_character_is_drawn():
    """One portrait of two canonical characters must not finish the stage."""
    state = ProjectState(
        project_id="p",
        visual_bible=VisualBible(characters={"A": _canon("A"), "B": _canon("B")}),
        generated=GeneratedAssets(portraits={"A": "/tmp/a.png"}),
    )
    one_of_two = estimate_progress(state, total_chunks=1)
    state.generated.portraits["B"] = "/tmp/b.png"
    both = estimate_progress(state, total_chunks=1)
    assert one_of_two < both


def test_unknown_portrait_keys_do_not_inflate_the_stage():
    """A portrait keyed to no canonical name must not count as a finished canon."""
    state = ProjectState(
        project_id="p",
        visual_bible=VisualBible(characters={"A": _canon("A"), "B": _canon("B")}),
        generated=GeneratedAssets(portraits={"A": "/tmp/a.png", "ghost": "/tmp/ghost.png"}),
    )
    pct = estimate_progress(state, total_chunks=1)
    state.generated.portraits = {"A": "/tmp/a.png", "B": "/tmp/b.png"}
    complete = estimate_progress(state, total_chunks=1)
    assert pct < complete


def test_character_assets_are_accepted_as_the_denominator():
    """Without a bible, the extracted character map is the canon list."""
    state = ProjectState(
        project_id="p",
        characters={"A": CharacterAsset(name="A"), "B": CharacterAsset(name="B")},
        generated=GeneratedAssets(portraits={"A": "/tmp/a.png"}),
    )
    one_of_two = estimate_progress(state, total_chunks=1)
    state.generated.portraits["B"] = "/tmp/b.png"
    both = estimate_progress(state, total_chunks=1)
    assert one_of_two < both


def _canon(name: str) -> CharacterCanon:
    return CharacterCanon(canonical_name=name)
