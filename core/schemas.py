"""core.schemas — structured contracts (Pydantic models).

These models are the single source of truth for the three JSON contracts that
flow through the comic pipeline:

- ``CharacterAsset`` / ``StoryElements``: the extraction product returned by the
  ``extract_story_elements`` forced function call.
- ``Storyboard`` / ``Panel``: the planning product returned by ``plan_storyboard``.
- ``ProjectState``: the resumable ``state.json`` persisted between stages.

Each LLM-facing model doubles as a **function tool ``parameters`` definition**:
``to_tool_schema`` turns a model into an OpenAI/Agnes tool schema via
``model_json_schema``, so the wire contract and the parsed type never drift.
Runtime-only fields (populated by the pipeline, not the model) are annotated with
``SkipJsonSchema`` so they never leak into the tool schema shown to the model.
"""

import ast
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

logger = logging.getLogger(__name__)


def coerce_jsonish(value: Any) -> Any:
    """If ``value`` is a JSON object/array (or JSON-encoded string), parse it.

    Some chat providers stringify nested structures inside tool-call arguments
    even after the top-level ``arguments`` blob has been ``json.loads``'d.
    Unwrap repeatedly so double-encoded payloads still become objects/lists.

    When the string is almost-JSON (unescaped quotes in dialogue, Python
    literals), fall back to ``json_repair`` / ``ast.literal_eval`` before giving up.
    """
    for _ in range(3):
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value
        # Markdown fences occasionally wrap tool JSON.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if not text or text[0] not in "[{'\"":
            return value
        try:
            value = json.loads(text)
            continue
        except json.JSONDecodeError:
            pass
        try:
            value = ast.literal_eval(text)
            continue
        except (ValueError, SyntaxError, MemoryError):
            pass
        try:
            repaired = repair_json(text, return_objects=True)
        except Exception:
            return value
        if repaired is None or repaired == "" or repaired == text:
            return value
        value = repaired
    return value


def coerce_str(value: Any) -> Any:
    """Normalize free-text LLM fields to a string.

    Handles scalars, lists, and small dicts (``{"shot": "wide"}`` →
    ``"shot: wide"``) so Pydantic ``str`` fields do not raise ``string_type``.
    """
    value = coerce_jsonish(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            text = coerce_str(item).strip()
            if not text:
                continue
            name = str(key).strip()
            parts.append(f"{name}: {text}" if name else text)
        return "; ".join(parts)
    if isinstance(value, list):
        parts = [coerce_str(item).strip() for item in value]
        return ", ".join(part for part in parts if part)
    return str(value)


def coerce_str_list(value: Any) -> Any:
    """Normalize name/tag lists (``list[str]``) from common LLM shapes."""
    value = coerce_jsonish(value)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]
    if isinstance(value, dict):
        # Prefer values when they look like names; else use keys
        # (e.g. ``{"Fogg": true}`` or ``{"0": "Fogg"}``).
        values = [coerce_str(v).strip() for v in value.values()]
        values = [v for v in values if v and v not in {"true", "false", "1", "0"}]
        if values:
            return values
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            item = coerce_jsonish(item)
            if isinstance(item, dict):
                if "name" in item:
                    name = coerce_str(item.get("name")).strip()
                    if name:
                        names.append(name)
                else:
                    names.extend(coerce_str_list(item))
            else:
                name = coerce_str(item).strip()
                if name:
                    names.append(name)
        return names
    text = coerce_str(value).strip()
    return [text] if text else []


def coerce_object_list(value: Any) -> Any:
    """Normalize lists of nested models (characters / settings / panels).

    Decodes stringified elements and wraps a lone object into a one-item list.
    Keeps already-constructed Pydantic models (constructor / resume paths).
    Non-object entries that cannot be decoded are dropped.
    """
    value = coerce_jsonish(value)
    if value is None or value == "":
        return []
    if isinstance(value, BaseModel):
        return [value]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return value
    objects: list[Any] = []
    for item in value:
        item = coerce_jsonish(item)
        if isinstance(item, (dict, BaseModel)):
            objects.append(item)
    return objects


def coerce_list(value: Any) -> Any:
    """Backward-compatible alias used by older call sites / tests."""
    return coerce_object_list(value)


def coerce_dialogue(value: Any) -> Any:
    """Normalize panel dialogue to ``str | None``.

    Chat models often emit speaker maps (``{"Name": "line"}``) or lists of
    lines instead of a single string. Layout only draws a string bubble, so
    flatten those shapes here.
    """
    value = coerce_jsonish(value)
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        lines: list[str] = []
        for speaker, line in value.items():
            text = coerce_str(line).strip()
            if not text:
                continue
            name = str(speaker).strip()
            lines.append(f"{name}: {text}" if name else text)
        return "\n".join(lines) or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            coerced = coerce_dialogue(item)
            if coerced:
                parts.append(coerced)
        return "\n".join(parts) or None
    text = coerce_str(value).strip()
    return text or None


