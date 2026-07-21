"""tests/test_segmentation.py — text chunking + character alias detection."""

from core.comic.segmentation import detect_character_aliases, merge_characters, segment_text
from core.schemas import CharacterAsset


def _asset(name: str) -> CharacterAsset:
    return CharacterAsset(name=name, l1_prompt=name)


def test_merge_characters_dedups_by_exact_name():
    existing = {"a": _asset("a")}
    new = [_asset("a"), _asset("b")]
    merged, created = merge_characters(existing, new)
    assert set(merged) == {"a", "b"}
    assert created == ["b"]


def test_segment_text_respects_chapter_headings():
    text = "第一章\nintro.\n第二章\nmore text here."
    chunks = segment_text(text)
    assert any("第一章" in c for c in chunks)
    assert any("第二章" in c for c in chunks)


def test_detect_character_aliases_substring_variant():
    existing = {"方鸿渐": _asset("方鸿渐")}
    sugg = detect_character_aliases(existing, ["鸿渐"])
    assert sugg == [("鸿渐", "方鸿渐", "name variant (normalized/substring match)")]


def test_detect_character_aliases_similarity():
    existing = {"alice": _asset("alice")}
    sugg = detect_character_aliases(existing, ["alica"])
    assert sugg and sugg[0][0] == "alica" and sugg[0][1] == "alice"


def test_detect_character_aliases_distinct_names_none():
    existing = {"alice": _asset("alice")}
    assert detect_character_aliases(existing, ["bob"]) == []
