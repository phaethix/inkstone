"""Regression tests for scheme A: portraits must not bake in props, and a
finished page must not be seeded with duplicate reference people.

Root cause these lock down: the boy's guitar leaked into the reusable identity
portrait (so the finished page showed the WRONG person holding it), and a
single guitar-holding boy portrait was pasted as multiple look-alike figures
across one page because references were assembled without a hard cap/dedup.
"""

from core.pipelines import creative_comic as cc
from core.pipelines.creative_comic import (
    _page_portrait_refs,
    _strip_portrait_props,
)
from core.schemas import CharacterAsset, ComicPagePlan


def _boy_prompt() -> str:
    return (
        "adult man, shy smile, short dark hair, red scarf, green coat, "
        "旧吉他抱在臂弯，朴素便装; source: “抱着一把旧吉他”; “笑得有些腼腆”"
    )


def test_portrait_prompt_drops_the_guitar_prop():
    """The portrait must lock face/hair/outfit only; a held prop belongs to the
    panel action, never to the reusable identity portrait."""
    cleaned = _strip_portrait_props(_boy_prompt()).lower()
    assert "吉他" not in cleaned
    assert "guitar" not in cleaned
    assert "少" in cleaned or "man" in cleaned


def test_portrait_prompt_keeps_face_and_outfit_words():
    """Stripping props must not erase identity cues (face, hair, scarf, coat)."""
    cleaned = _strip_portrait_props(_boy_prompt()).lower()
    for word in ("shy", "hair", "scarf", "coat"):
        assert word in cleaned


def test_strip_is_noop_for_a_clean_portrait():
    """A portrait with no prop is unchanged, so dedup keys stay stable."""
    clean = "young woman, calm face, long dark hair, camel coat, red scarf"
    assert _strip_portrait_props(clean) == clean


def test_portrait_sent_prompt_applies_the_prop_strip():
    """The renderer must SEND the stripped prompt, not just expose a helper."""
    text = open(cc.__file__, encoding="utf-8").read()
    marker = text.index("def _portrait_sent_prompt")
    body = text[marker : text.index("async def _render_portrait", marker)]
    assert "_strip_portrait_props" in body, "portrait renderer still sends the prop"


def test_portrait_key_material_uses_the_stripped_prompt():
    """Changing the portrait prompt must change the action key, otherwise the
    cached (prop-wrong) portrait is reused forever and the fix never renders."""
    a = CharacterAsset(
        name="少年", l1_prompt="boy, holding an old guitar", portrait_prompt=_boy_prompt()
    )
    b = CharacterAsset(name="少年", l1_prompt="boy", portrait_prompt="boy, shy, red scarf")
    assert cc._portrait_key_material("少年", a, "manhua") != cc._portrait_key_material(
        "少年", b, "manhua"
    )


def _plan_with_names(names):
    return ComicPagePlan.model_validate(
        {
            "page_id": "p1",
            "purpose": "the boy plays guitar",
            "layout_intent": "two panels, boy closeup then both by the railing",
            "panels": [
                {"panel_id": "1", "action": "the boy plucks", "characters": list(names)},
                {"panel_id": "2", "action": "both lean on the railing", "characters": list(names)},
            ],
            "reference_characters": list(names),
        }
    )


def _chars():
    chars = {
        "少年": CharacterAsset(name="少年", l1_prompt="boy"),
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="woman"),
    }
    chars["少年"].portrait_local = "assets/boy.png"
    chars["沈知意"].portrait_local = "assets/woman.png"
    return chars


def test_no_bible_branch_dedups_references():
    """A repeated name must not become a second reference: i2i models read a
    repeated portrait as 'draw another copy of this person'."""
    plan = _plan_with_names(["少年", "沈知意", "少年"])
    refs = _page_portrait_refs(plan, _chars())
    assert len(refs) == len(set(refs))
    assert refs == ["assets/boy.png", "assets/woman.png"]


def test_no_bible_branch_caps_references():
    """One page must not be handed a long list of portraits; a small hard cap
    keeps the model from pasting the cast as separate copies."""
    chars = {f"c{i}": CharacterAsset(name=f"c{i}", l1_prompt="p") for i in range(12)}
    for n, a in chars.items():
        a.portrait_local = f"assets/{n}.png"
    plan = _plan_with_names(list(chars))
    refs = _page_portrait_refs(plan, chars)
    assert len(refs) <= 3


def test_single_character_page_uses_exactly_one_reference():
    """A page whose cast is one character gets one portrait, not N copies."""
    chars = {"少年": CharacterAsset(name="少年", l1_prompt="boy")}
    chars["少年"].portrait_local = "assets/boy.png"
    refs = _page_portrait_refs(_plan_with_names(["少年"]), chars)
    assert refs == ["assets/boy.png"]


def test_portrait_style_forbids_held_props():
    """The shared portrait style must say 'empty hands': a held prop in a
    reference portrait is copied into the finished page onto the wrong owner."""
    from core.comic.consistency import DEFAULT_PORTRAIT_STYLE

    low = DEFAULT_PORTRAIT_STYLE.lower()
    assert "empty hands" in low
    assert "upper body" in low
    assert "person alone" in low
