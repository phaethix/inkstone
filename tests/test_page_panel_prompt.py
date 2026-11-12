"""tests/test_page_panel_prompt.py — per-panel prompt for panel_compose."""

from pathlib import Path

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


def test_page_panel_refs_trim_white_border(tmp_path):
    """A white-background portrait sent as i2i reference bleeds its border into
    the panel (3/3 bordered panels were the white-canvas portrait cast). The
    sent reference must be a border-trimmed copy, and the original asset file
    must survive untouched."""
    from PIL import Image

    from core.pipelines.creative_comic import _page_panel_reference_paths

    portrait = tmp_path / "shen.png"
    img = Image.new("RGB", (64, 64), (255, 255, 255))
    for x in range(20, 44):
        for y in range(20, 44):
            img.putpixel((x, y), (90, 110, 130))
    img.save(portrait)
    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local=str(portrait))}
    panel = PagePanelSpec(panel_id="1", characters=["沈知意"])

    refs = _page_panel_reference_paths(panel, assets, None, {}, tmp_path)

    assert len(refs) == 1
    sent = Path(refs[0])
    assert sent != portrait, "must not send the raw white-canvas portrait"
    assert sent.is_file(), "trimmed reference must exist on disk"
    assert portrait.read_bytes() != sent.read_bytes() or sent != portrait
    sent_img = Image.open(sent).convert("RGB")
    w, h = sent_img.size
    assert w < 64 and h < 64, f"border must be trimmed away, got {w}x{h}"
    corners = [sent_img.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    assert all(sum(c) / 3 < 245 for c in corners), (
        f"trimmed image corners must not be white, got {corners}"
    )
    assert Image.open(portrait).size == (64, 64), "original asset must be untouched"


def test_reference_bindings_match_trimmed_sidecar(tmp_path):
    """The sent reference may be a border-trimmed sidecar of the portrait; the
    owner label must still resolve or the face tag silently disappears from
    the prompt and the model reassigns faces."""
    from PIL import Image

    from core.comic.page_prompt import _reference_bindings

    portrait = tmp_path / "shen.png"
    Image.new("RGB", (32, 32), (255, 255, 255)).save(portrait)
    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="x", portrait_local=str(portrait))}
    sidecar = str(tmp_path / "shen_trimmed.png")
    assert _reference_bindings(["沈知意"], assets, [sidecar], None) == [(1, "沈知意")]


def test_page_panel_refs_keep_clean_reference_as_is(tmp_path):
    """A studio-neutral reference is sent unmodified — rewriting must not
    touch files needlessly. A dark flat backdrop counts as a scene (chapter-2
    inn panels inherited a river from scene-colored portrait backdrops), so
    only a light studio tone passes through."""
    from PIL import Image

    from core.pipelines.creative_comic import _page_panel_reference_paths

    portrait = tmp_path / "fu.png"
    Image.new("RGB", (64, 64), (238, 238, 238)).save(portrait)
    assets = {"福贵": CharacterAsset(name="福贵", l1_prompt="x", portrait_local=str(portrait))}
    panel = PagePanelSpec(panel_id="1", characters=["福贵"])

    refs = _page_panel_reference_paths(panel, assets, None, {}, tmp_path)

    assert refs == [str(portrait)]

    scene = tmp_path / "scene.png"
    Image.new("RGB", (64, 64), (90, 110, 130)).save(scene)
    assets2 = {"福贵": CharacterAsset(name="福贵", l1_prompt="x", portrait_local=str(scene))}
    refs2 = _page_panel_reference_paths(panel, assets2, None, {}, tmp_path)
    assert len(refs2) == 1 and refs2[0] != str(scene), (
        "a scene-colored backdrop must be neutralized before i2i"
    )


