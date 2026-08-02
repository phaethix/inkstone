# tests/test_visual_bible_schema.py
from core.schemas import ProjectState, VisualBible, VisualBibleReconcileResult


def test_visual_bible_round_trip_on_project_state():
    raw = {
        "project_id": "p1",
        "visual_bible": {
            "version": "bible_v1",
            "style_guide": "manhua, muted European period tones",
            "color": {
                "palette": [
                    {"name": "ink_black", "hex": "#1A1A1A", "usage": "line art"},
                    {"name": "skin_warm", "hex": "#E8C4A8", "usage": "skin"},
                ],
                "lighting": "soft even cel lighting",
                "forbidden": ["neon", "hyper-saturated"],
            },
            "characters": {
                "R": {
                    "canonical_name": "R",
                    "aliases": ["R·", "李先生"],
                    "face_lock": "handsome European man, dark short hair, calm eyes",
                    "palette_notes": "dark suit, white shirt",
                    "role": "writer",
                    "stages": [
                        {
                            "stage": "adult",
                            "appearance": {"hair": "dark short", "outfit_top": "suit"},
                            "outfit_lock": "dark suit jacket, white shirt",
                            "hair_lock": "dark short neat hair",
                            "portrait_key": "R",
                        }
                    ],
                }
            },
            "sheet_ref_local": None,
            "content_hash": "abc",
        },
    }
    state = ProjectState.model_validate(raw)
    assert state.visual_bible is not None
    assert state.visual_bible.characters["R"].aliases == ["R·", "李先生"]
    assert state.visual_bible.color.palette[0].hex == "#1A1A1A"
    dumped = state.model_dump()
    assert dumped["visual_bible"]["version"] == "bible_v1"


def test_project_state_loads_without_visual_bible():
    state = ProjectState.model_validate({"project_id": "old"})
    assert state.visual_bible is None


def test_reconcile_result_schema():
    result = VisualBibleReconcileResult.model_validate(
        {
            "merges": [
                {
                    "alias": "李先生",
                    "canonical": "R",
                    "confidence": "high",
                    "reason": "same man",
                }
            ],
            "stages": [
                {
                    "name": "女孩（叙述者）",
                    "stage": "teen",
                    "of_canonical": "陌生女人",
                    "reason": "younger self",
                }
            ],
            "keeps": [{"name": "老约翰", "reason": "servant"}],
            "color_patches": [],
            "style_guide": "manhua muted tones",
            "color": {
                "palette": [{"name": "ink", "hex": "#111111", "usage": "lines"}],
                "lighting": "soft",
                "forbidden": ["neon"],
            },
            "canons": [],
        }
    )
    assert result.merges[0].confidence == "high"
