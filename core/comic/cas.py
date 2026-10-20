"""core.comic.cas — content-addressed objects and manifests (phase 2).

A project gains two directories:

- ``cas/ab/ab12....bin`` — immutable bytes, named by their SHA-256
- ``index/<key>.json`` — one manifest per action key

``verify`` reads those directories directly. It must not call
``_reconcile_state``: that helper deletes missing files, and verify exists
to report them.

Letter, layout, and export consult these keys. A font change re-letters
from the stored blank. A new manifest records when it was written.
Render stays on the existing files.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# A recorded outcome is a hit. New writers produce only ``ok`` / ``rejected``;
# ``stopped`` / ``awaiting_human`` remain readable for old files but are now
# runtime pause state, not manifests.
HIT_OUTCOMES = frozenset({"ok", "rejected", "stopped", "awaiting_human"})


class ManifestOutput(BaseModel):
    """One named blob a manifest claims to have written."""

    model_config = ConfigDict(extra="ignore")

    name: str
    content: str


class ManifestCost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calls: int = 0


class Manifest(BaseModel):
    """Schema 2 manifest. ``key`` matches the index filename."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: int = Field(default=2, alias="schema")
    key: str
    stage: str
    outcome: str = "ok"
    reason: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[ManifestOutput] = Field(default_factory=list)
    stage_src: str = ""
    env: str = ""
    created_at: str = ""
    supersedes: list[str] = Field(default_factory=list)
    cost: ManifestCost = Field(default_factory=ManifestCost)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bare(content_id: str) -> str:
    return content_id.removeprefix("sha256:")


def action_key(
    *,
    stage: str,
    stage_src: str,
    inputs: list[str],
    params: dict,
    env: str,
    request_digest: str | None = None,
) -> str:
    """SHA-256 of the declared inputs. ``inputs`` stay in the given order.

    ``request_digest`` is the digest of the exact chat request a stage sent.
    A stage that supplies it uses schema 3; a stage that does not keeps the
    schema-2 payload, so existing render/letter/export keys do not move.
    """
    payload = {
        "schema": 2 if request_digest is None else 3,
        "stage": stage,
        "stage_src": stage_src,
        "inputs": list(inputs),
        "params": params,
        "env": env,
    }
    if request_digest is not None:
        payload["request_digest"] = request_digest
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + _hex_digest(canonical.encode("utf-8"))


def letter_inputs(blank: bytes, plan_json: str) -> list[str]:
    """Ordered inputs for a letter key: blank bytes, then the plan text."""
    return [
        "sha256:" + _hex_digest(blank),
        "sha256:" + _hex_digest(plan_json.encode("utf-8")),
    ]


def export_inputs(pages: list[bytes]) -> list[str]:
    """Ordered content ids of the page images an export will bind."""
    return ["sha256:" + _hex_digest(page) for page in pages]


def export_action_key(
    *,
    pages: list[bytes],
    layout: str,
    direction: str,
    env: str,
    stage_src: str,
) -> str:
    """Key an export from the page bytes plus the binding layout.

    The page images already contain lettering, so this key does not take
    ``page_size`` or the other render-only knobs.
    """
    return action_key(
        stage="export",
        stage_src=stage_src,
        inputs=export_inputs(pages),
        params={"layout": layout, "direction": direction},
        env=env,
    )


def webtoon_action_key(
    *,
    pages: list[bytes],
    lettering: list[dict[str, str]],
    page_width: int,
    env: str,
    stage_src: str,
) -> str:
    """Key a webtoon strip from the panel bytes and the text drawn on them.

    ``INKSTONE_WEBTOON_MAX_PIXELS`` stays out: it only refuses an oversized
    canvas. A strip that was produced does not change with that limit.
    """
    return action_key(
        stage="export",
        stage_src=stage_src,
        inputs=export_inputs(pages),
        params={"layout": "webtoon", "lettering": lettering, "page_width": page_width},
        env=env,
    )


