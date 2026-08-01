from core.comic.identity import (
    harden_human_identity_prompt,
    name_suggests_animal_metaphor,
)
from core.schemas import CharacterAsset


def test_name_suggests_animal_metaphor_huniu():
    assert name_suggests_animal_metaphor("虎妞") is True
    assert name_suggests_animal_metaphor("祥子") is False


def test_harden_huniu_prompt_forbids_tiger():
    out = harden_human_identity_prompt("虎妞", "虎妞, sturdy woman in traditional clothes")
    assert out.lower().startswith("human character")
    assert "metaphorical" in out.lower()
    assert "not an animal" in out.lower()
    assert "tiger" in out.lower()
    assert out.index("human character") < out.index("虎妞")


def test_ordinary_name_prompt_unchanged():
    base = "middle-aged farmer in patched jacket"
    assert harden_human_identity_prompt("祥子", base) == base


def test_ensure_character_l1_hardens_metaphor_name():
    from core.comic.identity import ensure_character_l1

    asset = CharacterAsset(name="虎妞", role="factory owner's daughter", l1_prompt="虎妞, sturdy")
    ensure_character_l1(asset)
    assert asset.l1_prompt.lower().startswith("human character")
    assert "not an animal" in asset.l1_prompt.lower()
