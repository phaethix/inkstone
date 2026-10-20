"""Manifest migration: canonicalize clean files, report unsafe ones."""

import json

from core.comic.cas import Manifest, save_manifest
from core.comic.migrate import apply_migrations, scan_manifests


def _write_legacy(root, stem: str = "a" * 64):
    index = root / "index"
    index.mkdir(parents=True)
    path = index / f"{stem}.json"
    path.write_text(
        json.dumps(
            {
                "key": f"sha256:{stem}",
                "stage": "render.page",
                "outcome": "ok",
                "outputs": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_migration_fills_current_manifest_fields(tmp_path):
    path = _write_legacy(tmp_path)
    report = scan_manifests(tmp_path)
    assert len(report.changed) == 1
    assert report.problems == []

    assert apply_migrations(report) == 1
    loaded = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded.schema_version == 2
    assert loaded.stage_src == ""
    assert loaded.env == ""


def test_migration_reports_a_key_filename_mismatch_without_rewriting(tmp_path):
    path = _write_legacy(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["key"] = "sha256:" + "b" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")

    report = scan_manifests(tmp_path)
    assert report.problems
    assert apply_migrations(report) == 0
    assert "filename" in report.problems[0].problems[0]


def test_migration_reports_malformed_json(tmp_path):
    index = tmp_path / "index"
    index.mkdir()
    (index / f"{'c' * 64}.json").write_text("{not json", encoding="utf-8")

    report = scan_manifests(tmp_path)
    assert report.problems
    assert apply_migrations(report) == 0


def test_migration_dry_run_is_idempotent(tmp_path):
    _write_legacy(tmp_path)
    first = scan_manifests(tmp_path)
    assert apply_migrations(first) == 1
    second = scan_manifests(tmp_path)
    assert second.changed == []


def test_migration_accepts_a_canonical_manifest(tmp_path):
    manifest = Manifest(
        key="sha256:" + "d" * 64,
        stage="render.page",
        outcome="ok",
        outputs=[],
    )
    save_manifest(tmp_path, manifest)

    report = scan_manifests(tmp_path)
    assert report.changed == []
    assert report.problems == []