def coerce_size(value: Any) -> Any:
    """Normalize panel size to ``WxH`` (default ``1024x1024``)."""
    value = coerce_jsonish(value)
    if isinstance(value, list) and len(value) >= 2:
        width = coerce_str(value[0]).strip()
        height = coerce_str(value[1]).strip()
        if width and height:
            return f"{width}x{height}"
    text = coerce_str(value).strip()
    if not text:
        return "1024x1024"
    if "," in text and "x" not in text.lower():
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) == 2:
            return f"{parts[0]}x{parts[1]}"
    return text


def decode_tool_arguments(args_raw: Any) -> dict:
    """Parse a provider tool-call ``arguments`` blob into a dict.

    Accepts an already-decoded mapping, or a JSON / almost-JSON string. Uses the
    same repair path as ``coerce_jsonish`` so a single broken quote in the outer
    payload does not fail the whole pipeline before field validators run.
    """
    if isinstance(args_raw, dict):
        return args_raw
    if args_raw is None:
        raise RuntimeError("chat: tool arguments missing")
    if not isinstance(args_raw, str):
        kind = type(args_raw).__name__
        raise RuntimeError(f"chat: tool arguments must be a JSON object, got {kind}")
    parsed = coerce_jsonish(args_raw)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"chat: tool arguments not valid JSON: {args_raw[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"chat: tool arguments must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


_COMIC_STYLE_HINT = "manhua/comic style: clean black ink line art, soft cel shading, flat colors"

# Resumable pipeline stages.
Stage = Literal[
    "extract",
    "storyboard",
    "page_plan",
    "portraits",
    "panels",
    "pages",
    "layout",
    "export",
]

RenderMode = Literal["finished_page", "panel_compose"]

# LLM sometimes fuses ``"field": "X"`` into a single key ``field："X”…`` / ``field: X``.
_FUSED_FIELD_KEY = re.compile(
    r"^(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*(?P<rest>.*)$",
    re.DOTALL,
)


