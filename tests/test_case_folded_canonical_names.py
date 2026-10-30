"""tests/test_case_folded_canonical_names.py — one person, one canon, one portrait.

The real failure, measured on a finished ``cbz`` run: extraction named the
protagonist ``著名小说家r`` (the excerpt writes ``r·``, lowercase), while the
visual bible reconciled the same person as ``著名小说家R``. Both spellings then
lived in ``state.characters``, each with its own portrait, and the page plan kept
referring to the lowercase one — which resolved to no canon at all:

    names referenced by panels: ['一位先生', '一位女士', '仆人', '著名小说家r']
    bible canon keys:          ['一位先生', '一位女士', '仆人', '著名小说家R']
    referenced but NOT a canon: ['著名小说家r']

``sync_characters_from_bible`` looks the canon up by exact key, so the canon's L1
identity was never copied onto the character the pages actually named;
``ensure_stage_portrait_assets`` then minted ``著名小说家R@adult`` beside the
``著名小说家r`` the plan uses and paid for a second portrait of the same face. A
case difference is not a different person: names differing only in case must
fold onto one row before portraits are requested.

The row already on disk always wins. It owns the portrait and the panel
references, so folding onto the canonical spelling instead would strand both.
"""

from core.comic.visual_bible import (
    apply_reconcile,
    collapse_duplicate_identities,
    ensure_stage_portrait_assets,
    sync_characters_from_bible,
)
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ProjectState,
    VisualBible,
    VisualBibleReconcileResult,
)

EXTRACTED = "著名小说家r"
RECONCILED = "著名小说家R"
FACE_LOCK = "thin angular Viennese scholar's face, sharp nose"


def _canon(name: str, *, with_stage: bool = False) -> CharacterCanon:
    return CharacterCanon(
        canonical_name=name,
        face_lock=FACE_LOCK,
        role="protagonist",
        gender="male",
        stages=[CharacterStage(stage="adult", portrait_key=f"{name}@adult")] if with_stage else [],
    )


def _bible(*names: str) -> VisualBible:
    return VisualBible(
        version="bible_v1",
        style_guide="manhua",
        color=ColorBible(palette=[], lighting="", forbidden=[]),
        characters={name: _canon(name) for name in names},
    )


def _state(**characters: CharacterAsset) -> ProjectState:
    return ProjectState(project_id="p", characters=dict(characters))


def _protagonist_rows(state: ProjectState) -> list[str]:
    """Every character row that describes the novelist, however it is spelled."""
    stem = EXTRACTED.casefold()
    return [name for name in state.characters if name.casefold().startswith(stem)]


def test_a_canon_whose_case_differs_updates_the_extracted_row():
    """The extracted spelling is the row the pages name, so it must get the L1."""
    state = _state(**{EXTRACTED: CharacterAsset(name=EXTRACTED, role="protagonist")})
    state.visual_bible = _bible(RECONCILED)

    sync_characters_from_bible(state)

    assert FACE_LOCK in state.characters[EXTRACTED].l1_prompt
    assert FACE_LOCK in state.characters[EXTRACTED].portrait_prompt


def test_a_case_variant_does_not_mint_a_second_portrait_row():
    """This is the measured bug: two base rows and an extra stage row for one face."""
    state = _state(**{EXTRACTED: CharacterAsset(name=EXTRACTED, role="protagonist")})
    state.visual_bible = _bible(RECONCILED)
    state.visual_bible.characters[RECONCILED].stages = [
        CharacterStage(stage="adult", portrait_key=f"{RECONCILED}@adult")
    ]

    sync_characters_from_bible(state)
    ensure_stage_portrait_assets(state)

    rows = _protagonist_rows(state)
    assert rows == [EXTRACTED, f"{EXTRACTED}@adult"], (
        f"one face was split across {rows}; the rows the plan names must be the "
        f"only rows, and the stage row must hang off them"
    )


def test_a_case_variant_keeps_the_base_row_portrait():
    """Folding must not orphan the portrait already drawn for the extracted row."""
    portrait = "/tmp/portraits/novelist.png"
    state = _state(
        **{EXTRACTED: CharacterAsset(name=EXTRACTED, role="protagonist", portrait_local=portrait)}
    )
    state.visual_bible = _bible(RECONCILED)

    sync_characters_from_bible(state)
    ensure_stage_portrait_assets(state)

    assert state.characters[EXTRACTED].portrait_local == portrait


def test_two_canons_differing_only_in_case_collapse():
    """A canon forked by case must fold, or the bible keeps charging twice."""
    state = _state(
        **{
            EXTRACTED: CharacterAsset(name=EXTRACTED, role="protagonist"),
            RECONCILED: CharacterAsset(name=RECONCILED, role="protagonist"),
        }
    )
    state.visual_bible = _bible(RECONCILED)
    state.visual_bible.characters[EXTRACTED] = _canon(EXTRACTED)

    mutated = collapse_duplicate_identities(state)

    assert mutated, "two canons of one person must be recognised as duplicates"
    assert state.visual_bible is not None
    remaining = [
        name for name in state.visual_bible.characters if name.casefold() == RECONCILED.casefold()
    ]
    assert len(remaining) == 1, f"the bible kept both spellings: {remaining}"
    assert _protagonist_rows(state) == [EXTRACTED], (
        "the two character rows must fold into the one the plan references"
    )


def test_reconcile_adopts_the_spelling_the_extraction_used():
    """Reconcile must not answer with a spelling the existing rows do not carry."""
    state = _state(**{EXTRACTED: CharacterAsset(name=EXTRACTED, role="protagonist")})
    result = VisualBibleReconcileResult(style_guide="manhua", canons=[_canon(RECONCILED)])

    out = apply_reconcile(state, result)

    assert out.visual_bible is not None
    canon_names = [
        name for name in out.visual_bible.characters if name.casefold() == RECONCILED.casefold()
    ]
    assert len(canon_names) == 1, f"the bible kept both spellings: {canon_names}"
    assert canon_names[0] == EXTRACTED, (
        "reconcile must resolve to the spelling the extraction already used, or "
        "the page plan keeps naming a character no canon describes"
    )
