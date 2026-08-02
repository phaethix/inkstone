import asyncio

from core.api import ChatProvider
from core.schemas import CharacterAsset
from core.screenwriter import reconcile_visual_bible


class _FakeChat(ChatProvider):
    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        return {
            "merges": [
                {
                    "alias": "李先生",
                    "canonical": "R",
                    "confidence": "high",
                    "reason": "same protagonist",
                }
            ],
            "stages": [],
            "keeps": [{"name": "老约翰", "reason": "servant"}],
            "color_patches": [],
            "style_guide": "manhua, muted European period",
            "color": {
                "palette": [
                    {"name": "ink", "hex": "#1A1A1A", "usage": "lines"},
                    {"name": "skin", "hex": "#E8C4A8", "usage": "skin"},
                ],
                "lighting": "soft even cel",
                "forbidden": ["neon"],
            },
            "canons": [
                {
                    "canonical_name": "R",
                    "aliases": ["李先生"],
                    "face_lock": "handsome man calm eyes",
                    "palette_notes": "dark suit",
                    "role": "writer",
                    "stages": [
                        {
                            "stage": "adult",
                            "outfit_lock": "dark suit",
                            "hair_lock": "dark short hair",
                            "portrait_key": "R",
                        }
                    ],
                }
            ],
        }


def test_reconcile_visual_bible_parses_tool_payload():
    chars = {
        "R": CharacterAsset(name="R"),
        "李先生": CharacterAsset(name="李先生"),
        "老约翰": CharacterAsset(name="老约翰"),
    }
    result = asyncio.run(
        reconcile_visual_bible(
            "excerpt about R and 李先生",
            chars,
            None,
            alias_hints=[("李先生", "R", "similar")],
            chat=_FakeChat(),
        )
    )
    assert result.merges[0].alias == "李先生"
    assert result.style_guide.startswith("manhua")
    assert result.canons[0].canonical_name == "R"