def unwrap_quoted_fragment(text: str) -> str:
    """Pull a value out of a fused-key remnant after ``field:`` / ``field：``."""
    text = (text or "").strip()
    if not text:
        return ""
    # Models often mix ASCII and curly quotes (e.g. ``"四·二八”运动现场``).
    quoted = re.match(r'^["\'“‘「](.+?)["\'”’」]', text)
    if quoted:
        inner = quoted.group(1).strip()
        if inner:
            return inner
    for left, right in (('"', '"'), ("'", "'"), ("“", "”"), ("「", "」")):
        start = text.find(left)
        if start < 0:
            continue
        end = text.find(right, start + 1)
        if end > start:
            inner = text[start + 1 : end].strip()
            if inner:
                return inner
    for sep in (",", "，", "\n", ";", "；"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return text.strip().strip("\"'“”「」")


# Backward-compatible alias used by earlier Setting patch / tests.
_unwrap_quoted_name = unwrap_quoted_fragment
_FUSED_NAME_KEY = re.compile(r"^name\s*[:：]\s*(.*)$", re.IGNORECASE | re.DOTALL)


def repair_fused_keys(
    obj: dict[str, Any],
    fields: set[str] | frozenset[str],
    *,
    stash_value_into: str | None = None,
) -> dict[str, Any]:
    """Split fused LLM keys like ``name："四·二八”…`` back into real fields.

    When the fused key's map-value is non-empty prose and ``stash_value_into`` is
    set (e.g. ``description``), store that prose if the target field is blank.
    """
    out = dict(obj)
    for key, val in list(out.items()):
        key_s = str(key).strip()
        match = _FUSED_FIELD_KEY.match(key_s)
        if not match:
            continue
        field = match.group("field")
        if field not in fields:
            continue
        if coerce_str(out.get(field)).strip():
            out.pop(key, None)
            continue
        extracted = unwrap_quoted_fragment(match.group("rest"))
        if not extracted:
            extracted = match.group("rest").strip().strip("\"'“”「」")
        if not extracted:
            continue
        out[field] = extracted
        out.pop(key, None)
        text = coerce_str(val).strip()
        if text and stash_value_into and not coerce_str(out.get(stash_value_into)).strip():
            out[stash_value_into] = text
    return out


def ensure_str_field(
    obj: dict[str, Any],
    field: str,
    *,
    aliases: tuple[str, ...] = (),
    default: str = "unnamed",
) -> dict[str, Any]:
    """Ensure ``field`` is a non-empty string, using aliases then ``default``."""
    out = dict(obj)
    if coerce_str(out.get(field)).strip():
        return out
    for alias in aliases:
        alt = out.get(alias)
        if alt is not None and coerce_str(alt).strip():
            out[field] = coerce_str(alt).strip()
            return out
    out[field] = default
    return out


def coerce_model_list(value: Any, model_cls: type[BaseModel]) -> list[Any]:
    """Decode a list of objects and validate each item; drop unrecoverable ones."""
    raw = coerce_object_list(value)
    out: list[Any] = []
    for index, item in enumerate(raw):
        if isinstance(item, model_cls):
            out.append(item)
            continue
        try:
            out.append(model_cls.model_validate(item))
        except (ValidationError, TypeError, ValueError) as exc:
            logger.warning(
                "dropping invalid %s at index %s: %s",
                model_cls.__name__,
                index,
                exc,
            )
    return out



class EvidenceQuote(BaseModel):
    """A verbatim quote from the source text that grounds a claim.

    Used to anchor character appearance fields (hair, outfit, body type, etc.)
    to the original novel excerpt. Each EvidenceQuote carries the field it
    supports, the exact substring (≤ 25 chars), and the character offset.
    """

    model_config = ConfigDict(extra="ignore")

    field: str
    quote: str
    offset: int

    @field_validator("field")
    @classmethod
    def _field_not_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("evidence field name must be non-empty")
        return v

    @field_validator("quote")
    @classmethod
    def _quote_length(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("evidence quote must be non-empty")
        if len(v) > 25:
            raise ValueError("evidence quote must be ≤ 25 chars")
        return v

    @field_validator("offset")
    @classmethod
    def _offset_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("evidence offset must be ≥ 0")
        return v


class Appearance(BaseModel):
    """Structured character appearance — the sole information source for the
    hardened prompt description."""

    model_config = ConfigDict(extra="ignore")

    hair: str = ""
    eyewear: str = ""
    outfit_top: str = ""
    outfit_bottom: str = ""
    shoes: str = ""
    body_type: str = ""
    distinguishing: str = ""
    appearance_evidence: list[EvidenceQuote] = Field(default_factory=list)

    @field_validator(
        "hair",
        "eyewear",
        "outfit_top",
        "outfit_bottom",
        "shoes",
        "body_type",
        "distinguishing",
        mode="before",
    )
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class CharacterAsset(BaseModel):
    """A character extracted from the source text; reused across chunks by name."""

    model_config = ConfigDict(extra="ignore")

    name: str
    role: str = ""
    appearance: Appearance = Field(default_factory=Appearance)

    @model_validator(mode="before")
    @classmethod
    def _ensure_name(cls, value: Any) -> Any:
        """Fill missing/blank ``name`` from role (or common aliases).

        Extract sometimes returns ``{"role": "Antagonist, …"}`` with no name;
        without this, ``StoryElements.model_validate`` aborts the whole job.
        Also repairs fused keys like ``name："方鸿渐”…``.
        """
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        out = repair_fused_keys(
            value,
            {"name", "role", "character_name", "character", "label", "l1_prompt"},
        )
        if coerce_str(out.get("name")).strip():
            return out
        for key in ("character_name", "character", "label"):
            alt = out.get(key)
            if alt is not None and coerce_str(alt).strip():
                return {**out, "name": coerce_str(alt).strip()}
        role_text = coerce_str(out.get("role")).strip()
        if role_text:
            # Prefer the first comma-segment so "Antagonist, ETO Enforcer" → "Antagonist".
            stand_in = role_text.split(",", 1)[0].strip() or role_text
            return {**out, "name": stand_in}
        return {**out, "name": "unnamed"}

    @field_validator("appearance", mode="before")
    @classmethod
    def _coerce_appearance(cls, value: Any) -> Any:
        value = coerce_jsonish(value)
        if value is None or value == "" or value == []:
            return {}
        if isinstance(value, str):
            # Non-JSON prose — stash as distinguishing rather than crash.
            return {"distinguishing": value}
        return value

    # Hardened description inlined into every panel prompt. Prefer deriving from
    # ``appearance`` via ``build_l1_from_appearance``; the model may still fill this.
    l1_prompt: str = ""
    # t2i prompt for the character design sheet (portrait).
    portrait_prompt: str = Field(
        default="",
        description=(
            "standalone t2i prompt for a character design sheet / reference "
            f"illustration; {_COMIC_STYLE_HINT}"
        ),
    )
    # Names merged into this identity (alias ledger). Runtime-maintained.
    aliases: SkipJsonSchema[list[str]] = Field(default_factory=list)
    # Runtime-only: local path of the generated portrait. Filled by the pipeline,
    # never requested from the model, so it is hidden from the tool schema.
    portrait_local: SkipJsonSchema[str | None] = None

    @field_validator("name", "role", "l1_prompt", "portrait_prompt", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("aliases", mode="before")
    @classmethod
    def _coerce_aliases(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("portrait_local", mode="before")
    @classmethod
    def _coerce_portrait_local(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        text = coerce_str(value).strip()
        return text or None


class Setting(BaseModel):
    """A scene/location extracted from the source text."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    scene_prompt: str = ""

    @model_validator(mode="before")
    @classmethod
    def _ensure_name(cls, value: Any) -> Any:
        """Fill missing ``name`` and repair fused LLM keys like ``name："四·二八”…``."""
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        out = repair_fused_keys(
            value,
            {"name", "description", "scene_prompt", "setting_name", "location", "place"},
            stash_value_into="description",
        )
        if coerce_str(out.get("name")).strip():
            return out
        out = ensure_str_field(
            out,
            "name",
            aliases=("setting_name", "location", "place", "label", "title", "scene"),
            default="",
        )
        if coerce_str(out.get("name")).strip():
            return out
        desc = coerce_str(out.get("description")).strip()
        if desc:
            out["name"] = desc[:48]
            return out
        out["name"] = "unnamed"
        return out

    @field_validator("name", "description", "scene_prompt", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class StoryElements(BaseModel):
    """Return payload of the ``extract_story_elements`` forced function call."""

    model_config = ConfigDict(extra="ignore")

    characters: list[CharacterAsset] = Field(default_factory=list)
    settings: list[Setting] = Field(default_factory=list)
    style_guide: str = Field(
        default="",
        description=(
            "concise English art-direction string shared across all panels; "
            f"default to {_COMIC_STYLE_HINT}"
        ),
    )

    @field_validator("characters", mode="before")
    @classmethod
    def _coerce_characters(cls, value: Any) -> Any:
        return coerce_model_list(value, CharacterAsset)

    @field_validator("settings", mode="before")
    @classmethod
    def _coerce_settings(cls, value: Any) -> Any:
        return coerce_model_list(value, Setting)

    @field_validator("style_guide", mode="before")
    @classmethod
    def _coerce_style_guide(cls, value: Any) -> Any:
        return coerce_str(value)


class Panel(BaseModel):
    """A single comic panel within a storyboard."""

    model_config = ConfigDict(extra="ignore")

    panel_id: str
    characters_present: list[str] = Field(default_factory=list)
    setting_ref: str = ""
    action: str = Field(
        default="",
        description="visual action for the panel; English OK for image models",
    )
    dialogue: str | None = Field(
        default=None,
        description=(
            "speech-bubble text shown to readers; MUST use the same language as "
            "the source novel excerpt (Chinese source → Chinese dialogue). "
            "Prefer short quotes close to the source; do not translate."
        ),
    )
    caption: str | None = Field(
        default=None,
        description=(
            "narration / caption bar text (not speech). Same language as the source. "
            "Use for time/place/narrator lines; leave null when unused."
        ),
    )
    sfx: str | None = Field(
        default=None,
        description=(
            "sound-effect / onomatopoeia lettering (e.g. 轰隆). Same language as source. "
            "Leave null when unused."
        ),
    )
    # Pipeline-owned prompt text. Hidden from the tool schema so the model does
    # not invent a competing prompt; ``ConsistencyEngine.build_panel_prompt`` is
    # the sole authority at render time. Kept for legacy state.json compatibility.
    panel_prompt: SkipJsonSchema[str] = ""
    reference_characters: list[str] = Field(default_factory=list)
    size: str = "1024x1024"

    @model_validator(mode="before")
    @classmethod
    def _ensure_panel_id(cls, value: Any) -> Any:
        """Repair fused keys and guarantee a non-empty ``panel_id``."""
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        out = repair_fused_keys(
            value,
            {
                "panel_id",
                "action",
                "setting_ref",
                "dialogue",
                "caption",
                "sfx",
                "size",
            },
            stash_value_into="action",
        )
        return ensure_str_field(
            out,
            "panel_id",
            aliases=("id", "panel", "panelId"),
            default="panel",
        )

    @field_validator(
        "panel_id",
        "setting_ref",
        "action",
        "panel_prompt",
        mode="before",
    )
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("characters_present", "reference_characters", mode="before")
    @classmethod
    def _coerce_name_lists(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("dialogue", "caption", "sfx", mode="before")
    @classmethod
    def _coerce_lettering(cls, value: Any) -> Any:
        text = coerce_dialogue(value)
        if isinstance(text, str):
            text = text.strip() or None
        return text

    @field_validator("size", mode="before")
    @classmethod
    def _coerce_size(cls, value: Any) -> Any:
        return coerce_size(value)


class PagePanelSpec(BaseModel):
    """A single panel slot within a finished-page plan."""

    model_config = ConfigDict(extra="ignore")

    panel_id: str
    role: str = "action"
    shape_hint: str = "rect"
    shot: str = ""
    action: str = ""
    characters: list[str] = Field(default_factory=list)
    setting_ref: str = ""
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None
    lettering_notes: str = ""
    speaker: str = ""
    timeline: Literal["present", "past", "liminal", ""] = ""

    @model_validator(mode="before")
    @classmethod
    def _ensure_panel_id(cls, value: Any) -> Any:
        """Repair fused keys and guarantee a non-empty ``panel_id``."""
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        out = repair_fused_keys(
            value,
            {
                "panel_id",
                "role",
                "shape_hint",
                "shot",
                "action",
                "setting_ref",
                "dialogue",
                "caption",
                "sfx",
                "lettering_notes",
                "speaker",
                "timeline",
            },
            stash_value_into="action",
        )
        return ensure_str_field(
            out,
            "panel_id",
            aliases=("id", "panel", "panelId"),
            default="panel",
        )

    @field_validator(
        "panel_id",
        "role",
        "shape_hint",
        "shot",
        "action",
        "setting_ref",
        "lettering_notes",
        "speaker",
        mode="before",
    )
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("timeline", mode="before")
    @classmethod
    def _coerce_timeline(cls, value: Any) -> Any:
        text = coerce_str(value).strip().casefold()
        if text in {"present", "past", "liminal"}:
            return text
        return ""

    @field_validator("characters", mode="before")
    @classmethod
    def _coerce_characters(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("dialogue", "caption", "sfx", mode="before")
    @classmethod
    def _coerce_lettering(cls, value: Any) -> Any:
        text = coerce_dialogue(value)
        if isinstance(text, str):
            text = text.strip() or None
        return text


class LetteringBox(BaseModel):
    """Normalized page rectangle for deferred lettering overlay."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["caption", "dialogue", "sfx"]
    panel_id: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.4
    h: float = 0.12

    @field_validator("panel_id", mode="before")
    @classmethod
    def _coerce_panel_id(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("x", "y", "w", "h", mode="before")
    @classmethod
    def _coerce_float(cls, value: Any) -> Any:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


class ComicPagePlan(BaseModel):
    """A single finished-page layout plan."""

    model_config = ConfigDict(extra="ignore")

    page_id: str
    purpose: str = ""
    layout_intent: str = ""
    timeline: Literal["present", "past", "liminal", ""] = ""
    panels: list[PagePanelSpec] = Field(default_factory=list)
    lettering_boxes: list[LetteringBox] = Field(default_factory=list)
    reference_characters: list[str] = Field(default_factory=list)
    setting_refs: list[str] = Field(default_factory=list)
    covers_beats: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _ensure_page_id(cls, value: Any) -> Any:
        """Repair fused keys and guarantee a non-empty ``page_id``."""
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        out = repair_fused_keys(
            value,
            {"page_id", "purpose", "layout_intent", "timeline"},
            stash_value_into="purpose",
        )
        return ensure_str_field(
            out,
            "page_id",
            aliases=("id", "page", "pageId"),
            default="page",
        )

    @field_validator("page_id", "purpose", "layout_intent", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("timeline", mode="before")
    @classmethod
    def _coerce_timeline(cls, value: Any) -> Any:
        text = coerce_str(value).strip().casefold()
        if text in {"present", "past", "liminal"}:
            return text
        return ""

    @field_validator("panels", mode="before")
    @classmethod
    def _coerce_panels(cls, value: Any) -> Any:
        return coerce_model_list(value, PagePanelSpec)

    @field_validator("lettering_boxes", mode="before")
    @classmethod
    def _coerce_lettering_boxes(cls, value: Any) -> Any:
        return coerce_model_list(value, LetteringBox)

    @field_validator("reference_characters", "setting_refs", "covers_beats", mode="before")
    @classmethod
    def _coerce_name_lists(cls, value: Any) -> Any:
        return coerce_str_list(value)


class KeyBeat(BaseModel):
    """A dramatizable turning point that should appear as drawable scene(s)."""

    model_config = ConfigDict(extra="ignore")

    beat_id: str
    summary: str = ""
    must_draw: bool = True
    characters: list[str] = Field(default_factory=list)
    setting_hint: str = ""

    @field_validator("beat_id", "summary", "setting_hint", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("characters", mode="before")
    @classmethod
    def _coerce_characters(cls, value: Any) -> Any:
        return coerce_str_list(value)


class KeyBeatSet(BaseModel):
    """Key beats extracted for one chunk or project window."""

    model_config = ConfigDict(extra="ignore")

    beats: list[KeyBeat] = Field(default_factory=list)

    @field_validator("beats", mode="before")
    @classmethod
    def _coerce_beats(cls, value: Any) -> Any:
        return coerce_model_list(value, KeyBeat)


class ComicPagePlanSet(BaseModel):
    """Finished-page plans for one storyboard chunk (unit)."""

    model_config = ConfigDict(extra="ignore")

    unit_id: str = ""
    pages: list[ComicPagePlan] = Field(default_factory=list)

    @field_validator("unit_id", mode="before")
    @classmethod
    def _coerce_unit_id(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("pages", mode="before")
    @classmethod
    def _coerce_pages(cls, value: Any) -> Any:
        return coerce_model_list(value, ComicPagePlan)


class Storyboard(BaseModel):
    """Return payload of the ``plan_storyboard`` forced function call (one chunk)."""

    model_config = ConfigDict(extra="ignore")

    chapter_id: str
    panels: list[Panel] = Field(default_factory=list)

    @field_validator("chapter_id", mode="before")
    @classmethod
    def _coerce_chapter_id(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("panels", mode="before")
    @classmethod
    def _coerce_panels(cls, value: Any) -> Any:
        return coerce_model_list(value, Panel)


class SourceSpan(BaseModel):
    """原文回链：chunk 文本内的字符偏移 + 自洽 text + 章节标识。

    ``start``/``end`` 是传入 ``plan_page_script`` 的同一段 ``chunk`` 文本内字符
    偏移（含/不含），``text`` 由 screenwriter 在 ``PageScript.model_validate`` 后
    服务端反推为 ``chunk[start:end]``，保证与切片自洽；``chapter_id`` 来自
    ``Storyboard.chapter_id``。
    """

    model_config = ConfigDict(extra="ignore")

    start: int  # chunk 文本字符偏移（含）
    end: int  # chunk 文本字符偏移（不含）
    text: str = ""  # 服务端反推 = chunk[start:end]
    chapter_id: str = ""  # 来自 board.chapter_id


class CausalLink(BaseModel):
    """单条因果：cause/effect 两端语义 + 可选原文 span。"""

    model_config = ConfigDict(extra="ignore")

    cause: str = ""
    effect: str = ""
    cause_span: SourceSpan | None = None
    effect_span: SourceSpan | None = None


class PageScriptPage(BaseModel):
    """一页分镜的可选遗留审计元数据（非质量闸门）。"""

    model_config = ConfigDict(extra="ignore")

    page_index: int = 0
    required_information: str = ""  # 该页声称覆盖的关键信息（遗留审计字段）
    causal_links: list[CausalLink] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    panel_ids: list[str] = Field(default_factory=list)  # 引用 Storyboard.panel_id


class PageScript(BaseModel):
    """一个 chunk 的可选遗留 PageScript 审计产物（storyboard 之后生成；非质量闸门）。"""

    model_config = ConfigDict(extra="ignore")

    chapter_id: str = ""
    pages: list[PageScriptPage] = Field(default_factory=list)
    # 内容审核拒绝页；coverage 仍计入分母，视为未覆盖。
    skipped_pages: list[int] = Field(default_factory=list)


class ChunkCache(BaseModel):
    """Per-chunk cache of panel-era billable chat-API results.

    Holds ``extract_story_elements`` / ``plan_storyboard`` (and optional legacy
    ``page_script``). Finished-page plans live in ``ProjectState.page_cache`` —
    a sibling map — so the default ``finished_page`` mode can skip storyboard
    without stuffing page plans into this panel-era object. Either field here
    may be ``None`` while a chunk is mid-flight; only the missing step is re-run.
    """

    model_config = ConfigDict(extra="ignore")

    elements: StoryElements | None = None
    storyboard: Storyboard | None = None
    # D2 遗留 PageScript 审计元数据；resume 跳过已生成块（不重复计费）。
    page_script: PageScript | None = None


class CharacterAliasSuggestion(BaseModel):
    """A new character name that likely refers to an already-known character.

    Detected by a cheap heuristic (name-variant / similarity) so the same person
    called by a variant (e.g. ``方鸿渐`` vs ``鸿渐``) is surfaced for human review
    rather than silently forked into a second character (which would spawn a
    duplicate portrait and fracture cross-chapter consistency). Nothing is
    auto-merged — the human decides.
    """

    model_config = ConfigDict(extra="ignore")

    new_name: str
    candidate: str
    reason: str = ""
    # True for high-confidence heuristics (substring / normalized equality).
    # Never auto-merged — UI may highlight as "suggested one-click merge".
    suggested: bool = False


class ModelSnapshot(BaseModel):
    """Model identifiers captured at project creation (for provenance / resume)."""

    model_config = ConfigDict(extra="ignore")

    chat: str = ""
    t2i: str = ""
    i2i: str = ""


class GeneratedPage(BaseModel):
    """A generated finished-page image and its story position."""

    model_config = ConfigDict(extra="ignore")

    local: str
    blank_local: str | None = None
    lettering_version: str = ""
    page_id: str = ""
    unit_index: int = 0
    page_index: int = 0
    mode: Literal["finished", "finished_lettered", "composed_fallback"] = "finished"
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None

    @field_validator("page_id", mode="before")
    @classmethod
    def _coerce_page_id(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("dialogue", "caption", "sfx", mode="before")
    @classmethod
    def _coerce_lettering(cls, value: Any) -> Any:
        text = coerce_dialogue(value)
        if isinstance(text, str):
            text = text.strip() or None
        return text


class GeneratedPanel(BaseModel):
    """A generated panel and the immutable storyboard position that produced it."""

    model_config = ConfigDict(extra="ignore")

    local: str
    chunk_index: int = 0
    panel_index: int = 0
    source_panel_id: str = ""
    dialogue: str | None = None
    caption: str | None = None
    sfx: str | None = None
    url: str | None = None
    expires_at: str | None = None

    @field_validator("dialogue", "caption", "sfx", mode="before")
    @classmethod
    def _coerce_lettering(cls, value: Any) -> Any:
        text = coerce_dialogue(value)
        if isinstance(text, str):
            text = text.strip() or None
        return text


class GeneratedAssets(BaseModel):
    """All generated artifacts: character portraits and per-panel images."""

    model_config = ConfigDict(extra="ignore")

    portraits: dict[str, str] = Field(default_factory=dict)
    panels: dict[str, GeneratedPanel] = Field(default_factory=dict)
    pages: dict[str, GeneratedPage] = Field(default_factory=dict)


CharacterStageLiteral = Literal["child", "teen", "adult", "elder", "default"]


class ColorSwatch(BaseModel):
    """A named color entry in the project visual bible palette."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    hex: str = ""
    usage: str = ""

    @field_validator("name", "hex", "usage", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class ColorBible(BaseModel):
    """Project-locked palette, lighting, and forbidden color directions."""

    model_config = ConfigDict(extra="ignore")

    palette: list[ColorSwatch] = Field(default_factory=list)
    lighting: str = ""
    forbidden: list[str] = Field(default_factory=list)

    @field_validator("palette", mode="before")
    @classmethod
    def _coerce_palette(cls, value: Any) -> Any:
        return coerce_model_list(value, ColorSwatch)

    @field_validator("lighting", mode="before")
    @classmethod
    def _coerce_lighting(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("forbidden", mode="before")
    @classmethod
    def _coerce_forbidden(cls, value: Any) -> Any:
        return coerce_str_list(value)


class CharacterStage(BaseModel):
    """Age- or context-specific appearance lock for a canonical character."""

    model_config = ConfigDict(extra="ignore")

    stage: CharacterStageLiteral = "default"
    appearance: Appearance = Field(default_factory=Appearance)
    outfit_lock: str = ""
    hair_lock: str = ""
    age_look: str = ""
    portrait_key: str = ""

    @field_validator("appearance", mode="before")
    @classmethod
    def _coerce_appearance(cls, value: Any) -> Any:
        value = coerce_jsonish(value)
        if value is None or value == "" or value == []:
            return {}
        if isinstance(value, str):
            return {"distinguishing": value}
        return value

    @field_validator("outfit_lock", "hair_lock", "age_look", "portrait_key", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


GenderLiteral = Literal["male", "female", "nonbinary", "unknown"]


class CharacterCanon(BaseModel):
    """Canonical character identity with shared face lock and optional stages."""

    model_config = ConfigDict(extra="ignore")

    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    face_lock: str = ""
    palette_notes: str = ""
    stages: list[CharacterStage] = Field(default_factory=list)
    role: str = ""
    gender: GenderLiteral = "unknown"
    narrative_function: str = ""

    @field_validator(
        "canonical_name",
        "face_lock",
        "palette_notes",
        "role",
        "narrative_function",
        mode="before",
    )
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("gender", mode="before")
    @classmethod
    def _coerce_gender(cls, value: Any) -> Any:
        text = coerce_str(value).strip().casefold()
        if text in {"male", "female", "nonbinary", "unknown"}:
            return text
        if not text:
            return "unknown"
        return "unknown"

    @field_validator("aliases", mode="before")
    @classmethod
    def _coerce_aliases(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("stages", mode="before")
    @classmethod
    def _coerce_stages(cls, value: Any) -> Any:
        return coerce_model_list(value, CharacterStage)


class VisualBible(BaseModel):
    """Project-level style, color, and canonical character locks."""

    model_config = ConfigDict(extra="ignore")

    version: str = "bible_v1"
    style_guide: str = ""
    era: str = ""
    era_forbidden_wardrobe: list[str] = Field(default_factory=list)
    color: ColorBible = Field(default_factory=ColorBible)
    characters: dict[str, CharacterCanon] = Field(default_factory=dict)
    sheet_ref_local: str | None = None
    content_hash: str = ""

    @field_validator("version", "style_guide", "era", "content_hash", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("era_forbidden_wardrobe", mode="before")
    @classmethod
    def _coerce_era_forbidden_wardrobe(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("color", mode="before")
    @classmethod
    def _coerce_color(cls, value: Any) -> Any:
        value = coerce_jsonish(value)
        if value is None or value == "":
            return {}
        return value

    @field_validator("sheet_ref_local", mode="before")
    @classmethod
    def _coerce_sheet_ref_local(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        text = coerce_str(value).strip()
        return text or None


class VisualBibleMerge(BaseModel):
    """High- or low-confidence alias merge suggestion from bible reconcile."""

    model_config = ConfigDict(extra="ignore")

    alias: str
    canonical: str
    confidence: Literal["high", "low"]
    reason: str = ""

    @field_validator("alias", "canonical", "reason", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class VisualBibleStageLink(BaseModel):
    """Attach an age-stage identity under an existing canonical character."""

    model_config = ConfigDict(extra="ignore")

    name: str
    stage: CharacterStageLiteral
    of_canonical: str
    reason: str = ""

    @field_validator("name", "of_canonical", "reason", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class VisualBibleKeep(BaseModel):
    """A new name that should remain an independent canonical character."""

    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str = ""

    @field_validator("name", "reason", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)


class VisualBibleReconcileResult(BaseModel):
    """LLM tool payload for bible create/update."""

    model_config = ConfigDict(extra="ignore")

    merges: list[VisualBibleMerge] = Field(default_factory=list)
    stages: list[VisualBibleStageLink] = Field(default_factory=list)
    keeps: list[VisualBibleKeep] = Field(default_factory=list)
    color_patches: list[ColorSwatch] = Field(default_factory=list)
    style_guide: str = ""
    era: str = ""
    era_forbidden_wardrobe: list[str] = Field(default_factory=list)
    color: ColorBible | None = None
    canons: list[CharacterCanon] = Field(default_factory=list)

    @field_validator("merges", mode="before")
    @classmethod
    def _coerce_merges(cls, value: Any) -> Any:
        return coerce_model_list(value, VisualBibleMerge)

    @field_validator("stages", mode="before")
    @classmethod
    def _coerce_stages(cls, value: Any) -> Any:
        return coerce_model_list(value, VisualBibleStageLink)

    @field_validator("keeps", mode="before")
    @classmethod
    def _coerce_keeps(cls, value: Any) -> Any:
        return coerce_model_list(value, VisualBibleKeep)

    @field_validator("color_patches", mode="before")
    @classmethod
    def _coerce_color_patches(cls, value: Any) -> Any:
        return coerce_model_list(value, ColorSwatch)

    @field_validator("style_guide", "era", mode="before")
    @classmethod
    def _coerce_style_guide(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("era_forbidden_wardrobe", mode="before")
    @classmethod
    def _coerce_era_forbidden_wardrobe(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("color", mode="before")
    @classmethod
    def _coerce_color(cls, value: Any) -> Any:
        value = coerce_jsonish(value)
        if value is None or value == "":
            return None
        return value

    @field_validator("canons", mode="before")
    @classmethod
    def _coerce_canons(cls, value: Any) -> Any:
        return coerce_model_list(value, CharacterCanon)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class ProjectState(BaseModel):
    """Resumable project state persisted to ``state.json`` between stages."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    source_file: str = ""
    source_fingerprint: str = ""
    structure_fingerprint: str = ""
    render_fingerprint: str = ""
    created_at: str = Field(default_factory=_now_iso)
    model_snapshot: ModelSnapshot = Field(default_factory=ModelSnapshot)
    stage: Stage = "extract"
    characters: dict[str, CharacterAsset] = Field(default_factory=dict)
    # Cross-chunk scene/location registry (exact-name merge).
    settings: dict[str, Setting] = Field(default_factory=dict)
    chunks_done: list[str] = Field(default_factory=list)
    # Dedup key set for resume: already-generated panel state keys are skipped on rerun.
    panels_done: list[str] = Field(default_factory=list)
    # Panel state keys that must be redrawn (e.g. after an alias merge).
    stale_panels: list[str] = Field(default_factory=list)
    # Panels rejected by the upstream content filter; recorded (not regenerated)
    # so a rerun stays honest about what was skipped instead of retrying blindly.
    skipped: list[str] = Field(default_factory=list)
    # Whole chunks whose text was rejected by the upstream content filter during
    # extraction/storyboard/portrait; recorded so a rerun stays honest about them.
    skipped_chunks: list[str] = Field(default_factory=list)
    # Cross-chapter character-name variants flagged for human review (no auto-merge).
    # Populated by the alias detector so a person called by a variant name is not
    # silently forked into a second character.
    needs_review: list[CharacterAliasSuggestion] = Field(default_factory=list)
    # Per-chunk cache of extraction + storyboard results so a resume reuses them
    # instead of re-calling the (billable) chat API for already-planned chunks.
    chunk_cache: dict[str, ChunkCache] = Field(default_factory=dict)
    render_mode: RenderMode = "finished_page"
    page_cache: dict[str, ComicPagePlanSet] = Field(default_factory=dict)
    beat_cache: dict[str, KeyBeatSet] = Field(default_factory=dict)
    pages_done: list[str] = Field(default_factory=list)
    stale_pages: list[str] = Field(default_factory=list)
    skipped_pages: list[str] = Field(default_factory=list)
    generated: GeneratedAssets = Field(default_factory=GeneratedAssets)
    visual_bible: VisualBible | None = None
    errors: str = "logs/errors.jsonl"
    active_elapsed_seconds: float = 0.0

    def save(self, path: str | Path) -> None:
        """Persist state atomically, so interruption cannot truncate ``state.json``."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                temp.write(self.model_dump_json(indent=2))
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, p)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: str | Path) -> "ProjectState":
        """Load and validate state from a ``state.json`` file."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def to_tool_schema(model: type[BaseModel], name: str, description: str) -> dict:
    """Turn a Pydantic model into an OpenAI/Agnes function-tool definition.

    The model's ``model_json_schema`` becomes the tool's ``parameters``, so the
    forced function call's contract is generated from the same type used to parse
    its result. Runtime-only fields (``SkipJsonSchema``) are excluded automatically.
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


class CoverageMetric(BaseModel):
    """单指标的原型覆盖率统计（required/causal/span 复用此结构；非质量闸门）。

    ``coverage_ratio = covered / total``；当 ``total == 0``（无条目）时置 ``1.0``
    表示 vacuous 通过；``passed`` 当且仅当 ``coverage_ratio >= threshold``。
    策略跳过的页面计入 ``total``、不计入 ``covered``。
    """

    model_config = ConfigDict(extra="ignore")

    total: int = 0
    covered: int = 0
    # covered/total；total==0 时 1.0（vacuous）；skipped 页计入 total 不计 covered
    coverage_ratio: float = 0.0
    threshold: float = 0.0
    passed: bool = False  # coverage_ratio >= threshold


class CoverageReport(BaseModel):
    """D2 遗留 PageScript 字段审计报告，落盘 ``coverage_report.json``（原型；非质量闸门）。

    三指标（必含信息覆盖率 / 因果链完整率 / 原文回溯率）各自承载一个
    ``CoverageMetric``；顶层保留兼容字段 ``threshold`` / ``below_threshold_pages``
    / ``overall_passed``。``overall_passed`` 在构造后由调用方按三项 ``passed`` 赋值。
    """

    model_config = ConfigDict(extra="ignore")

    required_coverage: CoverageMetric
    causal_coverage: CoverageMetric
    span_coverage: CoverageMetric
    threshold: float = 0.95  # 单值覆盖时代表统一阈值，否则为 span 阈值
    below_threshold_pages: list[str] = Field(default_factory=list)  # "c0001#chapter_N#p2"
    overall_passed: bool = False  # 三项均 passed