def layout_action_key(
    *,
    pages: list[bytes],
    lettering: list[dict[str, str]],
    page_width: int,
    cell_height: int,
    bg: list[int],
    env: str,
    stage_src: str,
) -> str:
    """Key a page collage from the panel bytes and the text drawn on them."""
    return action_key(
        stage="layout",
        stage_src=stage_src,
        inputs=export_inputs(pages),
        params={
            "bg": bg,
            "cell_height": cell_height,
            "layout": "page",
            "lettering": lettering,
            "page_width": page_width,
        },
        env=env,
    )


def extract_inputs(chunk: str) -> list[str]:
    """Ordered inputs for an extract manifest: the chunk text digest."""
    return ["sha256:" + _hex_digest(chunk.encode("utf-8"))]


def extract_action_key(
    *,
    chunk: str,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key an extract from the chunk text and the exact request.

    ``env`` is the chat model identity. Page size, continuity, and L3 stay out,
    so those knobs cannot make a stored extract miss.
    """
    return action_key(
        stage="extract",
        stage_src=stage_src,
        inputs=extract_inputs(chunk),
        params={},
        env=env,
        request_digest=request_digest,
    )


def render_page_inputs(plan_json: str, prompt: str) -> list[str]:
    """Ordered inputs for a finished page: the plan, then the prompt that was sent."""
    return [
        "sha256:" + _hex_digest(plan_json.encode("utf-8")),
        "sha256:" + _hex_digest(prompt.encode("utf-8")),
    ]


def render_page_action_key(
    *,
    plan_json: str,
    prompt: str,
    resolved_size: str,
    page_key: str,
    ref_policy: str,
    env: str,
    stage_src: str,
) -> str:
    """Key a finished page from its plan, prompt, identity, and ref policy.

    The L2 reference image itself is a soft reference and is not an input; the
    *selection policy* is (``ref_policy``, see
    :func:`core.comic.visual_bible.finished_page_ref_policy`). Without it, a
    reorder of the priority walk or a change to ``max_refs`` would hand a stale
    page back as a hit while the references actually sent had changed. Two
    chunks that happen to plan the same words still render two pages. A second
    run of the same page reuses the stored blank.
    """
    return action_key(
        stage="render.page",
        stage_src=stage_src,
        inputs=render_page_inputs(plan_json, prompt),
        params={
            "page_key": page_key,
            "resolved_size": resolved_size,
            "ref_policy": ref_policy,
        },
        env=env,
    )


def beats_inputs(chunk: str, extract_key: str) -> list[str]:
    """Ordered inputs for a beats manifest: the chunk digest, then the extract key."""
    return [
        "sha256:" + _hex_digest(chunk.encode("utf-8")),
        extract_key,
    ]


def beats_action_key(
    *,
    chunk: str,
    extract_key: str,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key a beat extract from the chunk and the extract that read it.

    A changed extract key replans the beats. Render-only knobs stay out of ``env``.
    """
    return action_key(
        stage="beats",
        stage_src=stage_src,
        inputs=beats_inputs(chunk, extract_key),
        params={},
        env=env,
        request_digest=request_digest,
    )


def page_plan_inputs(chunk: str, beats_json: str, beats_key: str) -> list[str]:
    """Ordered inputs for a page-plan manifest: chunk, beats, upstream key."""
    return [
        "sha256:" + _hex_digest(chunk.encode("utf-8")),
        "sha256:" + _hex_digest(beats_json.encode("utf-8")),
        beats_key,
    ]


def page_plan_action_key(
    *,
    chunk: str,
    beats_json: str,
    beats_key: str,
    history: dict,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key a page plan from the chunk, its beats, and the bounded history.

    ``beats_key`` is the upstream beats action key. A changed beats key replans
    the pages. ``history`` is the window size plus summarizer source hashes.
    The recent layout strings themselves stay out, so one earlier page cannot
    re-plan the rest of the book.
    """
    return action_key(
        stage="page_plan",
        stage_src=stage_src,
        inputs=page_plan_inputs(chunk, beats_json, beats_key),
        params={"history": history},
        env=env,
        request_digest=request_digest,
    )


def portrait_action_key(*, name: str, material: str, env: str, stage_src: str) -> str:
    """Key one portrait from that character's own prompt.

    The earlier portrait file is a soft reference and is not an input.
    """
    return action_key(
        stage="portrait",
        stage_src=stage_src,
        inputs=["sha256:" + _hex_digest(material.encode("utf-8"))],
        params={"name": name},
        env=env,
    )


def render_panel_action_key(
    *,
    panel_json: str,
    style: str,
    page_key: str,
    env: str,
    stage_src: str,
) -> str:
    """Key one storyboard panel from its own plan and style.

    The previous panel and the portrait file are soft references and are not
    inputs. The same panel on a later run reuses the stored image.
    """
    return action_key(
        stage="render.panel",
        stage_src=stage_src,
        inputs=["sha256:" + _hex_digest(panel_json.encode("utf-8"))],
        params={"page_key": page_key, "style": style},
        env=env,
    )


def bible_inputs(chunk: str) -> list[str]:
    """Ordered inputs for a bible manifest: the chunk text digest."""
    return ["sha256:" + _hex_digest(chunk.encode("utf-8"))]


def bible_action_key(
    *,
    chunk: str,
    history: dict,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key a bible reconcile from the chunk and the bounded summary.

    ``history`` is the window size plus summarizer source hashes. The bible
    prose and the character table stay out: a recorded merge must not make
    every chunk pay for another reconcile. A new character still reconciles
    through the existing new-name check.
    """
    return action_key(
        stage="bible",
        stage_src=stage_src,
        inputs=bible_inputs(chunk),
        params={"history": history},
        env=env,
        request_digest=request_digest,
    )


def storyboard_inputs(chunk: str, cast: str) -> list[str]:
    """Ordered inputs for a storyboard manifest: chunk digest, then cast digest."""
    return [
        "sha256:" + _hex_digest(chunk.encode("utf-8")),
        "sha256:" + _hex_digest(cast.encode("utf-8")),
    ]


def storyboard_action_key(
    *,
    chunk: str,
    cast: str,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key a storyboard from the chunk and the names in that chunk.

    Earlier panels and the portrait files stay out. A repeat of the same chunk
    does not call the planner again.
    """
    return action_key(
        stage="storyboard",
        stage_src=stage_src,
        inputs=storyboard_inputs(chunk, cast),
        params={},
        env=env,
        request_digest=request_digest,
    )


def page_script_inputs(chunk: str, storyboard_json: str, elements_json: str) -> list[str]:
    """Ordered inputs for a page-script manifest: chunk, storyboard, elements."""
    return [
        "sha256:" + _hex_digest(chunk.encode("utf-8")),
        "sha256:" + _hex_digest(storyboard_json.encode("utf-8")),
        "sha256:" + _hex_digest(elements_json.encode("utf-8")),
    ]


def page_script_action_key(
    *,
    chunk: str,
    storyboard_json: str,
    elements_json: str,
    env: str,
    stage_src: str,
    request_digest: str | None = None,
) -> str:
    """Key a page script from this chunk's text, storyboard, and elements.

    ``env`` is the chat model identity. A portrait file and the page size stay
    out. The same script on a later run does not call the planner again.
    """
    return action_key(
        stage="page_script",
        stage_src=stage_src,
        inputs=page_script_inputs(chunk, storyboard_json, elements_json),
        params={},
        env=env,
        request_digest=request_digest,
    )


def letter_action_key(*, blank: bytes, plan_json: str, env: str, stage_src: str) -> str:
    """Key a lettering step from the blank bytes, the plan, and ``h_env``.

    Render-only knobs are absent: lettering reads the blank's pixels, not the
    requested page size.
    """
    return action_key(
        stage="letter",
        stage_src=stage_src,
        inputs=letter_inputs(blank, plan_json),
        params={},
        env=env,
    )


def stored_output(root: Path, key: str, *, stage: str | None = None) -> bytes | None:
    """Return the first ``ok`` output, or ``None`` when the key is not a usable hit."""
    resolved = verified_manifest_files(root, key, stage=stage)
    if resolved is None:
        return None
    _, files = resolved
    return files[0][1]


def stored_files(
    root: Path, key: str, *, stage: str | None = None
) -> list[tuple[str, bytes]] | None:
    """Return every ``ok`` output, or ``None`` when one blob is missing."""
    resolved = verified_manifest_files(root, key, stage=stage)
    if resolved is None:
        return None
    return resolved[1]


def read_manifest_entry(root: Path, key: str, *, stage: str | None = None) -> Manifest | None:
    """Read a well-formed entry whose payload and index filename identify ``key``."""
    path = manifest_path(root, key)
    if path.stem != _bare(key) or not path.is_file():
        return None
    try:
        manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not _manifest_identity_valid(manifest, key, path.stem, stage):
        return None
    return manifest


def _manifest_identity_valid(
    manifest: Manifest, key: str, index_stem: str, stage: str | None = None
) -> bool:
    return (
        manifest.key == key
        and index_stem == _bare(key)
        and (stage is None or manifest.stage == stage)
    )


def _read_verified_object(root: Path, content_id: str) -> bytes | None:
    """Read a CAS object only when its canonical id matches the bytes."""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", content_id) is None:
        return None
    path = object_path(root, content_id)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if "sha256:" + _hex_digest(data) != content_id:
        return None
    return data


def verified_manifest_files(
    root: Path, key: str, *, stage: str | None = None
) -> tuple[Manifest, list[tuple[str, bytes]]] | None:
    """Resolve an ``ok`` manifest only when every referenced CAS object verifies."""
    manifest = read_manifest_entry(root, key, stage=stage)
    if manifest is None or manifest.outcome != "ok" or not manifest.outputs:
        return None
    files: list[tuple[str, bytes]] = []
    for item in manifest.outputs:
        data = _read_verified_object(root, item.content)
        if data is None:
            return None
        files.append((item.name, data))
    return manifest, files


def record_files(
    root: Path,
    *,
    key: str,
    stage: str,
    stage_src: str,
    env: str,
    inputs: list[str],
    files: list[tuple[str, bytes]],
) -> None:
    """Store each file and write one ``ok`` manifest whose outputs keep that order."""
    outputs = [ManifestOutput(name=name, content=put_bytes(root, data)) for name, data in files]
    save_manifest(
        root,
        Manifest(
            key=key,
            stage=stage,
            outcome="ok",
            inputs=list(inputs),
            outputs=outputs,
            stage_src=stage_src,
            env=env,
            created_at=_now_iso(),
        ),
    )


def record_ok(
    root: Path,
    *,
    key: str,
    stage: str,
    stage_src: str,
    env: str,
    inputs: list[str],
    name: str,
    data: bytes,
) -> None:
    """Store ``data`` and write an ``ok`` manifest at ``key``."""
    content = put_bytes(root, data)
    save_manifest(
        root,
        Manifest(
            key=key,
            stage=stage,
            outcome="ok",
            inputs=list(inputs),
            outputs=[ManifestOutput(name=name, content=content)],
            stage_src=stage_src,
            env=env,
            created_at=_now_iso(),
        ),
    )


def object_path(root: Path, content_id: str) -> Path:
    digest = _bare(content_id)
    return root / "cas" / digest[:2] / f"{digest}.bin"


def put_bytes(root: Path, data: bytes) -> str:
    """Store ``data`` once. A second put of the same bytes leaves the file."""
    digest = _hex_digest(data)
    dest = object_path(root, digest)
    content_id = f"sha256:{digest}"
    if dest.is_file():
        return content_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, data)
    return content_id


def read_bytes(root: Path, content_id: str) -> bytes:
    return object_path(root, content_id).read_bytes()


def manifest_path(root: Path, key: str) -> Path:
    return root / "index" / f"{_bare(key)}.json"


def save_manifest(root: Path, manifest: Manifest) -> None:
    """Write the manifest atomically. The filename is the key."""
    path = manifest_path(root, manifest.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump_json(by_alias=True, indent=2)
    _atomic_write(path, payload.encode("utf-8"))


def load_manifest(root: Path, key: str) -> Manifest | None:
    path = manifest_path(root, key)
    if not path.is_file():
        return None
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def rewrite_manifest(
    root: Path,
    key: str,
    *,
    outcome: str,
    reason: str | None = None,
    outputs: list[ManifestOutput] | None = None,
) -> Manifest:
    """Replace the manifest at ``key`` without minting a new key.

    Previous output hashes are appended to ``supersedes``.
    """
    current = load_manifest(root, key)
    if current is None:
        raise KeyError(f"no manifest for {key}")
    if current.key != key:
        raise ValueError(f"manifest key {current.key} does not match {key}")
    previous = [item.content for item in current.outputs]
    updated = current.model_copy(
        update={
            "outcome": outcome,
            "reason": reason,
            "outputs": current.outputs if outputs is None else outputs,
            "supersedes": list(current.supersedes) + previous,
        }
    )
    if updated.key != key:
        raise RuntimeError("rewrite changed the key")
    save_manifest(root, updated)
    return updated


def is_hit(manifest: Manifest) -> bool:
    """Every recorded outcome is a hit. Only ``ok`` is usable bytes."""
    return manifest.outcome in HIT_OUTCOMES


def release_tombstones(root: Path, *, stage: str, keys: list[str] | None = None) -> list[str]:
    """Delete non-ok manifests for ``stage`` so the next run re-attempts them.

    ``keys`` narrows the set. An ``ok`` manifest stays, and the bytes under
    ``cas/`` stay with it: a tombstone's previous outputs are still an audit
    trail. An empty index releases nothing.
    """
    index = root / "index"
    if not index.is_dir():
        return []
    wanted = set(keys) if keys else None
    released: list[str] = []
    for path in sorted(index.glob("*.json")):
        manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.stage != stage or manifest.outcome == "ok":
            continue
        if wanted is not None and manifest.key not in wanted:
            continue
        path.unlink()
        released.append(manifest.key)
    return released


def verify(
    root: Path,
    *,
    allowed_stages: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Report disagreements between ``index/`` and ``cas/``.

    An empty index is consistent. A missing blob, a hash mismatch, a key that
    does not match its filename, a non-ok outcome without a reason, or a stage
    outside ``allowed_stages`` is not. The last argument is optional so the
    storage layer does not import the pipeline catalog.
    """
    index = root / "index"
    if not index.is_dir():
        return []
    problems: list[str] = []
    for path in sorted(index.glob("*.json")):
        try:
            manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append(f"{path.name}: malformed manifest")
            continue
        expected = f"sha256:{path.stem}"
        if not _manifest_identity_valid(manifest, expected, path.stem):
            problems.append(f"{path.name}: key is {manifest.key}")
        if allowed_stages is not None and manifest.stage not in allowed_stages:
            problems.append(f"{path.name}: unknown stage {manifest.stage!r}")
        if manifest.outcome != "ok" and not (manifest.reason or "").strip():
            problems.append(f"{path.name}: outcome {manifest.outcome} has no reason")
        if manifest.outcome != "ok" and manifest.outputs:
            problems.append(f"{path.name}: outcome {manifest.outcome} still has outputs")
        for item in manifest.outputs:
            if re.fullmatch(r"sha256:[0-9a-f]{64}", item.content) is None:
                problems.append(f"{path.name}: invalid content id {item.content}")
                continue
            data = _read_verified_object(root, item.content)
            if data is None:
                blob = object_path(root, item.content)
                if not blob.is_file():
                    problems.append(f"{path.name}: missing {item.content}")
                else:
                    problems.append(f"{path.name}: {item.content} does not match its bytes")
                continue
    return problems


def _atomic_write(dest: Path, data: bytes) -> None:
    from core.comic.atomic import atomic_write_bytes

    atomic_write_bytes(dest, data)
