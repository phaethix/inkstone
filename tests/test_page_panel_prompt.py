"""tests/test_page_panel_prompt.py — per-panel prompt for panel_compose."""

from core.comic.page_prompt import render_page_panel_prompt
from core.schemas import CharacterAsset, ComicPagePlan, PagePanelSpec, Setting


def _plan(action, characters, setting_ref="亭子"):
    panel = PagePanelSpec(
        panel_id="1", action=action, characters=list(characters), setting_ref=setting_ref
    )
    return ComicPagePlan(
        page_id="p1",
        panels=[panel],
        setting_refs=[setting_ref],
        reference_characters=list(characters),
    )


def _assets():
    return {"沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman")}


def _settings():
    return {
        "亭子": Setting(
            name="亭子",
            scene_prompt=(
                "lakeside pavilion at dusk, one young man fishing, "
                "two distant figures on the railing"
            ),
        )
    }


def test_panel_prompt_asserts_cast_and_scrubs_extra_people():
    plan = _plan("沈知意 pours tea; two distant figures on the railing", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name=_settings(),
        style_guide="manhua",
    )
    assert "Exactly 1 character in this panel: 沈知意" in prompt
    assert "two distant figures" not in prompt
    assert "one young man fishing" not in prompt
    assert "lakeside pavilion" in prompt
    assert "pours tea" in prompt
    assert "no speech bubbles" in prompt


def test_panel_prompt_empty_cast_is_scenery_only():
    plan = _plan("the boat drifts", [])
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name={}, settings_by_name={}, style_guide="manhua"
    )
    assert "No people in this panel" in prompt


def test_panel_prompt_multi_cast_counts_in_order():
    assets = _assets()
    assets["福贵"] = CharacterAsset(name="福贵", l1_prompt="an old farmer")
    plan = _plan("沈知意 pours tea for 福贵", ["沈知意", "福贵"])
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name=assets, settings_by_name={}, style_guide=""
    )
    assert "Exactly 2 characters in this panel: 沈知意, 福贵" in prompt


def test_panel_prompt_strict_ban_and_reference_bindings(tmp_path):
    portrait = tmp_path / "shen.png"
    portrait.write_bytes(b"p")
    assets = {
        "沈知意": CharacterAsset(
            name="沈知意", l1_prompt="a shy young woman", portrait_local=str(portrait)
        )
    }
    plan = _plan("沈知意 waits", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
        reference_images=[str(portrait)],
        strict=True,
    )
    assert "absolutely no text" in prompt
    assert "reference image 1 is 沈知意" in prompt
