"""tests/test_face_lock_age_prefix.py — young stages never inherit adult prefix."""

from core.comic.visual_bible import (
    CharacterCanon,
    CharacterStage,
    ensure_canon_locks,
    l1_from_canon,
    strip_stale_age_prefix,
)


def test_strip_stale_age_prefix_drops_leading_gender_phrase():
    assert strip_stale_age_prefix("adult man, sharp jaw") == "sharp jaw"
    assert strip_stale_age_prefix("adult woman, calm eyes") == "calm eyes"
    assert strip_stale_age_prefix("sharp jaw") == "sharp jaw"
    assert strip_stale_age_prefix("") == ""


def test_young_stage_never_inherits_adult_prefix_from_face_lock():
    canon = CharacterCanon(
        canonical_name="阿远",
        gender="male",
        face_lock="adult man, sharp jaw",
        stages=[
            CharacterStage(
                stage="teen",
                age_look="sixteen-year-old boy",
                outfit_lock="school uniform",
                hair_lock="black mop",
                portrait_key="阿远",
            )
        ],
    )
    fixed = ensure_canon_locks(canon)
    row = next(s for s in fixed.stages if s.stage == "teen")
    assert "adult" not in (row.age_look or "").lower()
    l1 = l1_from_canon(fixed, "teen")
    assert "adult" not in l1.lower()
    assert "sixteen-year-old boy" in l1
