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
    assert "clean artwork with no overlays" in prompt


def test_panel_prompt_drops_source_quotes():
    """The evidence quotes in an L1 string must not reach an image prompt.

    Qwen's text encoder reads Chinese: ``source: “倚着栏杆”`` taught the model
    to letter those exact glyphs onto the art (page_c0000_p0002 drew a caption
    bar reading 倚着栏杆 verbatim).
    """
    assets = {
        "沈知意": CharacterAsset(
            name="沈知意",
            l1_prompt=(
                "young Chinese woman, thin knit scarf; source: “沈知意把围巾拢了拢”; “倚着栏杆”"
            ),
        )
    }
    plan = _plan("沈知意 waits at the rail", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "source:" not in prompt
    assert "沈知意把围巾拢了拢" not in prompt
    assert "倚着栏杆" not in prompt
    assert "thin knit scarf" in prompt


def test_panel_prompt_scrubs_chrome_clauses_from_action():
    """An action that names dialogue/bubble chrome must not carry it into a
    per-panel prompt: ``the line of dialogue hangs in the air`` drew a speech
    bubble full of pseudo-script (page_c0001_p0001)."""
    plan = _plan(
        "沈知意 listens; the line of dialogue hangs in the air between them",
        ["沈知意"],
    )
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name={},
        style_guide="manhua",
    )
    assert "dialogue" not in prompt
    assert "hangs in the air" not in prompt
    assert "listens" in prompt


def test_panel_prompt_count_line_claims_the_whole_frame():
    """The cast line must claim the entire image, not just the panel: wide
    establishing shots sprouted an extra bystander (page_c0000_p0000 drew a
    third man in green although the plan named one character)."""
    plan = _plan("沈知意 waits at the rail", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name={},
        style_guide="manhua",
    )
    assert "the ONLY people in the image" in prompt


def test_panel_prompt_count_line_bans_duplicate_copies():
    """A wide shot drew the same young man twice (two boys in green on
    page_c0000_p0002): the cast line must forbid rendering one character
    more than once, or the model splits a named character into copies."""
    plan = _plan("沈知意 waits at the rail", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name={},
        style_guide="manhua",
    )
    assert "only once" in prompt


def test_panel_prompt_scrubs_line_metaphors_from_action():
    """``the line lands`` / ``she delivers the line`` use *line* for dialogue:
    page_c0001_p0001 p2-s4/p2-s5 drew a speech bubble of pseudo-script from
    those phrasings, so every dialogue-metaphor clause must be scrubbed."""
    plan = _plan("沈知意 looks up; she delivers the line as a quiet observation", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=_assets(),
        settings_by_name={},
        style_guide="manhua",
    )
    assert "delivers the line" not in prompt
    assert "the line lands" not in prompt or "delivers" not in prompt
    assert "looks up" in prompt


def test_panel_prompt_scrubs_the_line_lands_clause():
    plan = _plan("The boy's hand stops on the strings; a beat of stillness as the line lands", [])
    prompt = render_page_panel_prompt(
        plan, plan.panels[0], characters_by_name={}, settings_by_name={}, style_guide="manhua"
    )
    assert "the line lands" not in prompt
    assert "strings" in prompt


def test_panel_prompt_style_has_no_white_frame_words():
    """Panel style must not say line art / design sheet: those words drew
    white bordered frames around the art (page_c0001_p0000-g0002) because the
    finished page ban list names them and the workflow has no negative
    channel."""
    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman")}
    plan = _plan("沈知意 waits", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="",
    )
    lowered = prompt.lower()
    assert "line art" not in lowered
    assert "design sheet" not in lowered


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
    assert "absolutely clean artwork" in prompt
    assert "reference image 1 is 沈知意" in prompt


def test_page_panel_refs_only_panel_cast(tmp_path):
    from core.pipelines.creative_comic import _page_panel_reference_paths

    shen = tmp_path / "shen.png"
    fu = tmp_path / "fu.png"
    shen.write_bytes(b"p")
    fu.write_bytes(b"p")
    assets = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local=str(shen)),
        "福贵": CharacterAsset(name="福贵", l1_prompt="x", portrait_local=str(fu)),
    }
    panel_a = PagePanelSpec(panel_id="1", characters=["沈知意"])
    panel_b = PagePanelSpec(panel_id="2", characters=["福贵", "沈知意"])
    assert _page_panel_reference_paths(panel_a, assets, None, {}, tmp_path) == [str(shen)]
    assert _page_panel_reference_paths(panel_b, assets, None, {}, tmp_path) == [str(fu), str(shen)]


def test_page_panel_refs_drop_missing_files(tmp_path):
    from core.pipelines.creative_comic import _page_panel_reference_paths

    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local="/nope.png")}
    panel = PagePanelSpec(panel_id="1", characters=["沈知意"])
    assert _page_panel_reference_paths(panel, assets, None, {}, tmp_path) == []
