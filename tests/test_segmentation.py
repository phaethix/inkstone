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


def test_merge_characters_mints_unique_unnamed():
    first = CharacterAsset(name="unnamed", appearance={"outfit_top": "tray"})
    second = CharacterAsset(name="unnamed", appearance={"outfit_top": "rifle"})
    merged, created = merge_characters({}, [first, second])
    assert "unnamed" in merged
    assert any(n.startswith("unnamed_") for n in merged)
    assert len(merged) == 2
    assert len(created) == 2
    tops = {a.appearance.outfit_top for a in merged.values()}
    assert tops == {"tray", "rifle"}


def test_merge_characters_fills_empty_appearance_fields():
    existing = {
        "Da Shi": CharacterAsset(name="Da Shi", appearance={"hair": "short"}),
    }
    new = [
        CharacterAsset(
            name="Da Shi",
            role="detective",
            appearance={"hair": "ignored", "eyewear": "aviator sunglasses"},
        )
    ]
    merged, created = merge_characters(existing, new)
    assert created == []
    assert merged["Da Shi"].appearance.hair == "short"
    assert merged["Da Shi"].appearance.eyewear == "aviator sunglasses"
    assert merged["Da Shi"].role == "detective"


def test_segment_text_respects_chapter_headings():
    text = "第一章\nintro.\n第二章\nmore text here."
    chunks = segment_text(text)
    assert any("第一章" in c for c in chunks)
    assert any("第二章" in c for c in chunks)


def test_segment_text_special_chinese_headings():
    text = "序章\nprologue.\n楔子\nsetup.\n尾声\nending.\n番外 特别篇\nextra.\n终章\nfinale."
    chunks = segment_text(text)
    assert len(chunks) >= 5
    for heading in ("序章", "楔子", "尾声", "番外", "终章"):
        assert any(c.lstrip().startswith(heading) for c in chunks), f"missing split at {heading}"


def test_segment_text_special_chinese_heading_with_number():
    text = "序章一\nbody.\n第二章\nmore."
    chunks = segment_text(text)
    assert len(chunks) >= 2
    assert any(c.lstrip().startswith("序章一") for c in chunks)
    assert any(c.lstrip().startswith("第二章") for c in chunks)


def test_segment_text_english_chapter_word_numbers():
    text = "Chapter One\nintro.\nChapter Twenty\noutro."
    chunks = segment_text(text)
    assert len(chunks) >= 2
    assert any(c.lstrip().startswith("Chapter One") for c in chunks)
    assert any(c.lstrip().startswith("Chapter Twenty") for c in chunks)


def test_segment_text_part_and_section_headings():
    text = "Part 1\nfirst.\nSection 2\nsecond."
    chunks = segment_text(text)
    assert len(chunks) >= 2
    assert any(c.lstrip().startswith("Part 1") for c in chunks)
    assert any(c.lstrip().startswith("Section 2") for c in chunks)


def test_segment_text_no_heading_yields_single_block():
    text = "plain paragraph one.\nplain paragraph two."
    chunks = segment_text(text)
    assert len(chunks) == 1
    assert "plain paragraph one" in chunks[0]
    assert "plain paragraph two" in chunks[0]


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
