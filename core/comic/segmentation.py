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
_ENGLISH_NUMBERS = (
    "one|two|three|four|five|six|seven|eight|nine|ten"
    "|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
    "|seventeen|eighteen|nineteen|twenty"
)
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9一二三四五六七八九十百千]+\s*[章节卷]"
    r"|(?:序章|楔子|尾声|番外|终章)(?:[0-9一二三四五六七八九十百千]+|\s+[^\n]+)?"
    r"|chapter\s+(?:[0-9]+|"
    + _ENGLISH_NUMBERS
    + r")"
    r"|volume\s+[0-9]+"
    r"|(?:part|section)\s+[0-9]+"
    r")",
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


def _normalize_name(name: str) -> str:
    """Lowercase and drop whitespace + common CJK honorifics for fuzzy compare."""
    s = name.strip().lower()
    for suf in (
        "先生",
        "小姐",
        "女士",
        "太太",
        "哥",
        "姐",
        "弟",
        "妹",
        "叔",
        "姨",
        "舅",
        "公",
        "婆",
    ):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


def detect_character_aliases(
    existing: dict[str, CharacterAsset],
    new_names: list[str],
    *,
    threshold: float = 0.8,
) -> list[tuple[str, str, str]]:
    """Flag new character names that likely refer to an existing character.

    Uses a cheap, deterministic heuristic (substring on normalized names, or
    ``difflib`` similarity) so the same person referred to by a variant name
    (e.g. ``方鸿渐`` vs ``鸿渐``) is surfaced for human review instead of being
    silently forked into a distinct character — which would spawn a duplicate
    portrait and fracture cross-chapter consistency. Nothing is auto-merged;
    callers record the suggestions in ``state.needs_review`` and let a human
    decide.

    Args:
        existing: the project's current character table.
        new_names: names freshly added this chunk (not yet in ``existing``).
        threshold: ``difflib`` similarity at/above which a name is flagged.

    Returns:
        A list of ``(new_name, candidate, reason)`` tuples, one per suggestion.
    """
    from difflib import SequenceMatcher

    suggestions: list[tuple[str, str, str]] = []
    existing_norm = {n: _normalize_name(n) for n in existing}
    for name in new_names:
        norm = _normalize_name(name)
        if not norm:
            continue
        for exn, exnorm in existing_norm.items():
            if not exnorm or exn == name:
                continue
            if norm == exnorm or norm in exnorm or exnorm in norm:
                suggestions.append((name, exn, "name variant (normalized/substring match)"))
                break
            if SequenceMatcher(None, norm, exnorm).ratio() >= threshold:
                suggestions.append((name, exn, "similar name (difflib match)"))
                break
    return suggestions
