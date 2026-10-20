"""core.experiment — planning-only A/B probes for harness candidates.

One experiment uses a single chunk. By default each candidate runs extract,
key beats, and page plan under its own harness (three chat calls, no images).
``shared_evidence=True`` keeps the cheaper probe: extract/beats run once under
the builtin harness and only page_plan is per candidate.

Candidates are anonymised A/B; a human picks one with
``inkstone experiment pick``. ``inkstone experiment compose`` then copies the
picked tree into a new harness directory, optionally taking named components
from another candidate (``--merge-from`` + ``--component``). Composition is
explicit, validated in a staging directory, and never overwrites the builtin
tree.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.api import ChatProvider, get_chat_provider
from core.comic.segmentation import segment_text
from core.comic.structure_report import structural_report
from core.density import PANELS_PER_CHUNK_B
from core.harness.spec import Harness, HarnessError, builtin_harness, harness_from_dir
from core.schemas import ProjectState
from core.screenwriter import (
    build_beats_request,
    build_extract_request,
    build_page_plan_request,
    extract_key_beats,
    extract_story_elements,
    plan_comic_pages,
)


class ExperimentError(ValueError):
    """An experiment is malformed, or its candidates cannot be probed."""


@dataclass
class Candidate:
    label: str
    harness: Harness | None
    anon: str = ""
    calls: int = 0


class CountingChat(ChatProvider):
    """Forward to a provider and count every forced call."""

    def __init__(self, inner: ChatProvider) -> None:
        self.inner = inner
        self.calls = 0
        self.model = getattr(inner, "model", "")

    async def chat_function_call(self, messages, tools, tool_choice, **kwargs):
        self.calls += 1
        return await self.inner.chat_function_call(messages, tools, tool_choice, **kwargs)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_prompt(directory: Path, stage: str, request) -> None:
    payload = {
        "stage": stage,
        "digest": request.digest(),
        "model": request.model,
        "messages": request.messages,
        "tools": request.tools,
        "tool_choice": request.tool_choice,
    }
    _write_json(directory / "prompts" / f"{stage}.json", payload)
    if stage == "page_plan":
        # Keep the MVP's flat prompt.json as an alias for existing readers.
        _write_json(directory / "prompt.json", payload)


def _load_candidates(specs: list[str]) -> list[Candidate]:
    if len(specs) < 2:
        raise ExperimentError("an experiment needs at least two --candidate entries")
    seen: set[str] = set()
    out: list[Candidate] = []
    for spec in specs:
        label = (spec or "").strip()
        if not label:
            raise ExperimentError("candidate label must not be empty")
        if label in seen:
            raise ExperimentError(f"duplicate candidate: {label}")
        seen.add(label)
        if label == "base":
            out.append(Candidate(label="base", harness=None))
            continue
        try:
            harness = harness_from_dir(label)
        except HarnessError as exc:
            raise ExperimentError(f"{label}: {exc}") from exc
        problems = harness.validate()
        if problems:
            raise ExperimentError(f"{label}: " + "; ".join(problems))
        out.append(Candidate(label=label, harness=harness))
    return out


def _assign_anonymized(candidates: list[Candidate], *, seed_text: str) -> None:
    """Stable A/B/C labels: sorted labels, then a seeded shuffle."""
    seed = int(hashlib.sha1(seed_text.encode("utf-8")).hexdigest()[:12], 16)
    ordered = sorted(candidates, key=lambda candidate: candidate.label)
    random.Random(seed).shuffle(ordered)
    for index, candidate in enumerate(ordered):
        candidate.anon = chr(ord("A") + index)


async def plan_experiment(
    source_txt: str,
    *,
    out_dir: str | Path,
    candidates: list[str],
    chunk_index: int = 0,
    genre: str | None = None,
    chat: ChatProvider | None = None,
    seed: str = "",
    shared_evidence: bool = False,
) -> dict:
    """Probe the planning pipeline for each candidate; write an anonymised experiment.

    By default each candidate runs extract, key beats, and page plan under its
    own harness. ``shared_evidence=True`` keeps the cheaper MVP: extract/beats
    run once under the builtin harness and only page_plan is per candidate.
    """
    chunks = segment_text(source_txt)
    if not chunks:
        raise ExperimentError("source text produced no chunks")
    if chunk_index < 0 or chunk_index >= len(chunks):
        raise ExperimentError(
            f"chunk index {chunk_index} out of range; source has {len(chunks)} chunks"
        )
    chunk = chunks[chunk_index]
    loading = _load_candidates(candidates)
    _assign_anonymized(loading, seed_text=f"{source_txt}\n{chunk_index}\n{seed}")

    provider = chat or get_chat_provider()
    counting = CountingChat(provider)
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise ExperimentError(f"experiment directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "task.md").write_text(chunk, encoding="utf-8")

    model = getattr(provider, "model", "")
    shared_elements = None
    shared_beats = None
    shared_calls = 0
    if shared_evidence:
        shared_extract = build_extract_request(chunk, harness=None, model=model)
        shared_elements = await extract_story_elements(chunk, chat=counting, request=shared_extract)
        shared_beats = await extract_key_beats(
            chunk,
            shared_elements,
            chat=counting,
            request=build_beats_request(chunk, shared_elements, harness=None, model=model),
        )
        shared_calls = counting.calls
        _write_prompt(out / "shared", "extract", shared_extract)
        _write_prompt(
            out / "shared",
            "beats",
            build_beats_request(chunk, shared_elements, harness=None, model=model),
        )

    records: dict[str, dict] = {}
    for candidate in loading:
        before = counting.calls
        directory = out / "candidates" / candidate.anon
        if shared_evidence:
            elements = shared_elements
            beats = shared_beats
        else:
            extract_request = build_extract_request(chunk, harness=candidate.harness, model=model)
            _write_prompt(directory, "extract", extract_request)
            elements = await extract_story_elements(chunk, chat=counting, request=extract_request)
            beats_request = build_beats_request(
                chunk, elements, harness=candidate.harness, model=model
            )
            _write_prompt(directory, "beats", beats_request)
            beats = await extract_key_beats(chunk, elements, chat=counting, request=beats_request)
        request = build_page_plan_request(
            chunk,
            elements,
            recent_layouts=[],
            visual_bible=None,
            key_beats=beats,
            panel_budget=PANELS_PER_CHUNK_B,
            genre=genre,
            harness=candidate.harness,
            model=model,
        )
        _write_prompt(directory, "page_plan", request)
        pageset = await plan_comic_pages(
            chunk,
            elements,
            chat=counting,
            recent_layouts=[],
            visual_bible=None,
            key_beats=beats,
            panel_budget=PANELS_PER_CHUNK_B,
            request=request,
        )
        candidate.calls = counting.calls - before
        _write_json(directory / "plan.json", pageset.model_dump(mode="json"))
        state = ProjectState(project_id=f"experiment-{candidate.anon}")
        state.page_cache[str(chunk_index)] = pageset
        state.beat_cache[str(chunk_index)] = beats
        report = structural_report(state, chunk)
        _write_json(directory / "structure_report.json", report.to_json())
        records[candidate.anon] = {
            "label": candidate.label,
            "harness_digest": (
                candidate.harness.digest() if candidate.harness is not None else "base"
            ),
            "chat_calls": candidate.calls,
            "prompt_digest": request.digest(),
        }

    manifest = {
        "schema": 1,
        "created_at": _now_iso(),
        "status": "awaiting_selection",
        "source_sha256": hashlib.sha256(source_txt.encode("utf-8")).hexdigest(),
        "chunk_index": chunk_index,
        "chunk_chars": len(chunk),
        "genre": genre,
        "evidence": "shared" if shared_evidence else "candidate",
        "candidates": records,
        "cost": {
            "chat_calls": counting.calls,
            "image_calls": 0,
            "shared_calls": shared_calls,
        },
    }
    _write_json(out / "experiment.json", manifest)
    (out / "README.md").write_text(
        "# Blind A/B experiment\n\n"
        "Open `candidates/<id>/plan.json` and `candidates/<id>/structure_report.json`.\n"
        "The candidate labels are hidden until selection.\n\n"
        "Record a choice with:\n\n"
        f'    inkstone experiment pick --run {out} --choice A --why "..."\n',
        encoding="utf-8",
    )
    return manifest


def pick_experiment(run_dir: str | Path, *, choice: str, why: str) -> dict:
    """Record a human choice and reveal the candidate label in selection.json."""
    run = Path(run_dir)
    manifest_path = run / "experiment.json"
    if not manifest_path.is_file():
        raise ExperimentError(f"no experiment.json under {run}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = manifest.get("candidates") or {}
    anon = (choice or "").strip().upper()
    if anon not in candidates:
        raise ExperimentError(f"choice {choice!r} is not one of {', '.join(sorted(candidates))}")
    if not (why or "").strip():
        raise ExperimentError("a selection needs --why; an unexplained pick is not a record")
    selection = {
        "experiment": run.name,
        "chunk": manifest.get("chunk_index"),
        "candidates": {
            key: {
                "harness_digest": value.get("harness_digest"),
                "label": value.get("label"),
            }
            for key, value in candidates.items()
        },
        "chosen": anon,
        "chosen_label": candidates[anon].get("label"),
        "why": why.strip(),
        "cost": manifest.get("cost"),
        "human": True,
        "selected_at": _now_iso(),
    }
    _write_json(run / "selection.json", selection)
    manifest["status"] = "selected"
    manifest["chosen"] = anon
    _write_json(manifest_path, manifest)
    return selection


def _candidate_harness(manifest: dict, candidate_id: str) -> Harness:
    record = (manifest.get("candidates") or {}).get(candidate_id)
    if record is None:
        raise ExperimentError(f"candidate {candidate_id!r} is not in the experiment")
    label = str(record.get("label", ""))
    if label == "base":
        return builtin_harness()
    try:
        return harness_from_dir(label)
    except HarnessError as exc:
        raise ExperimentError(f"candidate {candidate_id}: {exc}") from exc


def compose_harness(
    run_dir: str | Path,
    *,
    choice: str,
    out_dir: str | Path,
    merge_from: list[str] | None = None,
    components: list[str] | None = None,
) -> dict:
    """Materialise the human-picked harness, optionally merging components.

    The chosen tree is copied first. Each ``--merge-from`` candidate contributes
    only the explicitly named components; there is no implicit merge. The
    result is validated in a temporary directory and only then moved into place.
    """
    run = Path(run_dir)
    manifest_path = run / "experiment.json"
    if not manifest_path.is_file():
        raise ExperimentError(f"no experiment.json under {run}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selection = _experiment_selection(run)
    if selection is None:
        raise ExperimentError("pick a candidate before composing (no selection.json)")
    anon = (choice or "").strip().upper()
    if anon != selection.get("chosen"):
        raise ExperimentError(
            f"choice {choice!r} does not match the recorded pick {selection.get('chosen')!r}"
        )
    chosen = _candidate_harness(manifest, anon)
    merge_ids = [value.strip() for value in (merge_from or []) if value.strip()]
    component_ids = [value.strip() for value in (components or []) if value.strip()]
    if merge_ids and not component_ids:
        raise ExperimentError("--merge-from needs at least one --component")
    if component_ids and not merge_ids:
        raise ExperimentError("--component needs at least one --merge-from")

    merge_sources: list[tuple[str, Harness]] = []
    for spec in merge_ids:
        if spec in (manifest.get("candidates") or {}):
            merge_sources.append((spec, _candidate_harness(manifest, spec)))
        else:
            try:
                merge_sources.append((spec, harness_from_dir(spec)))
            except HarnessError as exc:
                raise ExperimentError(f"merge source {spec}: {exc}") from exc

    out = Path(out_dir)
    if out.exists():
        raise ExperimentError(f"output already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_name(out.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(chosen.root, staging)
        for component_id in component_ids:
            target = next((c for c in chosen.components if c.id == component_id), None)
            if target is None:
                raise ExperimentError(f"component {component_id!r} is not in the chosen harness")
            for source_label, source in merge_sources:
                source_component = next(
                    (c for c in source.components if c.id == component_id), None
                )
                if source_component is None:
                    raise ExperimentError(
                        f"component {component_id!r} is not in merge source {source_label}"
                    )
                (staging / target.path).write_text(
                    source.component_text(source_component.id), encoding="utf-8"
                )
        merged = harness_from_dir(staging)
        problems = merged.validate()
        if problems:
            raise ExperimentError("composed harness is invalid: " + "; ".join(problems))
        digest = merged.digest()
        staging.replace(out)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    record = {
        "experiment": run.name,
        "chosen": anon,
        "chosen_label": selection.get("chosen_label"),
        "why": selection.get("why"),
        "components": component_ids,
        "merge_from": merge_ids,
        "harness_digest": digest,
        "harness_dir": str(out),
        "created_at": _now_iso(),
    }
    _write_json(run / "compose.json", record)
    manifest["status"] = "composed"
    manifest["composed_harness"] = str(out)
    manifest["composed_digest"] = digest
    _write_json(manifest_path, manifest)
    (out / "ADOPTION.md").write_text(
        "# Adopted harness\n\n"
        f"- experiment: `{run.name}`\n"
        f"- chosen: `{anon}` ({selection.get('chosen_label')})\n"
        f"- why: {selection.get('why')}\n"
        f"- merged components: {', '.join(component_ids) or '(none)'}\n"
        f"- digest: `{digest}`\n",
        encoding="utf-8",
    )
    return record


def _experiment_selection(run: Path) -> dict | None:
    path = run / "selection.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- sequential recursion -------------------------------------------------


def _sequence_path(run: str | Path) -> Path:
    return Path(run) / "sequence.json"


def load_sequence(run: str | Path) -> dict:
    path = _sequence_path(run)
    if not path.is_file():
        raise ExperimentError(f"no sequence.json under {run}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_sequence(run: Path, sequence: dict) -> None:
    _write_json(_sequence_path(run), sequence)


def sequence_status(run: str | Path) -> dict:
    """Return a compact view of a sequential run."""
    sequence = load_sequence(run)
    return {
        "run": str(Path(run)),
        "status": sequence.get("status"),
        "rounds": sequence.get("rounds"),
        "current_round": sequence.get("current_round"),
        "completed_rounds": sequence.get("completed_rounds", []),
        "current_checkpoint": sequence.get("current_checkpoint"),
        "candidates": sequence.get("candidates", []),
    }


def _round_candidates(base: str, candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in [base, *candidates]:
        key = str(Path(value).resolve()) if value != "base" else "base"
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


async def plan_sequence_async(
    source_txt: str,
    *,
    out_dir: str | Path,
    candidates: list[str],
    base: str | None = None,
    rounds: int = 2,
    chunk_index: int = 0,
    genre: str | None = None,
    chat: ChatProvider | None = None,
    seed: str = "",
    shared_evidence: bool = False,
) -> dict:
    """Async body for ``plan_sequence`` (kept separate so the CLI can await it)."""
    if rounds < 1:
        raise ExperimentError("rounds must be >= 1")
    if not candidates:
        raise ExperimentError("a sequence needs at least one --candidate")
    base_spec = (base or "base").strip()
    round_candidates = _round_candidates(base_spec, candidates)
    _load_candidates(round_candidates)
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise ExperimentError(f"sequence directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "task.md").write_text(source_txt, encoding="utf-8")
    round_one = out / "rounds" / "1"
    manifest = await plan_experiment(
        source_txt,
        out_dir=round_one,
        candidates=round_candidates,
        chunk_index=chunk_index,
        genre=genre,
        chat=chat,
        seed=f"{seed}-r1",
        shared_evidence=shared_evidence,
    )
    sequence = {
        "schema": 1,
        "created_at": _now_iso(),
        "status": "awaiting_pick",
        "source_sha256": manifest["source_sha256"],
        "chunk_index": chunk_index,
        "genre": genre,
        "rounds": rounds,
        "current_round": 1,
        "completed_rounds": [],
        "base": base_spec,
        "candidates": list(candidates),
        "shared_evidence": shared_evidence,
        "current_checkpoint": base_spec,
        "seed": seed,
        "round_dirs": {"1": str(round_one)},
    }
    _write_sequence(out, sequence)
    return sequence


async def advance_sequence_async(
    run_dir: str | Path,
    *,
    chat: ChatProvider | None = None,
    merge_from: list[str] | None = None,
    components: list[str] | None = None,
) -> dict:
    """Compose the current round's pick and build the next round, if any."""
    run = Path(run_dir)
    sequence = load_sequence(run)
    if sequence.get("status") == "done":
        raise ExperimentError("sequence is already done")
    current = int(sequence.get("current_round", 1))
    total = int(sequence.get("rounds", 1))
    round_dir = run / "rounds" / str(current)
    experiment_path = round_dir / "experiment.json"
    if not experiment_path.is_file():
        raise ExperimentError(f"round {current} has no experiment.json")
    selection = _experiment_selection(round_dir)
    if selection is None:
        raise ExperimentError(f"round {current} has no selection; pick a candidate first")
    chosen = str(selection.get("chosen") or "")
    checkpoint = round_dir / "checkpoint"
    compose_harness(
        round_dir,
        choice=chosen,
        out_dir=checkpoint,
        merge_from=merge_from or [],
        components=components or [],
    )
    sequence.setdefault("completed_rounds", []).append(current)
    sequence["current_checkpoint"] = str(checkpoint)
    if current >= total:
        sequence["status"] = "done"
        sequence["current_round"] = current
        _write_sequence(run, sequence)
        return sequence
    next_round = current + 1
    sequence["current_round"] = next_round
    sequence["status"] = "awaiting_pick"
    round_candidates = _round_candidates(str(checkpoint), list(sequence.get("candidates") or []))
    next_dir = run / "rounds" / str(next_round)
    await plan_experiment(
        (run / "task.md").read_text(encoding="utf-8") if (run / "task.md").is_file() else "",
        out_dir=next_dir,
        candidates=round_candidates,
        chunk_index=int(sequence.get("chunk_index", 0)),
        genre=sequence.get("genre"),
        chat=chat,
        seed=f"{sequence.get('seed', '')}-r{next_round}",
        shared_evidence=bool(sequence.get("shared_evidence")),
    )
    sequence.setdefault("round_dirs", {})[str(next_round)] = str(next_dir)
    _write_sequence(run, sequence)
    return sequence