def test_panel_prompt_falls_back_to_stage_variant_identity():
    """A bare-name asset whose L1 is a Chinese-only placeholder gives the image
    model no identity: the same 少年 rendered as a small child in one panel and
    a teenager in another (page_c0000 p2a vs page_c0001 p02). When a stage
    variant of the same character carries a real English identity line, the
    panel prompt must adopt it so every panel draws the same person."""
    assets = {
        "少年": CharacterAsset(
            name="少年",
            l1_prompt=(
                "少年, 次要角色，抱旧吉他的腼腆少年, 旧吉他; "
                "⚠ unverified evidence for: distinguishing"
            ),
        ),
        "少年@teen": CharacterAsset(
            name="少年@teen",
            l1_prompt=(
                "young boy, tender East-Asian teenage boy face, shy, "
                "early-to-mid teens, plain muted tunic"
            ),
        ),
    }
    plan = _plan("少年 tunes the old guitar", ["少年"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "teenage boy face" in prompt, "stage-variant identity must reach the prompt"
    assert "抱旧吉他的腼腆少年" not in prompt, "Chinese placeholder must not be the identity"


def test_panel_prompt_prefers_clean_variant_over_flagged_default():
    """Among stage variants, an unflagged English identity wins over a
    placeholder-flagged @default one."""
    assets = {
        "沈知意": CharacterAsset(
            name="沈知意", l1_prompt="沈知意, 女主角，乘船去上游的旅人; ⚠ unverified evidence"
        ),
        "沈知意@default": CharacterAsset(
            name="沈知意@default",
            l1_prompt="adult woman, soft face; ⚠ no source evidence; using generic placeholder",
        ),
        "沈知意@adult": CharacterAsset(
            name="沈知意@adult",
            l1_prompt="adult woman, soft delicate East-Asian female face, early-20s, warm scarf",
        ),
    }
    plan = _plan("沈知意 waits at the rail", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "early-20s" in prompt
    assert "using generic placeholder" not in prompt


def test_panel_prompt_locks_one_age_per_character():
    """The model aged the same boy differently per panel; the prompt must pin
    one fixed age per character."""
    assets = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman in her early twenties")
    }
    plan = _plan("沈知意 waits at the rail", ["沈知意"])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "AGE LOCK" in prompt
    assert "never a younger" in prompt


def test_panel_prompt_omits_age_lock_for_scenery_only():
    plan = _plan("empty deck in the fog", [])
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name={},
        settings_by_name=_settings(),
        style_guide="manhua",
    )
    assert "AGE LOCK" not in prompt


def test_panel_prompt_rewrites_foreground_occlusion_staging():
    """A shoulder/back/silhouette staging line made the model paste the
    full-body reference along the panel edge (a sliced collage seam:
    page_c0000_p0000-g0002 left edge, page_c0001_p0001-g0000 right edge).
    The prompt must stage both figures fully visible instead."""
    assets = {
        "沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman"),
        "少年": CharacterAsset(name="少年", l1_prompt="a shy boy with a guitar"),
    }
    plan = _plan(
        "Over the youth's shoulder: 沈知意 across the table rests her chin on "
        "her hand, watching him; the near figure is a dark silhouette, her face "
        "lit by the lantern",
        ["少年", "沈知意"],
    )
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "dark silhouette" not in prompt
    assert "over the youth's shoulder" not in prompt.lower()
    assert "rests her chin on her hand" in prompt


def test_panel_prompt_rewrites_back_to_camera_staging():
    """'backs to us / back to the camera' stages a figure whose face is
    hidden — the model then copies the reference as an edge strip."""
    assets = {"沈知意": CharacterAsset(name="沈知意", l1_prompt="a shy young woman")}
    plan = _plan(
        "Low angle behind the wooden railing: two small silhouettes "
        "(沈知意 in her scarf, the boy with the guitar) lean on the rail "
        "side by side, backs to us",
        ["沈知意"],
    )
    prompt = render_page_panel_prompt(
        plan,
        plan.panels[0],
        characters_by_name=assets,
        settings_by_name={},
        style_guide="manhua",
    )
    assert "small silhouettes" not in prompt.lower()
    assert "backs to us" not in prompt.lower()
    assert "lean on the rail" in prompt


def test_setting_strip_removes_passenger_crowds():
    """'passengers leaning on it' drew a sliced ferry-cabin strip along the
    panel edge (page_c0000_p0000-g0001/g0002). Plural-crowd nouns in setting
    prose must be stripped like named-person clauses."""
    from core.comic.page_prompt import _strip_setting_people

    out = _strip_setting_people("wooden railing, passengers leaning on it", [])
    assert "passengers" not in out
    assert "wooden railing" in out
