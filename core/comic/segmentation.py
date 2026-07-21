"""core.comic.segmentation — source-text chunking and character merging.

Two pure helpers used by the comic pipeline:

- ``segment_text`` splits a long novel into processable chunks. It prefers
  chapter/volume boundaries, then falls back to a token budget applied at
  paragraph granularity, and overlaps the tail of one chunk into the next so
  narration stays continuous across breaks.
- ``merge_characters`` folds freshly extracted characters into the project's
  running character table by exact name, reporting which names are new (so the
  pipeline knows which portraits to generate).
"""

import re
from collections.abc import Iterable

from core.schemas import CharacterAsset

# Chapter / volume headings at the start of a line.
_HEADING_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千]+\s*[章节卷]"
    r"|chapter\s+[0-9]+"
    r"|volume\s+[0-9]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _estimate_tokens(text: str) -> int:
    """Rough token count without a tokenizer dependency.

    CJK characters are counted ~1 token each; other text ~0.25 token per char.
    Overestimating is safe here — it only makes chunks a bit smaller.
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + max(0, (len(text) - cjk)) // 4


def _chapter_blocks(text: str) -> list[str]:
    """Split text into chapter/volume blocks, keeping each heading with its body."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [stripped] if stripped else []
    starts = [0] + [m.start() for m in matches]
    ends = [m.start() for m in matches] + [len(text)]
    blocks = [text[s:e].strip() for s, e in zip(starts, ends, strict=False)]
    return [b for b in blocks if b]


def _lines(block: str) -> list[str]:
    return [ln.strip() for ln in block.split("\n") if ln.strip()]


def segment_text(
    text: str,
    *,
    max_tokens: int = 8000,
    overlap_chars: int = 600,
) -> list[str]:
    """Split ``text`` into ordered chunks for per-chunk processing.

    Strategy:
    1. Break at chapter/volume headings (each heading stays with its body).
    2. Within a block, accumulate lines until the estimated token count would
       exceed ``max_tokens``, then start a new chunk. A single line longer than
       the budget is kept whole (lines are not split mid-sentence).
    3. Prepend the tail ``overlap_chars`` of the previous chunk to the next one
       so context is not abruptly cut at a boundary.

    Args:
        text: the full source novel/scene text.
        max_tokens: per-chunk token budget (approximate).
        overlap_chars: how many trailing characters of a chunk are repeated at
            the head of the following chunk.

    Returns:
        A list of non-empty text chunks in source order.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _chapter_blocks(text)
    chunks: list[str] = []
    for block in blocks:
        tail = ""  # chapters are hard boundaries: no overlap across them
        cur = ""
        for line in _lines(block):
            candidate = f"{cur}\n\n{line}" if cur else line
            if cur and _estimate_tokens(candidate) > max_tokens:
                seg_tail = cur[-overlap_chars:] if len(cur) > overlap_chars else cur
                final = f"{tail}\n\n{cur}" if tail else cur
                chunks.append(final)
                tail = seg_tail
                cur = line
            else:
                cur = candidate
        if cur:
            seg_tail = cur[-overlap_chars:] if len(cur) > overlap_chars else cur
            final = f"{tail}\n\n{cur}" if tail else cur
            chunks.append(final)
            tail = seg_tail
    return chunks


def merge_characters(
    existing: dict[str, CharacterAsset],
    new: "Iterable[CharacterAsset]",
) -> tuple[dict[str, CharacterAsset], list[str]]:
    """Merge extracted characters into the running character table by name.

    Characters already present (exact name match) are kept and reused so the
    same portrait is not regenerated. New names are added and reported so the
    pipeline can generate their portraits.

    Args:
        existing: the project's current ``name -> CharacterAsset`` table.
        new: characters extracted from the current chunk.

    Returns:
        A tuple of (updated table, list of newly added character names).
    """
    merged = dict(existing)
    created: list[str] = []
    for char in new:
        if char.name not in merged:
            merged[char.name] = char
            created.append(char.name)
    return merged, created
