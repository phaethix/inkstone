"""tests/test_segmentation.py — text chunking and character merging (no network)."""

from core.comic.segmentation import merge_characters, segment_text
from core.schemas import CharacterAsset


def test_segment_single_short_text_is_one_chunk():
    text = "第一章\n一个年轻人站在甲板上，海风吹过他的衣角。"
    chunks = segment_text(text, max_tokens=8000)
    assert len(chunks) == 1
    assert "第一章" in chunks[0]


def test_segment_respects_chapter_boundaries():
    text = "第一章\n方鸿渐在甲板上眺望海面。\n第二章\n苏文纨在休息厅里读书。"
    chunks = segment_text(text, max_tokens=8000)
    assert len(chunks) == 2
    assert "第一章" in chunks[0] and "第二章" not in chunks[0]
    assert "第二章" in chunks[1] and "第一章" not in chunks[1]


def test_segment_splits_long_block_by_token_budget():
    # Many short lines under a tiny budget -> several chunks.
    lines = "\n".join(f"这是第 {i} 句话，用来凑一点长度。" for i in range(40))
    chunks = segment_text(lines, max_tokens=30, overlap_chars=10)
    assert len(chunks) > 1
    # every line ends up in exactly one chunk (no line is dropped or duplicated
    # beyond the intentional overlap).
    for i in range(40):
        needle = f"这是第 {i} 句话"
        assert sum(needle in c for c in chunks) >= 1


def test_segment_overlaps_tail_into_next_chunk():
    lines = "\n".join(f"段落内容编号 {i:02d} 一些文字用来填充。" for i in range(20))
    chunks = segment_text(lines, max_tokens=40, overlap_chars=24)
    assert len(chunks) >= 2
    # The second chunk should begin with the tail of the first.
    assert chunks[1].startswith(chunks[0][-24:])


def test_merge_characters_keeps_existing_and_reports_new():
    existing = {"方鸿渐": CharacterAsset(name="方鸿渐", l1_prompt="x")}
    new = [
        CharacterAsset(name="方鸿渐", l1_prompt="y"),  # already present -> reused
        CharacterAsset(name="苏文纨", l1_prompt="z"),  # new -> created
    ]
    merged, created = merge_characters(existing, new)
    assert set(merged) == {"方鸿渐", "苏文纨"}
    # existing asset is reused, not overwritten by the duplicate extraction
    assert merged["方鸿渐"].l1_prompt == "x"
    assert created == ["苏文纨"]
