"""core.comic.review_packet — the local evidence directory for one run.

The packet is for humans, never for a model. It records the exact chat requests
that were sent, the source, the structural report, and the human decision lines
already in ``runs.jsonl``. That makes a failure reproducible after the code has
moved on, which a prompt hash alone cannot do.

Layout::

    review/
      manifest.json
      source.txt
      prompts/<stage>.c<chunk>.json
      structure_report.json
      decisions.jsonl
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.harness.request import ChatRequest

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ReviewPacket:
    """Write one project's review evidence; disabled is a cheap no-op."""

    def __init__(self, output_dir: str | Path, *, project_id: str, enabled: bool = True) -> None:
        self.root = Path(output_dir) / "review"
        self.project_id = project_id
        self.enabled = enabled
        self._manifest: dict = {}

    def start(
        self,
        *,
        source: str,
        state,
        genre: str,
        harness_digest: str,
        render_mode: str,
        density: str,
    ) -> None:
        if not self.enabled:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "source.txt").write_text(source, encoding="utf-8")
        except OSError as exc:
            logger.warning("review packet disabled: cannot write %s (%s)", self.root, exc)
            self.enabled = False
            return
        self._manifest = {
            "schema": 1,
            "project_id": self.project_id,
            "status": "running",
            "started_at": _now_iso(),
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_chars": len(source),
            "genre": genre,
            "harness_digest": harness_digest,
            "render_mode": render_mode,
            "density": density,
            "model_snapshot": state.model_snapshot.model_dump(),
        }
        _write_json(self.root / "manifest.json", self._manifest)

    def record(self, stage: str, chunk_index: int, request: ChatRequest) -> None:
        """Record the exact request a stage sent (prompt bodies are local only)."""
        if not self.enabled:
            return
        try:
            _write_json(
                self.root / "prompts" / f"{stage}.c{chunk_index:04d}.json",
                {
                    "stage": stage,
                    "chunk": chunk_index,
                    "digest": request.digest(),
                    "model": request.model,
                    "messages": request.messages,
                    "tools": request.tools,
                    "tool_choice": request.tool_choice,
                },
            )
        except OSError as exc:
            logger.warning("review packet prompt not written (%s)", exc)

    def record_plan(self, stage: str, chunk_index: int, payload: dict) -> None:
        """Record the plan output a human needs to judge a redraw or a lesson."""
        if not self.enabled:
            return
        try:
            _write_json(
                self.root / "plans" / f"{stage}.c{chunk_index:04d}.json",
                payload,
            )
        except OSError as exc:
            logger.warning("review packet plan not written (%s)", exc)

    def finish(self, *, state, source: str, pages: list[str]) -> None:
        """Write the structure report and the final manifest. Never raises."""
        if not self.enabled:
            return
        try:
            from core.comic.structure_report import (
                chapter_validated,
                structural_report,
            )

            report = structural_report(state, source)
            payload = report.to_json()
            payload["chapter_validated"] = chapter_validated(report, state)
            _write_json(self.root / "structure_report.json", payload)
            _write_json(
                self.root / "pages.json",
                {
                    "count": len(pages),
                    "pages": [str(Path(page).name) for page in pages],
                },
            )
            self._manifest.update(
                {
                    "status": "finished",
                    "finished_at": _now_iso(),
                    "pages_done": list(state.pages_done),
                    "pages_stored": [str(Path(page).name) for page in pages],
                    "structure_ok": report.ok,
                    "chapter_validated": payload["chapter_validated"],
                }
            )
            _write_json(self.root / "manifest.json", self._manifest)
            self._copy_decisions()
        except Exception as exc:  # noqa: BLE001 — evidence must never break a run
            # A packet is a diagnostic side effect; a generation that finished
            # is still a generation. The next run rewrites the manifest.
            logger.warning("review packet finish failed (%s)", exc)

    def _copy_decisions(self) -> None:
        runs = self.root.parent / "runs.jsonl"
        if not runs.is_file():
            return
        rows = []
        for line in runs.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") in {"gate", "stop", "rebuild"}:
                rows.append(row)
        path = self.root / "decisions.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
