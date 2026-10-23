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


def scan_manifests(
    root: str | Path,
    *,
    allowed_stages: set[str] | frozenset[str] | None = None,
) -> MigrationReport:
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
        if allowed_stages is not None and manifest.stage not in allowed_stages:
            problems.append(f"unknown stage {manifest.stage!r}")
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


# Chat stages spend a provider call per miss; the remaining stages are local
# deterministic work, so a stale ``letter`` manifest costs time but no quota.
# Reporting them under one number would overstate the billable forecast.
_CHAT_STAGES = frozenset({"extract", "bible", "beats", "page_plan", "storyboard", "page_script"})


@dataclass
class StageKeyCheck:
    """Stored manifests for one stage, split by whether current code reuses them.

    ``stale`` counts manifests whose recorded ``stage_src`` differs from what
    current code computes: the key would differ, so the work is redone.
    ``missing`` counts work that was never keyed at all.
    """

    stage: str
    current: int = 0
    stale: int = 0
    missing: int = 0

    @property
    def recalculated(self) -> int:
        return self.stale + self.missing

    @property
    def billable(self) -> bool:
        return self.stage in _CHAT_STAGES


@dataclass
class RecomputeForecast:
    """What a re-run would redo after a prompt or code change."""

    stages: list[StageKeyCheck] = field(default_factory=list)
    pages: int = 0
    pages_reusable: int = 0
    # Manifest stages this forecast cannot place. A stage added to the registry
    # but not to ``_STAGE_SRC_CONSTANTS`` used to be skipped silently, which made
    # the cost report under-count for exactly the stage someone just introduced.
    # They are listed here instead, so an incomplete registration is visible.
    unattributed: list[str] = field(default_factory=list)

    @property
    def chat_calls(self) -> int:
        """Billable calls: chat stages only. Local stages cost time, not quota."""
        return sum(check.recalculated for check in self.stages if check.billable)

    @property
    def local_redos(self) -> int:
        """Deterministic stages that redo without spending a provider call."""
        return sum(check.recalculated for check in self.stages if not check.billable)

    @property
    def repaints(self) -> int:
        return self.pages - self.pages_reusable


# A manifest records the ``stage_src`` hash its key was built with, so current
# code can be compared against what the file actually claims. This is a
# provable comparison, unlike re-deriving a whole key: it needs no harness, no
# model label, and no declared inputs, so it cannot become a second derivation
# path. Each entry maps a stage to the module-level source-hash constant the
# pipeline uses for that stage.
_STAGE_SRC_CONSTANTS = {
    "extract": "_EXTRACT_STAGE_SRC",
    "bible": "_BIBLE_STAGE_SRC",
    "beats": "_BEATS_STAGE_SRC",
    "page_plan": "_PAGE_PLAN_STAGE_SRC",
    "storyboard": "_STORYBOARD_STAGE_SRC",
    "page_script": "_PAGE_SCRIPT_STAGE_SRC",
    "render.page": "_RENDER_PAGE_STAGE_SRC",
    "render.panel": "_RENDER_PANEL_STAGE_SRC",
    "portrait": "_PORTRAIT_STAGE_SRC",
    "letter": "_LETTER_STAGE_SRC",
    "layout": "_LAYOUT_STAGE_SRC",
    "export": "_EXPORT_STAGE_SRC",
}


def _current_stage_src(stage: str) -> str | None:
    """The source hash current code would key ``stage`` with, if known."""
    from core.pipelines import creative_comic

    name = _STAGE_SRC_CONSTANTS.get(stage)
    if name is None:
        return None
    return getattr(creative_comic, name, None)


def check_recompute(root: str | Path) -> RecomputeForecast:
    """Predict a re-run's billable cost from stored manifests, without any provider.

    This is the ``migrate --check`` half of the prompt-harness migration note
    (T-05). It deliberately does **not** re-derive keys: that would need the
    harness, the model label, and every declared input, and a second derivation
    path is the drift this migration exists to remove. It compares two things
    the stored files can prove on their own:

    * A manifest whose recorded ``stage_src`` differs from the hash current code
      computes was keyed by different stage code, so it misses by construction.
    * Stage work with no manifest and no stored key was never keyed, so it will
      call once.

    Page art is reported separately, because a chat re-plan does not force a
    repaint unless the plan bytes change: the render key does not include the
    upstream plan key (§4).
    """
    from core.comic.cas import load_manifest

    root = Path(root)
    forecast = RecomputeForecast()
    by_stage: dict[str, StageKeyCheck] = {}
    for stage in _STAGE_SRC_CONSTANTS:
        check = StageKeyCheck(stage=stage)
        by_stage[stage] = check
        forecast.stages.append(check)

    index = root / "index"
    if index.is_dir():
        for path in sorted(index.glob("*.json")):
            try:
                manifest = load_manifest(root, f"sha256:{path.stem}")
            except (OSError, ValueError):
                continue
            if manifest is None:
                continue
            check = by_stage.get(manifest.stage)
            if check is None:
                # Unknown stage: report it rather than dropping it. Counting it
                # as reusable would understate the bill; counting it as stale
                # without being able to name the stage would be a guess.
                if manifest.stage not in forecast.unattributed:
                    forecast.unattributed.append(manifest.stage)
                continue
            current = _current_stage_src(manifest.stage)
            # ``None`` means this build has no source hash for a stage the
            # forecast otherwise knows. Nobody can prove it is current, so it is
            # counted as a miss: a forecast may overstate a redo, never hide one.
            if current is None:
                check.stale += 1
            elif manifest.stage_src and manifest.stage_src != current:
                check.stale += 1
            else:
                check.current += 1

    state_path = root / "state.json"
    if state_path.exists():
        from core.schemas import ProjectState

        state = ProjectState.load(state_path)
        for pageset in state.page_cache.values():
            if not pageset.action_key:
                by_stage["page_plan"].missing += 1
        for page_key in state.generated.pages:
            forecast.pages += 1
            if page_key in state.pages_done:
                forecast.pages_reusable += 1
    return forecast
