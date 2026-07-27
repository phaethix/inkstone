"""tests/test_schemas_lettering.py — caption / dialogue / sfx fields."""

from core.schemas import GeneratedPanel, Panel


def test_panel_accepts_caption_dialogue_sfx():
    panel = Panel(
        panel_id="p1",
        action="looks up",
        caption="次日清晨",
        dialogue="师傅，快走！",
        sfx="轰隆！",
    )
    assert panel.caption == "次日清晨"
    assert panel.dialogue == "师傅，快走！"
    assert panel.sfx == "轰隆！"


def test_panel_blank_lettering_becomes_none():
    panel = Panel(panel_id="p1", action="a", caption="  ", dialogue="", sfx=None)
    assert panel.caption is None
    assert panel.dialogue is None
    assert panel.sfx is None


def test_generated_panel_round_trips_lettering():
    gen = GeneratedPanel(
        local="/tmp/x.png",
        caption="旁白",
        dialogue="对白",
        sfx="砰",
    )
    raw = gen.model_dump()
    again = GeneratedPanel.model_validate(raw)
    assert again.caption == "旁白"
    assert again.dialogue == "对白"
    assert again.sfx == "砰"
