"""core.comic.migrate — canonicalize manifests across schema versions.

Manifests outlive the code that wrote them. This module is the one-shot
upgrader: it reads every ``index/*.json`` under a project, fills the fields
current code expects, and reports anything that cannot be repaired safely
(a key/filename mismatch, malformed JSON, or a non-ok outcome without a reason).

The upgrade is intentionally conservative: unknown fields are ignored by the
``Manifest`` model and dropped on rewrite, while a key mismatch is never
"fixed" in place because the filename is the lookup identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.comic.cas import HIT_OUTCOMES, Manifest, manifest_path, save_manifest


@dataclass
class ManifestMigration:
    path: Path
    key: str
    changed: bool = False
    problems: list[str] = field(default_factory=list)


@dataclass
class MigrationReport:
    manifests: list[ManifestMigration] = field(default_factory=list)

    @property
    def changed(self) -> list[ManifestMigration]:
        return [item for item in self.manifests if item.changed]

    @property
    def problems(self) -> list[ManifestMigration]:
        return [item for item in self.manifests if item.problems]


def _canonical(raw: dict) -> tuple[Manifest, bool]:
    """Return a current manifest and whether the serialized form changes."""
    before = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    manifest = Manifest.model_validate(raw)
    after = json.dumps(
        manifest.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return manifest, before != after


def scan_manifests(root: str | Path) -> MigrationReport:
    """Read ``index/*.json`` and report what a canonical rewrite would change."""
    root = Path(root)
    report = MigrationReport()
    index = root / "index"
    if not index.is_dir():
        return report
    for path in sorted(index.glob("*.json")):
        expected = f"sha256:{path.stem}"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.manifests.append(
                ManifestMigration(path=path, key=expected, problems=[f"malformed manifest: {exc}"])
            )
            continue
        try:
            manifest, changed = _canonical(raw)
        except (ValueError, TypeError) as exc:
            report.manifests.append(
                ManifestMigration(path=path, key=expected, problems=[f"invalid manifest: {exc}"])
            )
            continue
        problems: list[str] = []
        if manifest.key != expected:
            problems.append(f"key {manifest.key} does not match filename {path.name}")
        if manifest.outcome not in HIT_OUTCOMES:
            problems.append(f"unknown outcome {manifest.outcome!r}")
        if manifest.outcome != "ok" and not (manifest.reason or "").strip():
            problems.append(f"outcome {manifest.outcome} has no reason")
        report.manifests.append(
            ManifestMigration(path=path, key=manifest.key, changed=changed, problems=problems)
        )
    return report


def apply_migrations(report: MigrationReport) -> int:
    """Rewrite the clean, changed manifests. Returns the number written."""
    written = 0
    for item in report.manifests:
        if not item.changed or item.problems:
            continue
        raw = json.loads(item.path.read_text(encoding="utf-8"))
        manifest, _ = _canonical(raw)
        save_manifest(item.path.parent.parent, manifest)
        written += 1
    return written


def manifest_path_for(root: str | Path, key: str) -> Path:
    """Public helper for callers that need the canonical filename."""
    return manifest_path(Path(root), key)
