"""core.comic.visual_bible — hash, reconcile apply, and ref helpers for Visual Bible."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from typing import Literal

from core.comic.identity import merge_character_alias, suggestion_from_alias
from core.schemas import (
    CharacterAsset,
    CharacterCanon,
    CharacterStage,
    ColorBible,
    ColorSwatch,
    ComicPagePlan,
    ComicPagePlanSet,
    ProjectState,
    VisualBible,
    VisualBibleReconcileResult,
)

logger = logging.getLogger(__name__)

COSTUME_CHANGE_LOCK_LINE = (
    "do not change hair color, outfit colors, or skin tone across panels "
    "unless action says costume change"
)

ANTI_CHARACTER_SHEET_LINE = (
    "NO character design sheets, turnarounds, model sheets, or multi-view "
    "reference collages inside the page."
)

PERIOD_WARDROBE_LINE = (
    "Period-accurate wardrobe only; no modern hoodies, sneakers, or athleisure "
    "unless action explicitly requires costume change."
)

CONTEMPORARY_WARDROBE_LINE = (
    "Wardrobe must match the project era; do not force historical costume "
    "unless action explicitly requires period dress."
)

DIEGETIC_TEXT_LINE = (
    "Diegetic props that are letters, books, newspapers, signs, or screens must "
    "show blank aged paper or abstract ink texture only — no letterforms, no "
    "Latin or CJK glyphs, no pseudo-script."
)

ANTI_MULTI_AGE_COLLAGE_LINE = (
    "Do not depict multiple age versions of the same person on one page unless "
    "layout_intent explicitly calls for a flashback split."
)

HAIR_STABILITY_LINE = (
    "Keep hair color and hair length stable for each locked identity across panels "
    "unless the character stage explicitly changes."
)

# Generic age/stage cues (CN + EN) → CharacterStageLiteral. Order matters: first match wins.
_STAGE_CUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "child",
        re.compile(
            r"(童年|孩童|小孩|幼年|十三|12岁|13岁|少女时代|as a child|childhood|\bchild\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "teen",
        re.compile(
            r"(少年|十六|17岁|18岁临别|teenager|boarding school|\bteen\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "elder",
        re.compile(r"(老年|暮年|白发苍苍|\belderly\b|\bold man\b|\bold woman\b)", re.IGNORECASE),
    ),
    (
        "adult",
        re.compile(
            r"(临终|写信|成年|交际花|丧子|成人|\badult\b|dying|deathbed)",
            re.IGNORECASE,
        ),
    ),
)


def default_age_look_for_stage(stage: str) -> str:
    """Soft age_look default from stage literal (not novel-specific)."""
    mapping = {
        "child": "about 10–13 years old, clearly a child",
        "teen": "about 16–18 years old, adolescent",
        "adult": "adult, roughly late twenties to forties",
        "elder": "elderly, visibly aged",
        "default": "age matching the story role",
    }
    return mapping.get(stage, mapping["default"])


def infer_stage_from_text(text: str) -> str | None:
    """Infer a stage literal from action/purpose/caption cues, or None."""
    blob = text or ""
    if not blob.strip():
        return None
    for stage, pattern in _STAGE_CUE_PATTERNS:
        if pattern.search(blob):
            return stage
    return None


def _stage_ref_name(canonical: str, stage: str) -> str:
    return f"{canonical}@{stage}"


def _canon_has_stage(canon: CharacterCanon, stage: str) -> bool:
    return any(s.stage == stage for s in canon.stages)


def _rewrite_name_to_stage(name: str, stage: str, bible: VisualBible) -> str:
    """Rewrite bare canonical/alias to Name@stage when that stage exists."""
    base, existing = parse_stage_ref(name)
    if existing != "default" and "@" in (name or ""):
        return name  # already staged
    canonical = resolve_canonical_name(base, bible)
    canon = bible.characters.get(canonical)
    if canon is None or not _canon_has_stage(canon, stage):
        return name
    return _stage_ref_name(canonical, stage)


def resolve_panel_stage_refs(plan: ComicPagePlan, bible: VisualBible) -> ComicPagePlan:
    """Rewrite bare character names to Name@stage using age cues in page/panel text."""
    updated = plan.model_copy(deep=True)
    page_cue = " ".join(
        part
        for part in (
            updated.purpose or "",
            updated.layout_intent or "",
            " ".join(p.action or "" for p in updated.panels),
            " ".join(p.caption or "" for p in updated.panels),
        )
        if part
    )
    page_stage = infer_stage_from_text(page_cue)

    for panel in updated.panels:
        panel_cue = " ".join(
            part for part in (panel.action or "", panel.caption or "", panel.dialogue or "") if part
        )
        stage = infer_stage_from_text(panel_cue) or page_stage
        if not stage:
            continue
        panel.characters = [_rewrite_name_to_stage(name, stage, bible) for name in panel.characters]

    if page_stage:
        updated.reference_characters = [
            _rewrite_name_to_stage(name, page_stage, bible) for name in updated.reference_characters
        ]
    return updated


GENDER_NO_SWAP_LINE = (
    "single human matching locked gender exactly; no gender swap or androgynous "
    "reinterpretation of a gendered canon"
)

_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_PROSE_MARKER_RE = re.compile(
    r"(?i)(,|\bwith\b|\bhair\b|\bexpression\b|\bwearing\b|\bbuild\b|\beyes\b|\bage\b|\bold\b)",
)
_PARENTHETICAL_RE = re.compile(r"（[^）]*）|\([^)]*\)")
_CJK_PROSE_MARKERS = (
    "眼神",
    "衣衫",
    "神情",
    "正在",
    "脸色",
    "瑟瑟",
    "颤抖",
    "衣着",
    "搬运",
    "惊恐",
    "苍白",
    "痴迷",
    "虔诚",
)
_OUTFIT_WORD_RE = re.compile(
    r"(?i)\b("
    r"wearing|hoodie|athletic|sneakers|jacket|suit|dress|skirt|pants|boots|coat|"
    r"sweater|jeans|uniform|robe|vest|tie|blouse|shirt|trousers|athleisure"
    r")\b",
)

_MODERN_WARDROBE_RE = re.compile(
    r"\b("
    r"hoodie|hoodies|sneakers|athleisure|zip-?up|jeans|t-?shirts?|"
    r"sweatpants|trainers|sportswear|sporty|tracksuit|converse|"
    r"sports?\s*shoes|light\s+sports|sporty\s+jacket|athletic\s+wear"
    r")\b",
    re.IGNORECASE,
)

_HISTORICAL_ERA_MARKERS = (
    "1900",
    "1910",
    "1920",
    "18th",
    "19th",
    "20th century",
    "early-20th",
    "early 20th",
    "victorian",
    "edwardian",
    "vienna",
    "period",
    "qing",
    "民国",
    "清末",
    "vienna",
    "belle epoque",
    "meiji",
    "historical",
    "century european",
)
_CONTEMPORARY_ERA_MARKERS = (
    "contemporary",
    "modern day",
    "present day",
    "21st",
    "today",
    "current era",
    "现代",
    "当代",
)

_FEMALE_MARKERS = (
    "寡妇",
    "母亲",
    "少女",
    "女儿",
    "女人",
    "女孩",
    "女士",
    "mother",
    "widow",
    "girl",
    "woman",
    "female",
    "lady",
    "she",
    "her",
)
_MALE_MARKERS = (
    "男仆",
    "男人",
    "先生",
    "小说家",
    "作家",
    "男孩",
    "gentleman",
    "man",
    "male",
    "butler",
    "boy",
    "he",
    "him",
    "约翰",
    "stepfather",
    "继父",
    "novelist",
)

_LETTER_WRITER_MARKERS = (
    "写信",
    "letter writer",
    "narrator",
    "叙述者",
    "陌生女人",
    "unknown woman",
)
_LETTER_READER_MARKERS = ("收信", "letter reader", "reading the letter", "收信人")
_SERVANT_FUNCTION_MARKERS = ("仆", "butler", "servant", "男仆")
_PARENT_FUNCTION_MARKERS = ("母", "妈", "mother", "widow", "父", "father", "parent")
_CHILD_FUNCTION_MARKERS = ("孩子", "child", "son", "daughter", "儿子", "女儿")
_LOVE_INTEREST_MARKERS = ("情人", "lover", "love interest", "被爱")
_PROTAGONIST_MARKERS = ("protagonist", "主角", "novelist", "小说家", "作家")

_MOTHER_ROLE_MARKERS = ("母", "妈", "mother", "widow", "寡妇")
_DAUGHTER_ROLE_MARKERS = ("女", "孩", "narrator", "少女", "女儿", "叙述者")
_COUNT_LOVER_ROLE_MARKERS = ("伯爵", "count", "工厂主", "情人")
_NOVELIST_ROLE_MARKERS = ("小说家", "作家", "novelist")
_SERVANT_ROLE_MARKERS = ("仆", "butler", "约翰")
_MASTER_ROLE_MARKERS = ("主人", "novelist", "作家")


EraClass = Literal["historical", "contemporary", "unspecified"]
GenderLiteral = Literal["male", "female", "nonbinary", "unknown"]


def classify_era(era: str, style_guide: str = "") -> EraClass:
    """Classify project era for wardrobe defaults and banlines."""
    blob = f"{era or ''} {style_guide or ''}".casefold()
    if any(marker in blob for marker in _CONTEMPORARY_ERA_MARKERS):
        return "contemporary"
    if any(marker.casefold() in blob for marker in _HISTORICAL_ERA_MARKERS):
        return "historical"
    return "unspecified"


def infer_era_text(era: str, style_guide: str) -> str:
    """Keep explicit era, else lift a short hint from style_guide, else unspecified."""
    text = (era or "").strip()
    if text:
        return text
    style = (style_guide or "").strip()
    if not style:
        return "unspecified"
    # Prefer a short clause that looks era-like.
    lower = style.casefold()
    for marker in _HISTORICAL_ERA_MARKERS + _CONTEMPORARY_ERA_MARKERS:
        if marker.casefold() in lower:
            return style[:120].strip()
    return "unspecified"


def default_outfit_for_era(era: str, style_guide: str = "") -> str:
    """Era-aware outfit default (no Vienna hardcode for every project)."""
    era_class = classify_era(era, style_guide)
    era_text = (era or "").strip()
    if era_class == "contemporary":
        return "contemporary everyday clothing matching the story setting"
    if era_class == "historical":
        if era_text and era_text.casefold() != "unspecified":
            return f"period-accurate clothing for {era_text}"
        return "period-accurate historical clothing matching the story era"
    return "clothing matching the story setting and era"


def outfit_has_modern_tokens(outfit: str) -> bool:
    """True when outfit text contains modern streetwear tokens."""
    return bool(_MODERN_WARDROBE_RE.search(outfit or ""))


def repair_outfit_lock(outfit: str, *, era: str, style_guide: str = "") -> str:
    """Fill blank outfits and rewrite modern tokens under historical eras."""
    text = (outfit or "").strip()
    era_class = classify_era(era, style_guide)
    if not text:
        return default_outfit_for_era(era, style_guide)
    if era_class == "historical" and outfit_has_modern_tokens(text):
        return default_outfit_for_era(era, style_guide)
    return text


def wardrobe_banline_for_bible(bible: VisualBible) -> str:
    """Era-conditioned wardrobe hard line for image prompts."""
    era_class = classify_era(bible.era, bible.style_guide)
    forbidden = list(bible.era_forbidden_wardrobe or [])
    if not forbidden and era_class == "historical":
        forbidden = ["hoodies", "sneakers", "athleisure", "sports shoes", "jeans", "t-shirts"]
    if era_class == "contemporary":
        line = CONTEMPORARY_WARDROBE_LINE
    else:
        line = PERIOD_WARDROBE_LINE
    if forbidden:
        line = f"{line} Forbidden wardrobe: {', '.join(forbidden)}."
    if bible.era and bible.era.strip().casefold() != "unspecified":
        line = f"Era lock: {bible.era.strip()}. {line}"
    return line


def _blob_has_marker(blob: str, markers: tuple[str, ...]) -> bool:
    lower = blob.casefold()
    return any(marker in blob or marker.casefold() in lower for marker in markers)


def infer_gender(
    *,
    name: str = "",
    role: str = "",
    face_lock: str = "",
    aliases: Iterable[str] | None = None,
    explicit: str = "unknown",
) -> GenderLiteral:
    """Infer gender from explicit field, then role/name/face markers."""
    if explicit in {"male", "female", "nonbinary"}:
        return explicit  # type: ignore[return-value]
    parts = [name or "", role or "", face_lock or ""]
    if aliases:
        parts.extend(aliases)
    blob = " ".join(parts)
    female = _blob_has_marker(blob, _FEMALE_MARKERS)
    male = _blob_has_marker(blob, _MALE_MARKERS)
    if female and not male:
        return "female"
    if male and not female:
        return "male"
    # Face-lock pronouns alone
    face = (face_lock or "").casefold()
    if re.search(r"\b(woman|girl|female|lady)\b", face):
        return "female"
    if re.search(r"\b(man|boy|male|gentleman)\b", face):
        return "male"
    return "unknown"


def gender_prefix(gender: str) -> str:
    """Idempotent face_lock gender phrase."""
    if gender == "male":
        return "adult man"
    if gender == "female":
        return "adult woman"
    if gender == "nonbinary":
        return "adult nonbinary person"
    return ""


def apply_gender_to_face_lock(face_lock: str, gender: str) -> str:
    """Prepend gender phrase when known; strip duplicate prefixes."""
    face = normalize_face_lock(face_lock) or (face_lock or "").strip()
    prefix = gender_prefix(gender)
    if not prefix:
        return face
    # Strip existing gender lead-ins for idempotency.
    face = re.sub(
        r"(?i)^(adult\s+)?(man|woman|male|female|nonbinary person)\s*,\s*",
        "",
        face,
    ).strip()
    if face:
        return f"{prefix}, {face}"
    return prefix


def infer_narrative_function(
    *,
    name: str = "",
    role: str = "",
    explicit: str = "",
) -> str:
    """Infer a short narrative_function tag when markers are clear."""
    text = (explicit or "").strip()
    allowed = {
        "letter_reader",
        "letter_writer",
        "protagonist",
        "love_interest",
        "servant",
        "parent",
        "child",
        "extra",
    }
    if text in allowed:
        return text
    blob = f"{name or ''} {role or ''}"
    if _blob_has_marker(blob, _LETTER_WRITER_MARKERS):
        return "letter_writer"
    if _blob_has_marker(blob, _LETTER_READER_MARKERS):
        return "letter_reader"
    if _blob_has_marker(blob, _SERVANT_FUNCTION_MARKERS):
        return "servant"
    if _blob_has_marker(blob, _PARENT_FUNCTION_MARKERS):
        return "parent"
    if _blob_has_marker(blob, _CHILD_FUNCTION_MARKERS):
        return "child"
    if _blob_has_marker(blob, _LOVE_INTEREST_MARKERS):
        return "love_interest"
    if _blob_has_marker(blob, _PROTAGONIST_MARKERS):
        return "protagonist"
    return "extra"


def narrative_functions_incompatible(a: str, b: str) -> bool:
    """True when functions must not high-merge (reader vs writer)."""
    pair = {(a or "").strip(), (b or "").strip()}
    return pair == {"letter_reader", "letter_writer"}


def genders_conflict(a: str, b: str) -> bool:
    """True when both genders are known and disagree as male vs female."""
    left = (a or "unknown").strip()
    right = (b or "unknown").strip()
    if left in {"", "unknown", "nonbinary"} or right in {"", "unknown", "nonbinary"}:
        return False
    return left != right and {left, right} == {"male", "female"}


def format_identity_line(name: str, canon: CharacterCanon) -> str:
    """Prompt line: identity: Name (gender, narrative_function)."""
    gender = canon.gender or "unknown"
    function = (canon.narrative_function or "extra").strip() or "extra"
    return f"identity: {name} ({gender}, {function})"


def portrait_gender_era_suffix(bible: VisualBible, canon: CharacterCanon) -> str:
    """Extra portrait prompt clauses for gender + era wardrobe."""
    parts = [GENDER_NO_SWAP_LINE]
    if canon.gender in {"male", "female", "nonbinary"}:
        parts.insert(0, f"gender-locked {gender_prefix(canon.gender)}")
    parts.append(wardrobe_banline_for_bible(bible))
    return ", ".join(parts)


def is_illegal_character_name(name: str) -> bool:
    """True when ``name`` looks like a prose description, not a character label."""
    text = (name or "").strip()
    if not text:
        return False
    ascii_count = len(_ASCII_LETTER_RE.findall(text))
    if len(text) > 40 and ascii_count >= 10:
        return True
    if text.count(",") >= 2 and ascii_count >= max(len(text) // 3, 8):
        return True
    if ascii_count > 0 and _PROSE_MARKER_RE.search(text):
        if ascii_count >= max(len(text) // 4, 6):
            return True
    if text.count("，") >= 2:
        return True
    if "，" in text and len(text) >= 8:
        return True
    if len(text) >= 16 and any(marker in text for marker in _CJK_PROSE_MARKERS):
        return True
    return False


def identity_stem(name: str) -> str:
    """Strip stage suffixes and parenthetical role tags: ``R（收信人）`` → ``R``."""
    base, _stage = parse_stage_ref(name)
    return _PARENTHETICAL_RE.sub("", base).strip()


def _identity_lookup_pairs(
    known_names: Iterable[str],
    bible: VisualBible | None,
) -> list[tuple[str, str]]:
    """``(lookup_key, resolved_name)`` pairs, longest key first."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(key: str, resolved: str) -> None:
        text = (key or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        pairs.append((text, resolved))

    if bible is not None:
        for key, canon in bible.characters.items():
            canonical = canon.canonical_name or key
            _add(canonical, canonical)
            _add(key, canonical)
            for alias in canon.aliases:
                _add(alias, canonical)
            stem = identity_stem(canonical)
            if stem:
                _add(stem, canonical)
    for name in known_names:
        resolved = resolve_canonical_name(name, bible) if bible is not None else name
        _add(name, resolved)
        stem = identity_stem(name)
        if stem:
            _add(stem, resolved)
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def match_plan_character_name(
    raw: str,
    known_names: Iterable[str],
    bible: VisualBible | None,
) -> str | None:
    """Map a planner character label onto a known canon/table name, or None."""
    text = (raw or "").strip()
    if not text:
        return None
    base, stage = parse_stage_ref(text)
    staged = stage != "default" and "@" in text
    pairs = _identity_lookup_pairs(known_names, bible)

    def _with_stage(resolved: str) -> str:
        if staged and "@" not in resolved:
            return f"{resolved}@{stage}"
        return resolved

    for key, resolved in pairs:
        if text == key or base == key:
            return _with_stage(resolved)

    best: str | None = None
    best_len = 1
    for key, resolved in pairs:
        if len(key) <= best_len:
            continue
        if key in text or text in key:
            best = resolved
            best_len = len(key)
    if best is None:
        return None
    return _with_stage(best)


def canonicalize_page_plan(
    plan: ComicPagePlan,
    *,
    known_names: Iterable[str],
    visual_bible: VisualBible | None,
) -> ComicPagePlan:
    """Rewrite page character lists onto known identities; drop unmatched prose."""
    known = [name for name in known_names if name]
    updated = plan.model_copy(deep=True)

    def _remap(names: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            mapped = match_plan_character_name(name, known, visual_bible)
            if not mapped or mapped in seen:
                continue
            seen.add(mapped)
            out.append(mapped)
        return out

    updated.reference_characters = _remap(updated.reference_characters)
    for panel in updated.panels:
        panel.characters = _remap(panel.characters)
        if not panel.characters:
            panel.characters = list(updated.reference_characters)
    return updated


def canonical_portrait_ref(
    name: str,
    characters_by_name: dict,
    bible: VisualBible | None,
) -> str | None:
    """Portrait path of the canonical face, when ``name`` is a stage/alias variant."""
    if bible is None:
        return None
    base, stage = parse_stage_ref(name)
    canonical = resolve_canonical_name(base, bible)
    is_base = "@" not in (name or "") and stage == "default" and name == canonical
    if is_base:
        return None
    char = characters_by_name.get(canonical)
    loc = getattr(char, "portrait_local", None) if char is not None else None
    if loc and name != canonical:
        return loc
    return None


def _merge_bible_canon(bible: VisualBible, keep_key: str, drop_key: str) -> None:
    keep = bible.characters[keep_key]
    drop = bible.characters[drop_key]
    merged = _upsert_canon(keep, drop)
    extras = [drop_key, drop.canonical_name, *drop.aliases]
    for alias in extras:
        if alias and alias not in merged.aliases and alias != merged.canonical_name:
            merged.aliases.append(alias)
    bible.characters[keep_key] = merged
    del bible.characters[drop_key]


def _fold_character_row(state: ProjectState, drop: str, keep: str) -> None:
    if drop == keep:
        return
    if keep not in state.characters and drop in state.characters:
        asset = state.characters.pop(drop)
        state.characters[keep] = asset.model_copy(update={"name": keep})
        if drop not in state.characters[keep].aliases:
            state.characters[keep].aliases.append(drop)
        return
    if drop in state.characters and keep in state.characters:
        try:
            merge_character_alias(state, drop, keep)
        except KeyError:
            state.characters.pop(drop, None)
        return
    state.characters.pop(drop, None)


def collapse_duplicate_identities(state: ProjectState) -> bool:
    """Merge forked canons that are the same person (stem or letter role duplicates)."""
    bible = state.visual_bible
    if bible is None:
        return False
    mutated = False

    stem_groups: dict[str, list[str]] = {}
    for key, canon in bible.characters.items():
        stem = identity_stem(canon.canonical_name or key)
        if not stem:
            continue
        stem_groups.setdefault(stem, []).append(key)

    for stem, keys in stem_groups.items():
        if len(keys) < 2:
            continue
        keep = next(
            (
                key
                for key in keys
                if identity_stem(bible.characters[key].canonical_name or key) == stem
                and (bible.characters[key].canonical_name or key) == stem
            ),
            min(keys, key=lambda key: (len(key), key)),
        )
        for drop in keys:
            if drop == keep or drop not in bible.characters or keep not in bible.characters:
                continue
            _merge_bible_canon(bible, keep, drop)
            _fold_character_row(state, drop, keep)
            mutated = True

    role_groups: dict[tuple[str, str], list[str]] = {}
    for key, canon in bible.characters.items():
        function = (canon.narrative_function or "").strip()
        gender = (canon.gender or "unknown").strip()
        if function not in {"letter_writer", "letter_reader"}:
            continue
        if gender in {"", "unknown"}:
            continue
        role_groups.setdefault((function, gender), []).append(key)

    for keys in role_groups.values():
        if len(keys) < 2:
            continue
        keep = max(keys, key=lambda key: (len(bible.characters[key].aliases), -len(key)))
        for drop in keys:
            if drop == keep or drop not in bible.characters or keep not in bible.characters:
                continue
            keep_canon = bible.characters[keep]
            drop_canon = bible.characters[drop]
            if roles_incompatible(keep_canon.role, drop_canon.role):
                continue
            if genders_conflict(keep_canon.gender, drop_canon.gender):
                continue
            _merge_bible_canon(bible, keep, drop)
            _fold_character_row(state, drop, keep)
            mutated = True

    return mutated


def _role_contains_any(role: str, markers: tuple[str, ...]) -> bool:
    lower = role.casefold()
    for marker in markers:
        if marker in role or marker.casefold() in lower:
            return True
    return False


def roles_incompatible(role_a: str, role_b: str) -> bool:
    """True when two role strings describe incompatible person identities."""
    a = (role_a or "").strip()
    b = (role_b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return False

    def _pair(
        left: str,
        right: str,
        markers_a: tuple[str, ...],
        markers_b: tuple[str, ...],
    ) -> bool:
        return _role_contains_any(left, markers_a) and _role_contains_any(right, markers_b)

    incompatible_pairs = (
        (_MOTHER_ROLE_MARKERS, _DAUGHTER_ROLE_MARKERS),
        (_DAUGHTER_ROLE_MARKERS, _MOTHER_ROLE_MARKERS),
        (_COUNT_LOVER_ROLE_MARKERS, _NOVELIST_ROLE_MARKERS),
        (_NOVELIST_ROLE_MARKERS, _COUNT_LOVER_ROLE_MARKERS),
        (_SERVANT_ROLE_MARKERS, _MASTER_ROLE_MARKERS),
        (_MASTER_ROLE_MARKERS, _SERVANT_ROLE_MARKERS),
    )
    return any(_pair(a, b, ma, mb) for ma, mb in incompatible_pairs)


def normalize_face_lock(text: str) -> str:
    """Strip outfit-related words so ``face_lock`` stays facial-only."""
    stripped = re.sub(r",?\s*wearing[^,;]*", "", (text or "").strip(), flags=re.IGNORECASE)
    parts: list[str] = []
    for part in re.split(r"[,;]", stripped):
        chunk = part.strip()
        if not chunk or _OUTFIT_WORD_RE.search(chunk):
            continue
        parts.append(chunk)
    return ", ".join(parts).strip()


_HAIR_MARKER_RE = re.compile(
    r"(?i)\b(hair|bald|balding|curly|straight|braid|ponytail)\b|[发髻鬃]",
)


def _default_hair_lock() -> str:
    return "dark hair"


def _hair_lock_from_canon_face(canon_face: str) -> str | None:
    """Derive a short hair lock from the first ``canon_face`` clause when it mentions hair."""
    text = (canon_face or "").strip()
    if not text:
        return None
    first_clause = re.split(r"[,;]", text, maxsplit=1)[0].strip()
    if not first_clause or not _HAIR_MARKER_RE.search(first_clause):
        return None
    # Hair locks are brief identity tags, not full face prose.
    return first_clause[:80].strip()


DEFAULT_OUTFIT_LOCK = "clothing matching the story setting and era"


def _default_outfit_lock(*, era: str = "", style_guide: str = "") -> str:
    return default_outfit_for_era(era, style_guide)


def ensure_stage_locks(
    stage: CharacterStage,
    *,
    canon_face: str,
    canonical_name: str = "",
    era: str = "",
    style_guide: str = "",
) -> CharacterStage:
    """Fill empty stage locks and repair illegal ``portrait_key`` values."""
    hair_lock = (stage.hair_lock or "").strip()
    if not hair_lock:
        # Prefer a short hair hint from the canon face's first clause before generic default.
        hair_lock = _hair_lock_from_canon_face(canon_face) or _default_hair_lock()
    outfit_lock = repair_outfit_lock(
        (stage.outfit_lock or "").strip(),
        era=era,
        style_guide=style_guide,
    )
    portrait_key = (stage.portrait_key or "").strip()
    if canonical_name and (not portrait_key or is_illegal_character_name(portrait_key)):
        portrait_key = f"{canonical_name}@{stage.stage}"
    age_look = (stage.age_look or "").strip() or default_age_look_for_stage(stage.stage)
    return CharacterStage(
        stage=stage.stage,
        appearance=stage.appearance,
        outfit_lock=outfit_lock,
        hair_lock=hair_lock,
        age_look=age_look,
        portrait_key=portrait_key,
    )


def ensure_canon_locks(
    canon: CharacterCanon,
    *,
    era: str = "",
    style_guide: str = "",
) -> CharacterCanon:
    """Normalize face/gender locks and ensure every stage has hair/outfit/portrait locks."""
    gender = infer_gender(
        name=canon.canonical_name,
        role=canon.role,
        face_lock=canon.face_lock,
        aliases=canon.aliases,
        explicit=canon.gender or "unknown",
    )
    face_lock = apply_gender_to_face_lock(canon.face_lock, gender)
    function = infer_narrative_function(
        name=canon.canonical_name,
        role=canon.role,
        explicit=canon.narrative_function or "",
    )
    stages = [
        ensure_stage_locks(
            stage,
            canon_face=face_lock,
            canonical_name=canon.canonical_name,
            era=era,
            style_guide=style_guide,
        )
        for stage in canon.stages
    ]
    return canon.model_copy(
        update={
            "face_lock": face_lock,
            "gender": gender,
            "narrative_function": function,
            "stages": stages,
        }
    )


def _canonical_for_character(state: ProjectState, name: str) -> str:
    """Resolve a character or alias string to its bible canonical name."""
    if not name:
        return name
    bible = state.visual_bible
    if bible is None:
        return name
    canon = bible.characters.get(name)
    if canon is not None:
        return canon.canonical_name or name
    return _build_alias_to_canonical_map(bible).get(name, name)


def _alias_loses_conflict(owner_role: str, other_role: str) -> bool:
    """True when ``owner_role`` should drop a contested alias to ``other_role``."""
    if not roles_incompatible(other_role, owner_role):
        return False
    loser_winner_pairs = (
        (_MOTHER_ROLE_MARKERS, _DAUGHTER_ROLE_MARKERS),
        (_COUNT_LOVER_ROLE_MARKERS, _NOVELIST_ROLE_MARKERS),
        (_SERVANT_ROLE_MARKERS, _MASTER_ROLE_MARKERS),
    )
    return any(
        _role_contains_any(owner_role, loser_markers)
        and _role_contains_any(other_role, winner_markers)
        for loser_markers, winner_markers in loser_winner_pairs
    )


def _canons_claiming_alias(
    bible: VisualBible,
    alias: str,
    alias_snapshot: dict[str, list[str]] | None = None,
) -> list[str]:
    """Return canonical names whose bible entry key, name, or alias list claims ``alias``."""
    claimed: list[str] = []
    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        aliases = alias_snapshot.get(key, canon.aliases) if alias_snapshot else canon.aliases
        if alias == key or alias == canonical or alias in aliases:
            if canonical not in claimed:
                claimed.append(canonical)
    return claimed


def _drop_incompatible_aliases(
    aliases: list[str],
    owner_role: str,
    state: ProjectState,
    owner_canonical: str = "",
    alias_snapshot: dict[str, list[str]] | None = None,
) -> list[str]:
    """Keep only aliases that are legal names and role-compatible with ``owner_role``."""
    owner_key = (owner_canonical or "").strip()
    owner_resolved = _canonical_for_character(state, owner_key) if owner_key else ""
    bible = state.visual_bible
    kept: list[str] = []
    for alias in aliases:
        if is_illegal_character_name(alias):
            continue
        other_asset = state.characters.get(alias)
        if (
            other_asset is not None
            and alias != owner_key
            and alias != owner_resolved
            and roles_incompatible(other_asset.role or "", owner_role)
        ):
            continue
        if bible is not None:
            other_claimants = [
                canon
                for canon in _canons_claiming_alias(bible, alias, alias_snapshot)
                if canon != owner_resolved
            ]
            incompatible_others = [
                other
                for other in other_claimants
                if roles_incompatible(_role_for_character(state, other), owner_role)
            ]
            if any(
                _alias_loses_conflict(owner_role, _role_for_character(state, other))
                for other in incompatible_others
            ):
                continue
        kept.append(alias)
    return kept


def sanitize_visual_bible_state(state: ProjectState) -> bool:
    """Clean polluted bible/character state and bump to bible_v3. Returns True if mutated."""
    bible = state.visual_bible
    if bible is None:
        return False

    mutated = False

    illegal_character_keys = [
        name for name in list(state.characters) if is_illegal_character_name(name)
    ]
    for name in illegal_character_keys:
        del state.characters[name]
        mutated = True

    for name, asset in state.characters.items():
        cleaned = _drop_incompatible_aliases(
            asset.aliases, asset.role or "", state, owner_canonical=name
        )
        if cleaned != asset.aliases:
            asset.aliases = cleaned
            mutated = True

    illegal_canon_keys = [key for key in list(bible.characters) if is_illegal_character_name(key)]
    for key in illegal_canon_keys:
        del bible.characters[key]
        mutated = True

    inferred_era = infer_era_text(bible.era, bible.style_guide)
    if inferred_era != (bible.era or "").strip():
        bible.era = inferred_era
        mutated = True

    canon_alias_snapshot = {key: list(canon.aliases) for key, canon in bible.characters.items()}

    for key, canon in list(bible.characters.items()):
        owner_role = canon.role or _role_for_character(state, key)
        cleaned_aliases = _drop_incompatible_aliases(
            canon.aliases,
            owner_role,
            state,
            owner_canonical=key,
            alias_snapshot=canon_alias_snapshot,
        )
        fixed = ensure_canon_locks(
            canon.model_copy(update={"aliases": cleaned_aliases, "role": owner_role or canon.role}),
            era=bible.era,
            style_guide=bible.style_guide,
        )
        if fixed.gender == "unknown":
            suggestion = suggestion_from_alias(
                fixed.canonical_name or key,
                "gender:unknown",
                "gender could not be inferred; set male/female explicitly",
            )
            _append_needs_review(state, suggestion)
        if cleaned_aliases != canon.aliases or fixed.model_dump() != canon.model_dump():
            mutated = True
        bible.characters[key] = fixed

    if bible.version != "bible_v3":
        bible.version = "bible_v3"
        mutated = True

    if collapse_duplicate_identities(state):
        mutated = True
        bible = state.visual_bible
        if bible is None:
            return mutated

    old_hash = bible.content_hash
    state.visual_bible = refresh_bible_hash(bible)
    if state.visual_bible.content_hash != old_hash:
        mutated = True

    return mutated


def parse_stage_ref(name: str) -> tuple[str, str]:
    """Split ``Name@stage`` into base name and stage (default ``default``)."""
    text = (name or "").strip()
    if "@" in text:
        base, stage = text.split("@", 1)
        base = base.strip()
        stage = stage.strip() or "default"
        return base, stage
    return text, "default"


def _bible_hash_payload(bible: VisualBible) -> dict:
    characters: dict[str, dict] = {}
    for name, canon in sorted(bible.characters.items()):
        stages = [
            {
                "stage": stage.stage,
                "outfit_lock": stage.outfit_lock,
                "hair_lock": stage.hair_lock,
                "age_look": stage.age_look,
            }
            for stage in canon.stages
        ]
        characters[name] = {
            "face_lock": canon.face_lock,
            "palette_notes": canon.palette_notes,
            "gender": canon.gender,
            "narrative_function": canon.narrative_function,
            "stages": stages,
            "appearance_evidence": [
                {"field": e.field, "quote": e.quote} for e in (canon.appearance_evidence or [])
            ],
        }
    return {
        "style_guide": bible.style_guide,
        "era": bible.era,
        "era_forbidden_wardrobe": list(bible.era_forbidden_wardrobe or []),
        "color": bible.color.model_dump(),
        "characters": characters,
    }


def compute_bible_hash(bible: VisualBible) -> str:
    """SHA-256 digest of style, color, and character locks (ignores content_hash)."""
    payload = json.dumps(_bible_hash_payload(bible), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def refresh_bible_hash(bible: VisualBible) -> VisualBible:
    """Return a copy of ``bible`` with ``content_hash`` set from current locks."""
    return bible.model_copy(update={"content_hash": compute_bible_hash(bible)})


def _ensure_canon_alias(bible: VisualBible, canonical: str, alias: str) -> None:
    if bible is None:
        return
    canon = bible.characters.get(canonical)
    if canon is None:
        return
    if alias not in canon.aliases and alias != canonical:
        canon.aliases.append(alias)


def _apply_color_patches(color: ColorBible, patches: list[ColorSwatch]) -> None:
    """Append or update palette swatches by name."""
    if not patches:
        return
    by_name = {s.name: i for i, s in enumerate(color.palette) if s.name}
    for patch in patches:
        if patch.name and patch.name in by_name:
            color.palette[by_name[patch.name]] = patch
        else:
            color.palette.append(patch)
            if patch.name:
                by_name[patch.name] = len(color.palette) - 1


def _same_face_descriptor(a: str, b: str) -> bool:
    """Return True when two face_lock strings normalize to the same value."""
    return (a or "").strip().lower() == (b or "").strip().lower()


def _upsert_canon(existing: CharacterCanon, incoming: CharacterCanon) -> CharacterCanon:
    """Merge incoming canon fields into an existing canonical character.

    ``face_lock`` is treated as a read-once field: once it has been set on the
    canonical character, subsequent reconciles may not overwrite it unless the
    incoming value normalizes to the same descriptor. This prevents reconcile
    drift from silently mutating a character's locked face.
    """
    updates: dict = {}
    incoming_face = (incoming.face_lock or "").strip()
    existing_face = (existing.face_lock or "").strip()
    if incoming_face:
        if not existing_face or _same_face_descriptor(existing_face, incoming_face):
            updates["face_lock"] = incoming.face_lock
        # else: incoming tried to mutate a locked face — keep original.
    if incoming.palette_notes:
        updates["palette_notes"] = incoming.palette_notes
    if incoming.role:
        updates["role"] = incoming.role
    if incoming.gender and incoming.gender != "unknown":
        updates["gender"] = incoming.gender
    if incoming.narrative_function:
        updates["narrative_function"] = incoming.narrative_function
    merged = existing.model_copy(update=updates) if updates else existing.model_copy(deep=True)

    for alias in incoming.aliases:
        if alias not in merged.aliases and alias != merged.canonical_name:
            merged.aliases.append(alias)

    # Merge source-fidelity evidence: keep existing groundings, append new ones.
    existing_quotes = {(e.field, e.quote) for e in (merged.appearance_evidence or [])}
    for e in incoming.appearance_evidence or []:
        if (e.field, e.quote) not in existing_quotes:
            merged.appearance_evidence.append(e)

    stage_index = {s.stage: i for i, s in enumerate(merged.stages)}
    for stage in incoming.stages:
        if stage.stage in stage_index:
            idx = stage_index[stage.stage]
            old = merged.stages[idx]
            merged.stages[idx] = CharacterStage(
                stage=stage.stage,
                outfit_lock=stage.outfit_lock or old.outfit_lock,
                hair_lock=stage.hair_lock or old.hair_lock,
                age_look=stage.age_look or old.age_look,
                portrait_key=stage.portrait_key or old.portrait_key,
            )
        else:
            merged.stages.append(stage)
    return merged


def _install_reconcile_bible(
    out: ProjectState,
    result: VisualBibleReconcileResult,
) -> None:
    """Create or update visual bible from reconcile style, color, and canons."""
    if out.visual_bible is None:
        out.visual_bible = VisualBible(
            version="bible_v1",
            style_guide=result.style_guide or "",
            era=result.era or "",
            era_forbidden_wardrobe=list(result.era_forbidden_wardrobe or []),
            color=result.color or ColorBible(palette=[], lighting="", forbidden=[]),
            characters={c.canonical_name: c for c in result.canons},
            sheet_ref_local=None,
            content_hash="",
        )
        _backfill_canon_evidence(out)
        return

    bible = out.visual_bible
    for canon in result.canons:
        existing = bible.characters.get(canon.canonical_name)
        if existing is None:
            bible.characters[canon.canonical_name] = canon
        else:
            bible.characters[canon.canonical_name] = _upsert_canon(existing, canon)

    if not bible.style_guide and result.style_guide:
        bible.style_guide = result.style_guide
    if not bible.era and result.era:
        bible.era = result.era
    if result.era_forbidden_wardrobe and not bible.era_forbidden_wardrobe:
        bible.era_forbidden_wardrobe = list(result.era_forbidden_wardrobe)

    if result.color_patches:
        _apply_color_patches(bible.color, result.color_patches)

    _backfill_canon_evidence(out)


def _backfill_canon_evidence(out: ProjectState) -> None:
    """Copy verbatim source-fidelity evidence from extraction assets onto canons.

    The bible canon does not re-extract quotes itself, so without this the
    reconcile step would silently drop the character's grounding and render
    prompts with no evidence. Matches by canonical name first, then by alias.
    Existing canon evidence is never overwritten.
    """
    bible = out.visual_bible
    if bible is None:
        return
    for canon in bible.characters.values():
        if canon.appearance_evidence:
            continue
        asset = out.characters.get(canon.canonical_name)
        if asset is None:
            for alias in canon.aliases:
                if alias in out.characters:
                    asset = out.characters[alias]
                    break
        if asset is None:
            continue
        quotes = asset.appearance.appearance_evidence or []
        if quotes:
            canon.appearance_evidence = list(quotes)


def _ensure_canonical_character(
    out: ProjectState,
    canonical: str,
    result: VisualBibleReconcileResult,
) -> None:
    """Ensure ``canonical`` exists in ``state.characters`` before alias merge."""
    if canonical in out.characters:
        return
    canon = None
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(canonical)
    if canon is None:
        for row in result.canons:
            if row.canonical_name == canonical:
                canon = row
                break
    if canon is not None:
        l1 = l1_from_canon(canon)
        out.characters[canonical] = CharacterAsset(
            name=canonical,
            role=canon.role or "",
            l1_prompt=l1,
            portrait_prompt=l1,
        )
        return
    for merge in result.merges:
        if merge.canonical == canonical and merge.alias in out.characters:
            asset = out.characters[merge.alias]
            out.characters[canonical] = asset.model_copy(update={"name": canonical})
            return


def _append_needs_review(out: ProjectState, suggestion) -> None:
    if not any(
        s.new_name == suggestion.new_name and s.candidate == suggestion.candidate
        for s in out.needs_review
    ):
        out.needs_review.append(suggestion)


def _role_for_character(out: ProjectState, name: str) -> str:
    asset = out.characters.get(name)
    if asset is not None and (asset.role or "").strip():
        return asset.role.strip()
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(name)
        if canon is not None and (canon.role or "").strip():
            return canon.role.strip()
        canonical = _build_alias_to_canonical_map(out.visual_bible).get(name)
        if canonical and canonical != name:
            return _role_for_character(out, canonical)
    return ""


def _gender_for_character(out: ProjectState, name: str) -> str:
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(name)
        if canon is None:
            canonical = _build_alias_to_canonical_map(out.visual_bible).get(name)
            if canonical:
                canon = out.visual_bible.characters.get(canonical)
        if canon is not None and (canon.gender or "unknown") != "unknown":
            return canon.gender
    return "unknown"


def _narrative_function_for_character(out: ProjectState, name: str) -> str:
    if out.visual_bible is not None:
        canon = out.visual_bible.characters.get(name)
        if canon is None:
            canonical = _build_alias_to_canonical_map(out.visual_bible).get(name)
            if canonical:
                canon = out.visual_bible.characters.get(canonical)
        if canon is not None and (canon.narrative_function or "").strip():
            return canon.narrative_function.strip()
    return ""


def apply_reconcile(
    state: ProjectState,
    result: VisualBibleReconcileResult,
) -> ProjectState:
    """Apply reconcile merges, stage links, and low-confidence review rows."""
    out = state.model_copy(deep=True)

    _install_reconcile_bible(out, result)

    for merge in result.merges:
        if merge.confidence == "high":
            role_alias = _role_for_character(out, merge.alias)
            role_canon = _role_for_character(out, merge.canonical)
            gender_alias = _gender_for_character(out, merge.alias)
            gender_canon = _gender_for_character(out, merge.canonical)
            fn_alias = _narrative_function_for_character(out, merge.alias)
            fn_canon = _narrative_function_for_character(out, merge.canonical)
            if (
                roles_incompatible(role_alias, role_canon)
                or genders_conflict(gender_alias, gender_canon)
                or narrative_functions_incompatible(fn_alias, fn_canon)
            ):
                suggestion = suggestion_from_alias(merge.alias, merge.canonical, merge.reason)
                _append_needs_review(out, suggestion)
                continue
            _ensure_canonical_character(out, merge.canonical, result)
            try:
                merge_character_alias(out, merge.alias, merge.canonical)
            except KeyError as exc:
                logger.warning(
                    "visual bible merge skipped (%s → %s): %s",
                    merge.alias,
                    merge.canonical,
                    exc,
                )
            if out.visual_bible is not None:
                _ensure_canon_alias(out.visual_bible, merge.canonical, merge.alias)
        else:
            suggestion = suggestion_from_alias(merge.alias, merge.canonical, merge.reason)
            _append_needs_review(out, suggestion)

    if out.visual_bible is not None:
        for link in result.stages:
            canon = out.visual_bible.characters.get(link.of_canonical)
            if canon is None:
                continue
            _ensure_canon_alias(out.visual_bible, link.of_canonical, link.name)
            existing = {s.stage for s in canon.stages}
            if link.stage not in existing:
                canon.stages.append(
                    CharacterStage(
                        stage=link.stage,
                        outfit_lock="",
                        hair_lock="",
                        portrait_key=f"{link.of_canonical}@{link.stage}",
                    )
                )

    return out


def alias_to_canonical_map(bible: VisualBible) -> dict[str, str]:
    """Public alias of ``_build_alias_to_canonical_map`` for pipeline callers."""
    return _build_alias_to_canonical_map(bible)


def _build_alias_to_canonical_map(bible: VisualBible) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        for alias in canon.aliases:
            if alias and alias != canonical:
                mapping[alias] = canonical
    return mapping


def rewrite_pageset_from_bible(pageset: ComicPagePlanSet, bible: VisualBible) -> ComicPagePlanSet:
    """Rewrite panel/reference names in ``pageset`` using bible alias map."""
    mapping = _build_alias_to_canonical_map(bible)
    if not mapping:
        return pageset
    return pageset.model_copy(
        update={
            "pages": [rewrite_page_plan_names(plan, mapping) for plan in pageset.pages],
        }
    )


def ensure_stage_portrait_assets(state: ProjectState) -> None:
    """Ensure each stage ``portrait_key`` has a ``CharacterAsset`` for rendering."""
    bible = state.visual_bible
    if bible is None:
        return
    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        base_asset = state.characters.get(canonical)
        for stage in canon.stages:
            portrait_key = (stage.portrait_key or "").strip()
            if not portrait_key or portrait_key == canonical:
                continue
            if portrait_key in state.characters:
                continue
            l1 = l1_from_canon(canon, stage.stage)
            if base_asset is not None and not l1:
                state.characters[portrait_key] = base_asset.model_copy(
                    update={"name": portrait_key}
                )
            else:
                state.characters[portrait_key] = CharacterAsset(
                    name=portrait_key,
                    role=canon.role or (base_asset.role if base_asset else ""),
                    l1_prompt=l1,
                    portrait_prompt=l1,
                )


def resolve_canonical_name(name: str, bible: VisualBible | None) -> str:
    """Resolve ``name`` to canonical bible character name when possible."""
    if bible is None:
        return name
    base, _stage = parse_stage_ref(name)
    if base in bible.characters:
        return base
    mapping = _build_alias_to_canonical_map(bible)
    return mapping.get(base, base)


def resolve_character_asset(
    name: str,
    characters_by_name: dict[str, CharacterAsset],
    bible: VisualBible | None = None,
) -> CharacterAsset | None:
    """Look up a character asset, resolving bible aliases and stage portrait keys."""
    if name in characters_by_name:
        return characters_by_name[name]
    if bible is None:
        return None
    base, stage = parse_stage_ref(name)
    canon = bible.characters.get(base)
    if canon is None:
        canonical = resolve_canonical_name(base, bible)
        canon = bible.characters.get(canonical)
        base = canonical
    if canon is not None:
        stage_row = next((s for s in canon.stages if s.stage == stage), None)
        if stage_row is not None and stage_row.portrait_key:
            key = stage_row.portrait_key
            if key in characters_by_name:
                return characters_by_name[key]
    canonical = resolve_canonical_name(base, bible)
    return characters_by_name.get(canonical)


def sync_characters_from_bible(state: ProjectState) -> None:
    """Sync character L1 prompts from bible canons and rewrite cached page plans."""
    bible = state.visual_bible
    if bible is None:
        return

    mapping = _build_alias_to_canonical_map(bible)

    for key, canon in bible.characters.items():
        canonical = canon.canonical_name or key
        asset = state.characters.get(canonical)
        if asset is None:
            continue
        l1 = l1_from_canon(canon)
        if l1:
            asset.l1_prompt = l1
            asset.portrait_prompt = l1
        for alias in canon.aliases:
            if alias and alias != canonical and alias not in asset.aliases:
                asset.aliases.append(alias)

    ensure_stage_portrait_assets(state)

    if not mapping:
        return

    for cache_key, pageset in list(state.page_cache.items()):
        state.page_cache[cache_key] = rewrite_pageset_from_bible(pageset, bible)


def format_color_bible_block(bible: VisualBible) -> str:
    """Format palette, lighting, and forbidden colors for image prompts."""
    color = bible.color
    lines: list[str] = []
    for swatch in color.palette:
        if not swatch.hex:
            continue
        label = swatch.name or swatch.usage or "color"
        detail = f"{label} {swatch.hex}"
        if swatch.usage and swatch.name and swatch.usage != swatch.name:
            detail = f"{swatch.name} {swatch.hex} ({swatch.usage})"
        lines.append(detail)
    if color.lighting:
        lines.append(f"lighting: {color.lighting}")
    if color.forbidden:
        lines.append(f"forbidden: {', '.join(color.forbidden)}")
    if not lines:
        return ""
    return "Color bible:\n" + "\n".join(f"  {line}" for line in lines)


def l1_from_canon(canon: CharacterCanon, stage: str = "default") -> str:
    """Build an L1 identity string from canon face lock and stage outfit/hair locks."""
    parts: list[str] = []
    prefix = gender_prefix(canon.gender or "unknown")
    face = (canon.face_lock or "").strip()
    if prefix and not face.casefold().startswith(prefix.casefold()):
        parts.append(prefix)
    if face:
        parts.append(face)
    if canon.palette_notes:
        parts.append(canon.palette_notes)
    stage_row = next((s for s in canon.stages if s.stage == stage), None)
    if stage_row is None and canon.stages:
        stage_row = canon.stages[0]
    if stage_row is not None:
        if stage_row.age_look:
            parts.append(stage_row.age_look)
        if stage_row.outfit_lock:
            parts.append(stage_row.outfit_lock)
        if stage_row.hair_lock:
            parts.append(stage_row.hair_lock)
    body = ", ".join(parts)
    quotes = [(it.quote or "").strip() for it in (canon.appearance_evidence or [])]
    quotes = [q for q in quotes if q]
    if quotes:
        body = (body + "; source: " + "; ".join(f"“{q}”" for q in quotes)).strip()
    return body


def _rewrite_name_list(names: list[str], mapping: dict[str, str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        mapped = mapping.get(name, name)
        if mapped not in seen:
            out.append(mapped)
            seen.add(mapped)
    return out


def backfill_panel_characters(
    plan: ComicPagePlan,
    known_names: Iterable[str],
) -> ComicPagePlan:
    """Fill empty panel ``characters`` from page refs and action substring matches."""
    known = [name for name in known_names if name]
    updated = plan.model_copy(deep=True)
    for panel in updated.panels:
        if panel.characters:
            continue
        found: list[str] = []
        seen: set[str] = set()
        for name in updated.reference_characters:
            if name not in seen:
                found.append(name)
                seen.add(name)
        action = panel.action or ""
        for name in known:
            if name in action and name not in seen:
                found.append(name)
                seen.add(name)
        panel.characters = found
    return updated


def rewrite_page_plan_names(
    plan: ComicPagePlan,
    mapping: dict[str, str],
) -> ComicPagePlan:
    """Rewrite panel and reference character names using ``mapping``."""
    updated = plan.model_copy(deep=True)
    updated.reference_characters = _rewrite_name_list(updated.reference_characters, mapping)
    for panel in updated.panels:
        panel.characters = _rewrite_name_list(panel.characters, mapping)
    return updated


def build_visual_sheet(bible: VisualBible) -> None:
    """Phase B stub — visual sheet generation is deferred to phase C."""
    return None


def _page_character_names(plan: ComicPagePlan) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for name in plan.reference_characters:
        if name not in seen:
            names.append(name)
            seen.add(name)
    for panel in plan.panels:
        for name in panel.characters:
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _portrait_path_for_name(
    name: str,
    characters_by_name: dict,
    bible: VisualBible,
) -> str | None:
    base, stage = parse_stage_ref(name)
    canonical = resolve_canonical_name(base, bible)
    canon = bible.characters.get(canonical)
    if canon is not None:
        stage_row = next((s for s in canon.stages if s.stage == stage), None)
        if stage_row is not None and stage_row.portrait_key:
            key = stage_row.portrait_key
            char = characters_by_name.get(key)
            if char is not None and char.portrait_local:
                return char.portrait_local
    char = resolve_character_asset(name, characters_by_name, bible)
    if char is not None and char.portrait_local:
        return char.portrait_local
    return None


def collect_finished_page_refs(
    plan: ComicPagePlan,
    characters_by_name: dict,
    bible: VisualBible,
    *,
    prev_blank: str | None = None,
    max_refs: int = 9,
) -> list[str]:
    """Collect i2i reference paths: sheet, portraits, then optional previous blank."""
    refs: list[str] = []
    seen: set[str] = set()

    def _add(path: str | None) -> bool:
        if not path or path in seen:
            return False
        refs.append(path)
        seen.add(path)
        return len(refs) >= max_refs

    if bible.sheet_ref_local:
        _add(bible.sheet_ref_local)

    for name in _page_character_names(plan):
        if len(refs) >= max_refs:
            break
        _add(_portrait_path_for_name(name, characters_by_name, bible))

    if len(refs) < max_refs and prev_blank:
        _add(prev_blank)

    return refs
