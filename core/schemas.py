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

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

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


class CharacterAsset(BaseModel):
    """A character extracted from the source text; reused across chunks by name."""

    model_config = ConfigDict(extra="ignore")

    name: str
    role: str = ""
    appearance: Appearance = Field(default_factory=Appearance)
    # English hardened description inlined into every panel prompt.
    l1_prompt: str = ""
    # t2i prompt for the character design sheet (portrait).
    portrait_prompt: str = ""
    # Runtime-only: local path of the generated portrait. Filled by the pipeline,
    # never requested from the model, so it is hidden from the tool schema.
    portrait_local: SkipJsonSchema[str | None] = None


class Setting(BaseModel):
    """A scene/location extracted from the source text."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    scene_prompt: str = ""


class StoryElements(BaseModel):
    """Return payload of the ``extract_story_elements`` forced function call."""

    model_config = ConfigDict(extra="ignore")

    characters: list[CharacterAsset] = Field(default_factory=list)
    settings: list[Setting] = Field(default_factory=list)
    style_guide: str = ""


class Panel(BaseModel):
    """A single comic panel within a storyboard."""

    model_config = ConfigDict(extra="ignore")

    panel_id: str
    characters_present: list[str] = Field(default_factory=list)
    setting_ref: str = ""
    action: str = ""
    dialogue: str | None = None
    # Built from CharacterAsset.l1_prompt + setting.scene_prompt + action.
    panel_prompt: str = ""
    reference_characters: list[str] = Field(default_factory=list)
    size: str = "1024x1024"


class Storyboard(BaseModel):
    """Return payload of the ``plan_storyboard`` forced function call (one chunk)."""

    model_config = ConfigDict(extra="ignore")

    chapter_id: str
    panels: list[Panel] = Field(default_factory=list)


class ModelSnapshot(BaseModel):
    """Model identifiers captured at project creation (for provenance / resume)."""

    model_config = ConfigDict(extra="ignore")

    chat: str = ""
    t2i: str = ""
    i2i: str = ""


class GeneratedPanel(BaseModel):
    """A generated panel's on-disk path plus optional source URL (24h expiry)."""

    model_config = ConfigDict(extra="ignore")

    local: str
    url: str | None = None
    expires_at: str | None = None


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
    created_at: str = Field(default_factory=_now_iso)
    model_snapshot: ModelSnapshot = Field(default_factory=ModelSnapshot)
    stage: Stage = "extract"
    characters: dict[str, CharacterAsset] = Field(default_factory=dict)
    chunks_done: list[str] = Field(default_factory=list)
    # Dedup key set for resume: already-generated panel_ids are skipped on rerun.
    panels_done: list[str] = Field(default_factory=list)
    # Panels rejected by the upstream content filter; recorded (not regenerated)
    # so a rerun stays honest about what was skipped instead of retrying blindly.
    skipped: list[str] = Field(default_factory=list)
    generated: GeneratedAssets = Field(default_factory=GeneratedAssets)
    errors: str = "logs/errors.jsonl"

    def save(self, path: str | Path) -> None:
        """Persist state as pretty JSON, creating parent dirs as needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")

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
