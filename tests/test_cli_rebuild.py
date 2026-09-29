"""Phase 0f: the tombstone override path, ``rebuild --stage render --key <k>``.

Design §7 defines the ``rebuild`` mode as "ignore hits for a named stage or
subtree, force regeneration, rewrite manifests" and requires it to "also clear
any tombstone it targets, otherwise a page rejected in error can never be
retried". §9 makes the pairing mandatory: tombstones and their override ship in
the same phase, never tombstones first.
"""

import argparse

from core.cli import _build_parser, _run_rebuild
from core.schemas import ProjectState, Tombstone


def _args(out, keys=(), stage="render"):
    return argparse.Namespace(out=str(out), stage=stage, key=list(keys))


def _state_with_tombstone(out, key="c0000:u1_p0001"):
    state_path = out / "state.json"
    state = ProjectState.load(state_path) if state_path.exists() else ProjectState(project_id="t")
    state.tombstones[key] = Tombstone(
        outcome="rejected", reason="content_policy", stage="render.page"
    )
    state.skipped_pages.append(key)
    state.save(state_path)
    return state


def test_rebuild_parser_accepts_stage_and_repeatable_key():
    parser = _build_parser()

    args = parser.parse_args(
        ["rebuild", "--out", "o", "--stage", "render", "--key", "k1", "--key", "k2"]
    )

    assert args.command == "rebuild"
    assert args.stage == "render"
    assert args.key == ["k1", "k2"]


def test_rebuild_clears_the_tombstone_and_its_skip_record(tmp_path):
    _state_with_tombstone(tmp_path)

    exit_code = _run_rebuild(_args(tmp_path, ["c0000:u1_p0001"]))

    assert exit_code == 0
    saved = ProjectState.load(tmp_path / "state.json")
    assert saved.tombstones == {}
    assert saved.skipped_pages == []
    # Released for regeneration, not silently accepted: the page must be redrawn.
    assert "c0000:u1_p0001" not in saved.pages_done


def test_rebuild_reports_an_unknown_key_without_claiming_success(tmp_path, capsys):
    _state_with_tombstone(tmp_path)

    exit_code = _run_rebuild(_args(tmp_path, ["c0000:does_not_exist"]))

    assert exit_code == 1
    assert "c0000:does_not_exist" in capsys.readouterr().out


def test_rebuild_rejects_a_non_render_stage(tmp_path, capsys):
    """Only the render stage owns page tombstones; other stages have none yet."""
    _state_with_tombstone(tmp_path)

    exit_code = _run_rebuild(_args(tmp_path, ["c0000:u1_p0001"], stage="letter"))

    assert exit_code == 1
    assert "render" in capsys.readouterr().out
    assert ProjectState.load(tmp_path / "state.json").tombstones


def test_rebuild_without_keys_clears_only_the_named_stage(tmp_path):
    """§7: ``--stage`` alone targets the whole stage; --key narrows it."""
    _state_with_tombstone(tmp_path, key="c0000:u1_p0001")
    _state_with_tombstone(tmp_path, key="c0001:u2_p0001")

    exit_code = _run_rebuild(_args(tmp_path, ["c0000:u1_p0001"]))

    assert exit_code == 0
    saved = ProjectState.load(tmp_path / "state.json")
    assert "c0000:u1_p0001" not in saved.tombstones
    assert "c0001:u2_p0001" in saved.tombstones
