"""core.harness.spec — the editable prompt-harness components.

The first harness component moved out of Python is the system prompt: it is the
widest-reaching text in the pipeline and the one most likely to change during
prompt iteration. Genre packs are the domain layer: they add craft guidance to
planning without touching the invariant system rules.

This module stays dependency-free (stdlib only). It reads a manifest, validates
the component tree, and renders the pieces a builder asks for. Stage templates
still live beside their builders in ``core.screenwriter``; the manifest is the
single place a new component is registered.
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
GENRES = ("general", "wuxia")
STAGES = ("extract", "beats", "bible", "storyboard", "page_plan", "page_script")
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


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    """Read and shape-check ``data/manifest.json``."""
    raw = _read_json(DATA_DIR / MANIFEST_NAME)
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


def components() -> list[Component]:
    out = []
    for item in load_manifest()["components"]:
        out.append(
            Component(
                id=str(item["id"]),
                path=str(item["path"]),
                kind=str(item["kind"]),
                max_chars=int(item.get("max_chars", 8000)),
                slots=tuple(str(slot) for slot in item.get("slots", [])),
            )
        )
    return out


def component_path(component: Component) -> Path:
    path = (DATA_DIR / component.path).resolve()
    if DATA_DIR.resolve() not in path.parents:
        raise HarnessError(f"{component.id}: path escapes the harness data directory")
    return path


def read_component(component: Component) -> str:
    path = component_path(component)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HarnessError(f"{component.id}: component file missing: {component.path}") from exc


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """The invariant system prompt shared by every chat stage."""
    component = next((c for c in components() if c.id == "system"), None)
    if component is None:
        raise HarnessError("harness has no 'system' component")
    body = read_component(component).strip()
    if not body:
        raise HarnessError("the invariant system prompt is empty")
    return body


def available_genres() -> tuple[str, ...]:
    return tuple(str(g).strip().lower() for g in load_manifest()["genres"])


def genre_block(genre: str | None) -> str:
    """The domain notes for one genre, or ``""`` for the general profile.

    ``general`` is deliberately empty: a project that has not chosen a genre
    must produce exactly the prompt it produced before the domain layer existed.
    """
    name = (genre or "").strip().lower()
    if name in ("", "general"):
        return ""
    if name not in available_genres():
        raise HarnessError(f"unknown genre {name!r}; available: {', '.join(available_genres())}")
    component = next((c for c in components() if c.id == f"genre.{name}"), None)
    if component is None:
        raise HarnessError(f"genre {name!r} is listed but has no genre.{name} component")
    body = read_component(component).strip()
    if not body:
        raise HarnessError(f"genre {name!r} is empty")
    return f"\n\nDomain notes for this genre ({name}):\n{body}"


def _stage_component(stage: str) -> Component:
    component = next((c for c in components() if c.id == f"stage.{stage}"), None)
    if component is None:
        raise HarnessError(
            f"no stage template for {stage!r}; expected stages/{stage}.md in the manifest"
        )
    return component


def stage_template(stage: str) -> str:
    """The editable user-message template for one chat stage."""
    return read_component(_stage_component(stage)).strip()


def stage_slots(stage: str) -> tuple[str, ...]:
    """The slots a stage template uses, in first-appearance order."""
    return tuple(dict.fromkeys(_SLOT.findall(stage_template(stage))))


def render_stage(stage: str, values: dict[str, str]) -> str:
    """Render one stage template with named values.

    Slot substitution is deliberately dumb: ``{{name}}`` is replaced, nothing is
    evaluated. Unknown or missing slots are hard errors and list every problem,
    because a silently empty prompt is worse than a failed run.
    """
    template = stage_template(stage)
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
    rendered = _SLOT.sub(lambda match: str(values[match.group(1)]), template)
    return rendered.strip()


@lru_cache(maxsize=1)
def harness_digest() -> str:
    """A digest of the manifest and every component body.

    The digest describes the harness that produced an artifact. It is written
    to the review packet, not folded into every action key: the request digest
    already covers what a stage actually sent.
    """
    h = hashlib.sha256()
    h.update(json.dumps(load_manifest(), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    h.update(b"\0")
    for component in sorted(components(), key=lambda c: c.id):
        h.update(component.id.encode("utf-8"))
        h.update(b"\0")
        h.update(read_component(component).encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def validate() -> list[str]:
    """Return every harness problem in one pass; empty means valid."""
    problems: list[str] = []
    try:
        manifest = load_manifest()
        comps = components()
    except HarnessError as exc:
        return [str(exc)]

    genres = [str(g).strip().lower() for g in manifest.get("genres", [])]
    if len(set(genres)) != len(genres):
        problems.append("genres list contains duplicates")
    component_ids = {c.id for c in comps}
    for genre in genres:
        if f"genre.{genre}" not in component_ids:
            problems.append(f"genre {genre!r} is listed but has no genre.{genre} component")
    for cid in sorted(component_ids):
        if cid.startswith("genre."):
            name = cid.split(".", 1)[1]
            if name not in genres:
                problems.append(f"{cid} exists but {name!r} is not in the genres list")

    for component in comps:
        try:
            body = read_component(component)
        except HarnessError as exc:
            problems.append(str(exc))
            continue
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
    if not any(c.id == "system" for c in comps):
        problems.append("harness has no 'system' component")
    stage_ids = {c.id for c in comps}
    for stage in STAGES:
        if f"stage.{stage}" not in stage_ids:
            problems.append(f"harness has no stage.{stage} component")
    return problems


def validate_or_raise() -> None:
    problems = validate()
    if problems:
        raise HarnessError(" | ".join(problems))
