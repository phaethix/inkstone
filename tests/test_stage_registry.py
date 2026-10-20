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


def test_every_hard_input_names_a_producer():
    """Phase 0c: a contract's inputs and the read-edge table cannot drift.

    Adding ``hard_inputs`` without saying who produces them would leave the
    declared dependency as prose, which is what this edge table exists to stop.
    """
    from core.pipelines.stage_registry import input_sources

    for spec in all_specs():
        hard_inputs = stage_contract_for(spec.name).hard_inputs
        assert set(input_sources(spec.name)) == set(hard_inputs), spec.name


def test_no_stage_reads_a_later_phase():
    """A read edge that points forward would be a graph the scheduler cannot run."""
    from core.pipelines.stage_registry import read_edges, stage_spec

    phase_order = {"plan": 0, "render": 1, "export": 2}
    for consumer, producers in read_edges().items():
        consumer_order = phase_order[stage_spec(consumer).phase.value]
        for producer in producers:
            assert phase_order[stage_spec(producer).phase.value] <= consumer_order, (
                f"{consumer} reads {producer}"
            )


def test_a_forward_read_edge_is_rejected(monkeypatch):
    """The check must fail on a real mistake, not pass because it checks nothing."""
    import core.pipelines.stage_registry as registry

    broken = dict(registry._INPUT_SOURCES["letter"])
    broken["blank_page"] = "export"
    monkeypatch.setitem(registry._INPUT_SOURCES, "letter", broken)

    problems = registry.validate_read_edges()
    assert any("runs later" in problem for problem in problems), problems


def test_an_unproduced_hard_input_is_rejected(monkeypatch):
    import core.pipelines.stage_registry as registry

    broken = dict(registry._INPUT_SOURCES["letter"])
    del broken["blank_page"]
    monkeypatch.setitem(registry._INPUT_SOURCES, "letter", broken)

    problems = registry.validate_read_edges()
    assert any("no declared producer" in problem for problem in problems), problems


def test_upstream_of_walks_the_whole_chain():
    """A finished page cannot precede its plan, its bible, or its beats."""
    from core.pipelines.stage_registry import upstream_of

    assert {"page_plan", "bible", "beats", "render.page"} <= set(upstream_of("render.page"))
    assert "render.page" in upstream_of("export")
    assert upstream_of("extract") == frozenset({"extract"})
