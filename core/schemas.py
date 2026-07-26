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
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema


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

# Six resumable pipeline stages.
Stage = Literal["extract", "storyboard", "portraits", "panels", "layout", "export"]


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
        """
        value = coerce_jsonish(value)
        if not isinstance(value, dict):
            return value
        name = value.get("name", None)
        if name is not None and coerce_str(name).strip():
            return value
        for key in ("character_name", "character", "label"):
            alt = value.get(key)
            if alt is not None and coerce_str(alt).strip():
                return {**value, "name": coerce_str(alt).strip()}
        role_text = coerce_str(value.get("role")).strip()
        if role_text:
            # Prefer the first comma-segment so "Antagonist, ETO Enforcer" → "Antagonist".
            stand_in = role_text.split(",", 1)[0].strip() or role_text
            return {**value, "name": stand_in}
        return {**value, "name": "unnamed"}

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

    @field_validator("characters", "settings", mode="before")
    @classmethod
    def _coerce_asset_lists(cls, value: Any) -> Any:
        return coerce_object_list(value)

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
            "speech-bubble / narration text shown to readers; MUST use the same "
            "language as the source novel excerpt (Chinese source → Chinese "
            "dialogue). Prefer short quotes close to the source; do not translate."
        ),
    )
    # Pipeline-owned prompt text. Hidden from the tool schema so the model does
    # not invent a competing prompt; ``ConsistencyEngine.build_panel_prompt`` is
    # the sole authority at render time. Kept for legacy state.json compatibility.
    panel_prompt: SkipJsonSchema[str] = ""
    reference_characters: list[str] = Field(default_factory=list)
    size: str = "1024x1024"

    @field_validator(
        "panel_id",
        "setting_ref",
        "action",
        "panel_prompt",
        mode="before",
    )
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> Any:
        return coerce_str(value)

    @field_validator("size", mode="before")
    @classmethod
    def _coerce_size(cls, value: Any) -> Any:
        return coerce_size(value)

    @field_validator("characters_present", "reference_characters", mode="before")
    @classmethod
    def _coerce_name_lists(cls, value: Any) -> Any:
        return coerce_str_list(value)

    @field_validator("dialogue", mode="before")
    @classmethod
    def _coerce_dialogue(cls, value: Any) -> Any:
        return coerce_dialogue(value)


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
        return coerce_object_list(value)


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
    """一页分镜的信息完备闸门内容。"""

    model_config = ConfigDict(extra="ignore")

    page_index: int = 0
    required_information: str = ""  # 读完该页须保留的关键信息
    causal_links: list[CausalLink] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    panel_ids: list[str] = Field(default_factory=list)  # 引用 Storyboard.panel_id


class PageScript(BaseModel):
    """一个 chunk 的信息完备分镜产物（storyboard 之后生成）。"""

    model_config = ConfigDict(extra="ignore")

    chapter_id: str = ""
    pages: list[PageScriptPage] = Field(default_factory=list)
    # 内容审核拒绝页；coverage 仍计入分母，视为未覆盖。
    skipped_pages: list[int] = Field(default_factory=list)


class ChunkCache(BaseModel):
    """Per-chunk cache of the billable chat-API results.

    ``extract_story_elements`` and ``plan_storyboard`` are the only network/cost
    calls in the pipeline; caching their products per chunk lets a resume reuse
    them instead of re-paying for already-planned chunks. Either field may be
    ``None`` while a chunk is mid-flight (e.g. extraction cached but storyboard
    still pending or rejected), in which case only the missing step is re-run.
    """

    model_config = ConfigDict(extra="ignore")

    elements: StoryElements | None = None
    storyboard: Storyboard | None = None
    # D2 信息完备分镜产物；resume 跳过已生成块（不重复计费）。
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


class GeneratedPanel(BaseModel):
    """A generated panel and the immutable storyboard position that produced it."""

    model_config = ConfigDict(extra="ignore")

    local: str
    chunk_index: int = 0
    panel_index: int = 0
    source_panel_id: str = ""
    dialogue: str | None = None
    url: str | None = None
    expires_at: str | None = None

    @field_validator("dialogue", mode="before")
    @classmethod
    def _coerce_dialogue(cls, value: Any) -> Any:
        return coerce_dialogue(value)


class GeneratedAssets(BaseModel):
    """All generated artifacts: character portraits and per-panel images."""

    model_config = ConfigDict(extra="ignore")

    portraits: dict[str, str] = Field(default_factory=dict)
    panels: dict[str, GeneratedPanel] = Field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class ProjectState(BaseModel):
    """Resumable project state persisted to ``state.json`` between stages."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    source_file: str = ""
    source_fingerprint: str = ""
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
    generated: GeneratedAssets = Field(default_factory=GeneratedAssets)
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
    """单指标的量化闸门（required/causal/span 复用此结构）。

    ``coverage_ratio = covered / total``；当 ``total == 0``（无条目）时置 ``1.0``
    表示 vacuous 通过；``passed`` 当且仅当 ``coverage_ratio >= threshold``。
    策略跳过的页面计入 ``total``、不计入 ``covered``。
    """

    model_config = ConfigDict(extra="ignore")

    total: int = 0
    covered: int = 0
    coverage_ratio: float = 0.0  # covered/total；total==0 时 1.0（vacuous）；skipped 页计入 total 不计 covered
    threshold: float = 0.0
    passed: bool = False  # coverage_ratio >= threshold


class CoverageReport(BaseModel):
    """D2 三指标覆盖率报告，落盘 ``coverage_report.json``。

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
