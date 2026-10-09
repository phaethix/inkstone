"""core.comic.cas — content-addressed objects and manifests (phase 2).

A project gains two directories:

- ``cas/ab/ab12....bin`` — immutable bytes, named by their SHA-256
- ``index/<key>.json`` — one manifest per action key

``verify`` reads those directories directly. It must not call
``_reconcile_state``: that helper deletes missing files, and verify exists
to report them.

The generate path does not consult these keys yet. Letter and export are
the stages this store is for; render stays on the existing files.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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
) -> str:
    """SHA-256 of the declared inputs. ``inputs`` stay in the given order."""
    payload = {
        "schema": 2,
        "stage": stage,
        "stage_src": stage_src,
        "inputs": list(inputs),
        "params": params,
        "env": env,
    }
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


def stored_output(root: Path, key: str) -> bytes | None:
    """Return the first ``ok`` output, or ``None`` when the key is not a usable hit."""
    manifest = load_manifest(root, key)
    if manifest is None or manifest.outcome != "ok" or not manifest.outputs:
        return None
    path = object_path(root, manifest.outputs[0].content)
    if not path.is_file():
        return None
    return path.read_bytes()


def stored_files(root: Path, key: str) -> list[tuple[str, bytes]] | None:
    """Return every ``ok`` output, or ``None`` when one blob is missing."""
    manifest = load_manifest(root, key)
    if manifest is None or manifest.outcome != "ok" or not manifest.outputs:
        return None
    files: list[tuple[str, bytes]] = []
    for item in manifest.outputs:
        path = object_path(root, item.content)
        if not path.is_file():
            return None
        files.append((item.name, path.read_bytes()))
    return files


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


def verify(root: Path) -> list[str]:
    """Report disagreements between ``index/`` and ``cas/``.

    An empty index is consistent. A missing blob, a hash mismatch, a key that
    does not match its filename, or a non-ok outcome without a reason is not.
    """
    index = root / "index"
    if not index.is_dir():
        return []
    problems: list[str] = []
    for path in sorted(index.glob("*.json")):
        manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        expected = f"sha256:{path.stem}"
        if manifest.key != expected:
            problems.append(f"{path.name}: key is {manifest.key}")
        if manifest.outcome != "ok" and not (manifest.reason or "").strip():
            problems.append(f"{path.name}: outcome {manifest.outcome} has no reason")
        if manifest.outcome != "ok" and manifest.outputs:
            problems.append(f"{path.name}: outcome {manifest.outcome} still has outputs")
        for item in manifest.outputs:
            blob = object_path(root, item.content)
            if not blob.is_file():
                problems.append(f"{path.name}: missing {item.content}")
                continue
            actual = "sha256:" + _hex_digest(blob.read_bytes())
            if actual != item.content:
                problems.append(f"{path.name}: {item.content} does not match its bytes")
    return problems


def _atomic_write(dest: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, dest)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
