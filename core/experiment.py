"""core.experiment — planning-only A/B probes for harness candidates.

One experiment uses a single chunk and a shared base evidence set (extract +
key beats). Each candidate then runs only the page-plan request, so the default
comparison costs no image calls. Candidates are anonymised A/B; a human picks
one with ``inkstone experiment pick`` and the selection is recorded.

This is the Phase 2 MVP: it does not compose a harness automatically and it
does not run extract/beats under the candidate. Those are the next steps once
the selection format has real use.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.api import ChatProvider, get_chat_provider
from core.comic.segmentation import segment_text
from core.comic.structure_report import structural_report
from core.density import PANELS_PER_CHUNK_B
from core.harness.spec import Harness, HarnessError, harness_from_dir
from core.schemas import ProjectState
from core.screenwriter import (
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
) -> dict:
    """Probe page planning for each candidate; write an anonymised experiment."""
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

    elements = await extract_story_elements(chunk, chat=counting)
    beats = await extract_key_beats(chunk, elements, chat=counting)
    shared_calls = counting.calls

    records: dict[str, dict] = {}
    for candidate in loading:
        before = counting.calls
        request = build_page_plan_request(
            chunk,
            elements,
            recent_layouts=[],
            visual_bible=None,
            key_beats=beats,
            panel_budget=PANELS_PER_CHUNK_B,
            genre=genre,
            harness=candidate.harness,
            model=getattr(provider, "model", ""),
        )
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
        directory = out / "candidates" / candidate.anon
        _write_json(
            directory / "prompt.json",
            {
                "stage": "page_plan",
                "digest": request.digest(),
                "model": request.model,
                "messages": request.messages,
                "tools": request.tools,
                "tool_choice": request.tool_choice,
            },
        )
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
