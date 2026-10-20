"""The declarative stage catalog stays in sync with the contract table."""

from core.cli import _build_parser, _run_stages
from core.comic.stage_contract import _STAGES
from core.pipelines.stage_registry import (
    all_specs,
    stage_contract_for,
    stage_spec,
    validate_stage_registry,
)


def test_every_contract_has_a_stage_spec():
    assert validate_stage_registry() == []
    assert {spec.contract_stage for spec in all_specs()} == set(_STAGES)
    assert {spec.name for spec in all_specs()} >= set(_STAGES)


def test_webtoon_reuses_the_export_contract_and_manifest_stage():
    spec = stage_spec("webtoon")
    assert spec.contract_stage == "export"
    assert spec.manifest_stage == "export"
    assert stage_contract_for("webtoon").stage == "export"


def test_stages_cli_lists_the_catalog(capsys):
    args = _build_parser().parse_args(["stages"])
    assert _run_stages(args) == 0
    out = capsys.readouterr().out
    assert "render.page" in out
    assert "manifest" in out


def test_verify_and_migrate_reject_unknown_manifest_stages(tmp_path):
    from core.comic.cas import Manifest, save_manifest, verify
    from core.comic.migrate import scan_manifests
    from core.pipelines.stage_registry import manifest_stages

    save_manifest(
        tmp_path,
        Manifest(key="sha256:" + "a" * 64, stage="not-a-stage", outcome="ok"),
    )

    allowed = manifest_stages()
    assert any("unknown stage" in problem for problem in verify(tmp_path, allowed_stages=allowed))
    report = scan_manifests(tmp_path, allowed_stages=allowed)
    assert report.problems
    assert "unknown stage" in report.problems[0].problems[0]
