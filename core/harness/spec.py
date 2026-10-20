"""core.harness.spec — the editable prompt-harness components.

A harness is a directory tree: ``manifest.json``, ``common/system.md``, the six
``stages/*.md`` templates, and ``genres/*.md``. ``Harness`` loads, validates,
renders, and digests one tree; ``harness_from_dir`` loads any candidate
directory, which is what the experiment CLI uses for A/B probes.

The module-level helpers operate on the builtin tree (``data/``) for the normal
pipeline. Candidate directories never mutate that builtin tree.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST_NAME = "manifest.json"
COMPONENT_KINDS = frozenset({"invariant", "stage", "domain"})
STAGES = ("extract", "beats", "bible", "storyboard", "page_plan", "page_script")
GENRES = ("general", "wuxia")
_SLOT = re.compile(r"\{\{([a-z0-9_]+)\}\}")

# A prompt must describe the work, never its measurement. These markers are the
# Inkstone analogue of harness2's grader-verdict tripwire.
FORBIDDEN_MARKERS = (
    "structure_report",
    "acceptance rate",
    "quality score",
    "redraw rate",
    "gate.json",
)


class HarnessError(ValueError):
    """A harness component is missing, malformed, or contradicts an invariant."""


@dataclass(frozen=True)
class Component:
    id: str
    path: str
    kind: str
    max_chars: int
    slots: tuple[str, ...] = ()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HarnessError(f"harness manifest missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"harness manifest is not valid JSON: {exc}") from exc


def _shape_manifest(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise HarnessError("harness manifest must be a JSON object")
    if raw.get("schema") != 1:
        raise HarnessError(f"unsupported harness schema: {raw.get('schema')!r}")
    components = raw.get("components")
    if not isinstance(components, list) or not components:
        raise HarnessError("harness manifest needs a non-empty components list")
    seen: set[str] = set()
    for item in components:
        if not isinstance(item, dict):
            raise HarnessError("each harness component must be an object")
        cid = str(item.get("id", "")).strip()
        if not cid or cid in seen:
            raise HarnessError(f"component id is missing or duplicated: {cid!r}")
        seen.add(cid)
        kind = str(item.get("kind", ""))
        if kind not in COMPONENT_KINDS:
            raise HarnessError(f"{cid}: unknown component kind {kind!r}")
        if not str(item.get("path", "")).strip():
            raise HarnessError(f"{cid}: component path is empty")
        slots = item.get("slots", [])
        if not isinstance(slots, list) or not all(isinstance(slot, str) for slot in slots):
            raise HarnessError(f"{cid}: slots must be a list of strings")
    genres = raw.get("genres")
    if not isinstance(genres, list) or not genres:
        raise HarnessError("harness manifest needs a non-empty genres list")
    return raw


class Harness:
    """One harness tree: manifest, component bodies, validation, render, digest."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = _shape_manifest(_read_json(self.root / MANIFEST_NAME))
        self._components = [
            Component(
                id=str(item["id"]),
                path=str(item["path"]),
                kind=str(item["kind"]),
                max_chars=int(item.get("max_chars", 8000)),
                slots=tuple(str(slot) for slot in item.get("slots", [])),
            )
            for item in self.manifest["components"]
        ]
        self._bodies: dict[str, str] = {}
        for component in self._components:
            self._bodies[component.id] = self._read_component(component)

    @property
    def components(self) -> list[Component]:
        return list(self._components)

    def component_path(self, component: Component) -> Path:
        path = (self.root / component.path).resolve()
        if self.root not in path.parents:
            raise HarnessError(f"{component.id}: path escapes the harness directory")
        return path

    def _read_component(self, component: Component) -> str:
        path = self.component_path(component)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HarnessError(f"{component.id}: component file missing: {component.path}") from exc

    @property
    def available_genres(self) -> tuple[str, ...]:
        return tuple(str(genre).strip().lower() for genre in self.manifest["genres"])

    def system_prompt(self) -> str:
        component = next((c for c in self._components if c.id == "system"), None)
        if component is None:
            raise HarnessError("harness has no 'system' component")
        body = self._bodies[component.id].strip()
        if not body:
            raise HarnessError("the invariant system prompt is empty")
        return body

    def genre_block(self, genre: str | None) -> str:
        """Domain notes, or ``""`` for the general profile."""
        name = (genre or "").strip().lower()
        if name in ("", "general"):
            return ""
        if name not in self.available_genres:
            raise HarnessError(
                f"unknown genre {name!r}; available: {', '.join(self.available_genres)}"
            )
        component = next((c for c in self._components if c.id == f"genre.{name}"), None)
        if component is None:
            raise HarnessError(f"genre {name!r} is listed but has no genre.{name} component")
        body = self._bodies[component.id].strip()
        if not body:
            raise HarnessError(f"genre {name!r} is empty")
        return f"\n\nDomain notes for this genre ({name}):\n{body}"

    def _stage_component(self, stage: str) -> Component:
        component = next((c for c in self._components if c.id == f"stage.{stage}"), None)
        if component is None:
            raise HarnessError(
                f"no stage template for {stage!r}; expected stages/{stage}.md in the manifest"
            )
        return component

    def stage_template(self, stage: str) -> str:
        return self._bodies[self._stage_component(stage).id].strip()

    def stage_slots(self, stage: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_SLOT.findall(self.stage_template(stage))))

    def render_stage(self, stage: str, values: dict[str, str]) -> str:
        """Substitute named ``{{slots}}``; unknown or missing slots are errors."""
        template = self.stage_template(stage)
        used = list(dict.fromkeys(_SLOT.findall(template)))
        missing = [slot for slot in used if slot not in values]
        unused = sorted(set(values) - set(used))
        if missing or unused:
            problems = []
            if missing:
                problems.append("missing values: " + ", ".join(missing))
            if unused:
                problems.append("unused values: " + ", ".join(unused))
            raise HarnessError(f"{stage}: template slot mismatch ({'; '.join(problems)})")
        return _SLOT.sub(lambda match: str(values[match.group(1)]), template).strip()

    def digest(self) -> str:
        """A digest of the manifest and every component body."""
        h = hashlib.sha256()
        h.update(json.dumps(self.manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        h.update(b"\0")
        for component in sorted(self._components, key=lambda c: c.id):
            h.update(component.id.encode("utf-8"))
            h.update(b"\0")
            h.update(self._bodies[component.id].encode("utf-8"))
            h.update(b"\0")
        return "sha256:" + h.hexdigest()

    def validate(self) -> list[str]:
        """Return every harness problem in one pass; empty means valid."""
        problems: list[str] = []
        genres = [str(g).strip().lower() for g in self.manifest.get("genres", [])]
        if len(set(genres)) != len(genres):
            problems.append("genres list contains duplicates")
        component_ids = {component.id for component in self._components}
        for genre in genres:
            if f"genre.{genre}" not in component_ids:
                problems.append(f"genre {genre!r} is listed but has no genre.{genre} component")
        for cid in sorted(component_ids):
            if cid.startswith("genre."):
                name = cid.split(".", 1)[1]
                if name not in genres:
                    problems.append(f"{cid} exists but {name!r} is not in the genres list")
        for component in self._components:
            body = self._bodies[component.id]
            if not body.strip():
                problems.append(f"{component.id}: component is empty")
            if len(body) > component.max_chars:
                problems.append(
                    f"{component.id}: {len(body)} chars exceeds the {component.max_chars} cap"
                )
            low = body.lower()
            for marker in FORBIDDEN_MARKERS:
                if marker in low:
                    problems.append(
                        f"{component.id}: contains measurement marker {marker!r}; "
                        "a prompt describes the work, not its measurement"
                    )
            if component.id.startswith("stage."):
                used = tuple(dict.fromkeys(_SLOT.findall(body)))
                if used != component.slots:
                    problems.append(
                        f"{component.id}: manifest slots {list(component.slots)!r} "
                        f"do not match template slots {list(used)!r}"
                    )
            elif component.slots:
                problems.append(f"{component.id}: only stage components may declare slots")
        if "system" not in component_ids:
            problems.append("harness has no 'system' component")
        for stage in STAGES:
            if f"stage.{stage}" not in component_ids:
                problems.append(f"harness has no stage.{stage} component")
        return problems


_HARNESSES: dict[str, Harness] = {}


def harness_from_dir(root: str | Path) -> Harness:
    """Load (and cache) a harness tree from any directory."""
    key = str(Path(root).resolve())
    harness = _HARNESSES.get(key)
    if harness is None:
        harness = Harness(key)
        _HARNESSES[key] = harness
    return harness


def builtin_harness() -> Harness:
    return harness_from_dir(DATA_DIR)


def clear_harness_cache() -> None:
    _HARNESSES.clear()


# --- builtin wrappers (kept for the existing pipeline and tests) ------------


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    return builtin_harness().manifest


def components() -> list[Component]:
    return builtin_harness().components


def available_genres() -> tuple[str, ...]:
    return builtin_harness().available_genres


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return builtin_harness().system_prompt()


def genre_block(genre: str | None) -> str:
    return builtin_harness().genre_block(genre)


def stage_template(stage: str) -> str:
    return builtin_harness().stage_template(stage)


def stage_slots(stage: str) -> tuple[str, ...]:
    return builtin_harness().stage_slots(stage)


def render_stage(stage: str, values: dict[str, str]) -> str:
    return builtin_harness().render_stage(stage, values)


@lru_cache(maxsize=1)
def harness_digest() -> str:
    return builtin_harness().digest()


def validate() -> list[str]:
    return builtin_harness().validate()


def validate_or_raise() -> None:
    problems = validate()
    if problems:
        raise HarnessError(" | ".join(problems))
