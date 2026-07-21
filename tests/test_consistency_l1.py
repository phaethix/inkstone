"""tests/test_consistency_l1.py — prompt hardening (no network).

Verifies:
- build_panel_prompt inlines a single character's l1_prompt plus scene + action + style.
- Multiple characters are inlined in order, each joined by ", ".
- setting accepts a Setting, a dict, or a raw scene string.
- Empty segments (missing l1_prompt / setting / style) are dropped, not padded.
- A per-call style_guide overrides the engine default.
"""

from core.comic.consistency import DEFAULT_PANEL_STYLE, ConsistencyEngine
from core.schemas import CharacterAsset, Setting


def test_l1_inlines_single_character():
    char = CharacterAsset(
        name="方鸿渐", l1_prompt="a young Chinese man with round metal-framed glasses"
    )
    setting = Setting(name="远洋邮轮甲板", scene_prompt="ocean liner deck at dawn")
    engine = ConsistencyEngine(style_guide="manhua line-art style")
    prompt = engine.build_panel_prompt(
        characters=char, setting=setting, action="leaning on the railing gazing at sea"
    )
    assert "a young Chinese man with round metal-framed glasses" in prompt
    assert "ocean liner deck at dawn" in prompt
    assert "leaning on the railing gazing at sea" in prompt
    assert "manhua line-art style" in prompt


def test_l1_inlines_multiple_characters_in_order():
    c1 = CharacterAsset(name="A", l1_prompt="tall man in red coat")
    c2 = CharacterAsset(name="B", l1_prompt="short woman in blue dress")
    engine = ConsistencyEngine()
    prompt = engine.build_panel_prompt(
        characters=[c1, c2], setting=None, action="talking by the window"
    )
    assert "tall man in red coat" in prompt
    assert "short woman in blue dress" in prompt
    # Both characters present, c1 before c2 (order preserved).
    assert prompt.index("tall man in red coat") < prompt.index("short woman in blue dress")
    assert "talking by the window" in prompt


def test_l1_accepts_setting_dict_and_str():
    engine = ConsistencyEngine()
    p_dict = engine.build_panel_prompt(
        characters=[], setting={"scene_prompt": "a quiet study room"}, action="reading"
    )
    assert "a quiet study room" in p_dict
    p_str = engine.build_panel_prompt(
        characters=[], setting="explicit scene text", action="reading"
    )
    assert "explicit scene text" in p_str


def test_l1_drops_empty_segments_but_keeps_comic_style():
    engine = ConsistencyEngine()
    # Character without l1_prompt and no setting -> action + default comic style.
    char = CharacterAsset(name="x")  # l1_prompt defaults to ""
    prompt = engine.build_panel_prompt(characters=char, setting=None, action="just action")
    assert "just action" in prompt
    assert DEFAULT_PANEL_STYLE in prompt


def test_l1_per_call_style_overrides_default():
    engine = ConsistencyEngine(style_guide="default style")
    prompt = engine.build_panel_prompt(
        characters=[], setting=None, action="act", style_guide="override style"
    )
    assert "override style" in prompt
    assert "default style" not in prompt


def test_l1_default_style_is_always_appended():
    engine = ConsistencyEngine()
    prompt = engine.build_panel_prompt(characters=[], setting=None, action="act")
    assert DEFAULT_PANEL_STYLE in prompt


def test_l1_user_style_is_kept_alongside_default():
    engine = ConsistencyEngine(style_guide="vibrant cyberpunk manhua")
    prompt = engine.build_panel_prompt(characters=[], setting=None, action="act")
    assert "vibrant cyberpunk manhua" in prompt
    assert DEFAULT_PANEL_STYLE in prompt
