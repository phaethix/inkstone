"""The per-stage runner holds the four properties §9 states for it."""

from pathlib import Path

import pytest

from core.comic.cas import Manifest, put_bytes, record_ok, save_manifest
from core.pipelines.stage_registry import stage_spec
from core.pipelines.stage_runner import (
    manifest_stage_for,
    record,
    replay,
    run_stage,
)

_TOKEN = "sha256:" + "b" * 64


def _key(tag: str) -> str:
    return "sha256:" + (tag * 64)[:64]


def _record(tmp_path: Path, stage: str, key: str, name: str = "a.bin") -> None:
    record_ok(
        tmp_path,
        key=key,
        stage=stage,
        stage_src="src",
        env="env",
        inputs=[],
        name=name,
        data=b"payload",
    )


def test_the_manifest_stage_comes_from_the_registry_not_the_caller():
    """Property 3: a runner cannot invent a stage string.

    ``webtoon`` is the sharp case: it is its own spec but records under
    ``export``. Asking for the spec name and getting ``export`` is the mapping
    that keeps the runner and ``verify`` in agreement.
    """
    assert manifest_stage_for("webtoon") == "export"
    assert manifest_stage_for("webtoon") == stage_spec("webtoon").manifest_stage
    assert manifest_stage_for("letter") == "letter"


def test_an_unknown_stage_is_refused():
    with pytest.raises(KeyError):
        manifest_stage_for("not-a-stage")


def test_a_hit_issues_no_external_call(tmp_path):
    """Property 2: the produce callback must not run on a hit."""
    key = _key("a")
    _record(tmp_path, "letter", key)

    def explode():
        raise AssertionError("a settled key must not produce again")

    run = run_stage(tmp_path, "letter", key, explode, stage_src="src", env="env", inputs=[])
    assert run.replayed is True
    assert run.executed is False
    assert run.external_calls == 0
    assert run.files == (("a.bin", b"payload"),)


def test_a_miss_produces_once_and_records_under_the_registry_stage(tmp_path):
    key = _key("b")
    calls = []

    def produce():
        calls.append(1)
        return [("out.png", b"bytes")]

    run = run_stage(tmp_path, "webtoon", key, produce, stage_src="src", env="env", inputs=["in"])
    assert calls == [1]
    assert run.executed is True
    assert run.replayed is False
    assert run.external_calls == 1
    assert run.manifest_stage == "export"

    recorded = Manifest.model_validate_json(
        (tmp_path / "index" / f"{key.removeprefix('sha256:')}.json").read_text()
    )
    assert recorded.stage == "export"
    assert recorded.key == key


def test_the_second_run_of_the_same_key_does_not_produce(tmp_path):
    """The whole point of the runner: a resume is free."""
    key = _key("c")
    calls = []

    def produce():
        calls.append(1)
        return [("out.png", b"bytes")]

    first = run_stage(tmp_path, "letter", key, produce, stage_src="src", env="env", inputs=[])
    second = run_stage(tmp_path, "letter", key, produce, stage_src="src", env="env", inputs=[])
    assert calls == [1]
    assert first.executed is True
    assert second.replayed is True
    assert second.files == first.files


def test_a_tombstone_settles_without_a_provider_call(tmp_path):
    """A rejected key is a hit on the rejected side: no call, no re-attempt."""
    key = _key("d")
    save_manifest(
        tmp_path,
        Manifest(key=key, stage="letter", outcome="rejected", reason="policy"),
    )

    def explode():
        raise AssertionError("a tombstoned key must not produce again")

    run = run_stage(tmp_path, "letter", key, explode, stage_src="src", env="env", inputs=[])
    assert run.tombstoned is True
    assert run.external_calls == 0
    assert run.settled is True


def test_an_empty_production_is_not_recorded_as_ok(tmp_path):
    """An ``ok`` manifest with no outputs would be a hit nothing can satisfy."""
    key = _key("e")
    run = record(
        tmp_path,
        "letter",
        key,
        [],
        stage_src="src",
        env="env",
        inputs=[],
        executed=True,
    )
    assert run.files == ()
    assert replay(tmp_path, "letter", key) is None


def test_an_empty_key_is_refused(tmp_path):
    """An empty key would make every run a hit."""
    with pytest.raises(ValueError):
        replay(tmp_path, "letter", "")
    with pytest.raises(ValueError):
        record(
            tmp_path,
            "letter",
            "   ",
            [("a.bin", b"bytes")],
            stage_src="src",
            env="env",
            inputs=[],
        )


def test_a_missing_cas_object_is_a_miss_not_a_hit(tmp_path):
    """A manifest whose bytes are gone must not report a usable hit."""
    key = _key("f")
    _record(tmp_path, "letter", key)
    object_file = next((tmp_path / "cas").rglob("*.bin"))
    object_file.unlink()

    assert replay(tmp_path, "letter", key) is None


def test_two_stages_sharing_a_key_do_not_collide(tmp_path):
    """The stage is part of the identity a runner reads back."""
    key = _key("g")
    put_bytes(tmp_path, b"unused")
    record(
        tmp_path,
        "layout",
        key,
        [("page_01.png", b"layout")],
        stage_src="src",
        env="env",
        inputs=[],
        executed=True,
    )
    # ``layout`` wrote under ``layout``; a letter read of the same key misses.
    assert replay(tmp_path, "letter", key) is None
    assert replay(tmp_path, "layout", key) is not None


def test_the_runner_touches_only_its_own_manifest_and_objects(tmp_path):
    """Property 4: no state outside the stage's outputs.

    A control key written before the run must survive it untouched.
    """
    other = _key("h")
    _record(tmp_path, "bible", other, name="bible.json")
    before = (tmp_path / "index" / f"{other.removeprefix('sha256:')}.json").read_bytes()

    key = _key("i")
    run_stage(
        tmp_path,
        "letter",
        key,
        lambda: [("out.png", b"bytes")],
        stage_src="src",
        env="env",
        inputs=[],
    )

    after = (tmp_path / "index" / f"{other.removeprefix('sha256:')}.json").read_bytes()
    assert before == after
    assert run_stage(
        tmp_path, "bible", other, lambda: [], stage_src="src", env="env", inputs=[]
    ).replayed
