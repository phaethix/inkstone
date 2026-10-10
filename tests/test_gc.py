"""Phase 4 reclaims cas objects nothing still names, and only when they are old."""

import json
import os
import time

from core.comic.cas import (
    Manifest,
    ManifestOutput,
    put_bytes,
    record_ok,
    save_manifest,
    verify,
)
from core.comic.gc import GcError, apply_gc, plan_gc
from core.comic.ledger import ConsistencyLedger, LedgerEntry, ReferenceVersion
from core.comic.prune import parse_older_than
from core.schemas import GeneratedPage, GeneratedPanel, ProjectState


def _age(path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _orphan(root, data: bytes):
    content_id = put_bytes(root, data)
    path = root / "cas" / content_id[7:9] / f"{content_id[7:]}.bin"
    _age(path, 100 * 86400)
    return content_id, path


def test_an_old_orphan_is_listed_and_deleted_only_on_apply(tmp_path):
    _content_id, path = _orphan(tmp_path, b"orphan")

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert [candidate.path for candidate in plan.candidates] == [path]
    assert path.is_file()
    apply_gc(plan)
    assert not path.is_file()
    assert verify(tmp_path) == []


def test_a_young_orphan_stays(tmp_path):
    content_id = put_bytes(tmp_path, b"fresh")
    path = tmp_path / "cas" / content_id[7:9] / f"{content_id[7:]}.bin"

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert plan.candidates == []
    assert path.is_file()


def test_manifest_outputs_and_inputs_stay(tmp_path):
    blank = put_bytes(tmp_path, b"blank")
    page = put_bytes(tmp_path, b"page")
    record_ok(
        tmp_path,
        key="a" * 64,
        stage="letter",
        stage_src="src",
        env="env",
        inputs=[blank],
        name="page.png",
        data=b"page",
    )
    for content_id in (blank, page):
        _age(tmp_path / "cas" / content_id[7:9] / f"{content_id[7:]}.bin", 100 * 86400)

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert plan.candidates == []


def test_a_rejected_manifest_keeps_its_previous_bytes(tmp_path):
    previous = put_bytes(tmp_path, b"previous")
    save_manifest(
        tmp_path,
        Manifest(
            key="sha256:" + "b" * 64,
            stage="letter",
            outcome="rejected",
            reason="bad",
            supersedes=[previous],
        ),
    )
    _age(tmp_path / "cas" / previous[7:9] / f"{previous[7:]}.bin", 100 * 86400)

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert plan.candidates == []


def test_bytes_only_named_by_an_ok_supersedes_list_are_reclaimable(tmp_path):
    previous, path = _orphan(tmp_path, b"replaced")
    save_manifest(
        tmp_path,
        Manifest(
            key="sha256:" + "c" * 64,
            stage="letter",
            outcome="ok",
            outputs=[ManifestOutput(name="page.png", content=put_bytes(tmp_path, b"current"))],
            supersedes=[previous],
        ),
    )

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert [candidate.path for candidate in plan.candidates] == [path]


def test_a_ledger_content_hash_stays(tmp_path):
    content_id, path = _orphan(tmp_path, b"portrait")
    ConsistencyLedger(
        characters={
            "甲": LedgerEntry(reference=ReferenceVersion(content_hash=content_id)),
        }
    ).save(tmp_path / "consistency.json")

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert plan.candidates == []
    assert path.is_file()


def test_a_gate_content_hash_stays(tmp_path):
    content_id, _path = _orphan(tmp_path, b"pending")
    (tmp_path / "gate.json").write_text(
        json.dumps(
            {
                "projects": {
                    "p": {
                        "sample_size": 1,
                        "sample_keys": [],
                        "note": content_id,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert plan.candidates == []


def _pending_page(
    root,
    data: bytes,
    *,
    decision: dict | None = None,
    released: bool = False,
    yes: bool = False,
):
    page = root / "pages" / "page.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(data)
    content_id, blob = _orphan(root, data)
    state = ProjectState(project_id="p")
    state.generated.pages["c0000-p0000"] = GeneratedPage(local=str(page))
    state.save(root / "state.json")
    project = {
        "sample_size": 1,
        "sample_keys": ["c0000-p0000"],
        "decisions": {} if decision is None else {"c0000-p0000": decision},
        "released": released,
        "yes": yes,
    }
    (root / "gate.json").write_text(
        json.dumps({"projects": {"p": project}}),
        encoding="utf-8",
    )
    return content_id, blob, page


def test_a_pending_sample_page_keeps_its_bytes(tmp_path):
    _content_id, blob, _page = _pending_page(tmp_path, b"pending-page")

    plan = plan_gc(tmp_path, parse_older_than("7d"))

    assert blob not in {candidate.path for candidate in plan.candidates}


def test_a_redraw_keeps_the_page_and_an_acceptance_does_not(tmp_path):
    _content_id, blob, _page = _pending_page(
        tmp_path, b"redraw-page", decision={"choice": "redraw"}
    )
    assert plan_gc(tmp_path, parse_older_than("7d")).candidates == []

    gate = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    gate["projects"]["p"]["decisions"] = {"c0000-p0000": {"choice": "accept"}}
    (tmp_path / "gate.json").write_text(json.dumps(gate), encoding="utf-8")

    plan = plan_gc(tmp_path, parse_older_than("7d"))
    assert [candidate.path for candidate in plan.candidates] == [blob]


def test_a_released_or_yes_sample_does_not_keep_the_page(tmp_path):
    _content_id, blob, _page = _pending_page(tmp_path, b"released-page", released=True)
    plan = plan_gc(tmp_path, parse_older_than("7d"))
    assert [candidate.path for candidate in plan.candidates] == [blob]

    gate = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    gate["projects"]["p"]["released"] = False
    gate["projects"]["p"]["yes"] = True
    (tmp_path / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
    plan = plan_gc(tmp_path, parse_older_than("7d"))
    assert [candidate.path for candidate in plan.candidates] == [blob]


def test_a_pending_blank_and_a_shared_gate_keep_their_bytes(tmp_path):
    blank = tmp_path / "pages" / "blank.png"
    blank.parent.mkdir()
    blank.write_bytes(b"blank-bytes")
    _content_id, blob = _orphan(tmp_path, b"blank-bytes")
    state = ProjectState(project_id="p")
    state.generated.pages["c0000-p0000"] = GeneratedPage(
        local=str(tmp_path / "pages" / "missing.png"),
        blank_local=str(blank),
    )
    panel = tmp_path / "panels" / "panel.png"
    panel.parent.mkdir()
    panel.write_bytes(b"panel-bytes")
    panel_id, panel_blob = _orphan(tmp_path, b"panel-bytes")
    state.generated.panels["c0000-p0001"] = GeneratedPanel(local=str(panel))
    state.save(tmp_path / "state.json")
    shared = tmp_path / "shared" / "gate.json"
    shared.parent.mkdir()
    shared.write_text(
        json.dumps(
            {
                "projects": {
                    "p": {
                        "sample_size": 2,
                        "sample_keys": ["c0000-p0000", "c0000-p0001"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = plan_gc(tmp_path, parse_older_than("7d"), gate_files=[shared])

    assert blob not in {candidate.path for candidate in plan.candidates}
    assert panel_blob not in {candidate.path for candidate in plan.candidates}
    assert panel_id.startswith("sha256:")


def test_an_unreadable_state_refuses_to_plan(tmp_path):
    _pending_page(tmp_path, b"pending-page")
    (tmp_path / "state.json").write_text("{", encoding="utf-8")

    try:
        plan_gc(tmp_path, parse_older_than("7d"))
    except GcError as exc:
        assert "state.json" in str(exc)
    else:
        raise AssertionError("expected GcError")


def test_an_unreadable_manifest_refuses_to_plan(tmp_path):
    _orphan(tmp_path, b"orphan")
    index = tmp_path / "index"
    index.mkdir()
    (index / f"{'d' * 64}.json").write_text("{", encoding="utf-8")

    try:
        plan_gc(tmp_path, parse_older_than("7d"))
    except GcError as exc:
        assert "unreadable" in str(exc)
    else:
        raise AssertionError("expected GcError")
