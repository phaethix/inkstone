"""core.comic.budget — quota budget accounts (§12, phase 5a).

A single run issues no more billable calls than its budget. Accounting is
three tiers:

1. ``quota.jsonl`` in the shared data directory — an append-only log of
   calls, not of stages.
2. Per-stage reservations in ``budget.json``. A stage that exhausts its own
   reservation pauses instead of borrowing from another, so a runaway render
   cannot consume the extract budget.
3. A recovery reserve withheld from every run of two or more calls, so the
   run cannot burn the call a later manual retry would need.

Exhaustion converts to a recoverable pause at the item boundary
(``outcome`` semantics of ``stopped``, §6). ``--no-carry-over`` is the
default: the same reservation is not re-armed on a later calendar day.
``--carry-over`` is the explicit switch that does, and the switch is written
into the project's ``runs.jsonl``.

Budget, carry-over, and remaining quota are runtime inputs. They never enter
an ``action_key``, ``h_env``, or a fingerprint (§12 invariant 10). Repeating
a pause at an unchanged boundary key appends no second stop record (§6).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# One call, once the budget is large enough to spare it. A one-call budget
# withholds nothing: there is no later retry to protect.
DEFAULT_RECOVERY_RESERVE = 1

_FINISHED_PAGE_FLOORS: tuple[tuple[str, int], ...] = (
    ("extract", 1),
    ("bible", 1),
    ("beats", 1),
    ("page_plan", 2),
    ("portrait", 1),
)
_PANEL_COMPOSE_FLOORS: tuple[tuple[str, int], ...] = (
    ("extract", 1),
    ("bible", 1),
    ("storyboard", 1),
    ("page_script", 1),
    ("portrait", 1),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class BudgetPaused(Exception):
    """The next billable call would exceed this run's budget.

    The item named by ``key`` was not started. A re-run with the same budget
    pauses here again without issuing the call and without a second stop
    record.
    """

    def __init__(self, *, key: str, stage: str, carry_over: bool) -> None:
        self.key = key
        self.stage = stage
        self.carry_over = carry_over
        switch = "on" if carry_over else "off"
        super().__init__(f"budget exhausted at {key} ({stage}); carry_over={switch}")


@dataclass(frozen=True)
class BudgetSpec:
    """The allowance for one run. Not a cache key."""

    total: int
    reservations: dict[str, int]
    recovery_reserve: int = 0
    carry_over: bool = False

    def __post_init__(self) -> None:
        reservations = {stage: int(calls) for stage, calls in self.reservations.items()}
        object.__setattr__(self, "reservations", reservations)
        if self.total < 0 or self.recovery_reserve < 0:
            raise ValueError("budget and recovery reserve must be >= 0")
        if self.recovery_reserve > self.total:
            raise ValueError("recovery reserve cannot exceed the budget")
        granted = sum(reservations.values())
        spendable = self.total - self.recovery_reserve
        if granted > spendable:
            raise ValueError(
                f"stage reservations ({granted}) exceed the spendable budget ({spendable})"
            )
        if any(calls < 0 for calls in reservations.values()):
            raise ValueError("a stage reservation must be >= 0")


def allocate(
    total: int,
    *,
    carry_over: bool = False,
    recovery_reserve: int | None = None,
    render_mode: str = "finished_page",
    page_script: bool = True,
) -> BudgetSpec:
    """Split ``total`` into a reserve plus per-stage floors, rest to render.

    Chat floors are funded before render, so render cannot borrow them.
    ``page_plan`` keeps two calls because the pipeline may retry it once.
    ``panel_compose`` funds storyboard instead of the finished-page plan
    stages, and funds page_script only when that path is enabled. An unused
    floor would shrink the render allowance for no call.
    """
    if total < 0:
        raise ValueError("budget must be >= 0")
    if recovery_reserve is None:
        recovery_reserve = DEFAULT_RECOVERY_RESERVE if total >= 2 else 0
    if render_mode == "panel_compose":
        floors = _PANEL_COMPOSE_FLOORS
        if not page_script:
            floors = tuple(item for item in floors if item[0] != "page_script")
    elif render_mode == "finished_page":
        floors = _FINISHED_PAGE_FLOORS
    else:
        raise ValueError(f"unknown render_mode: {render_mode}")

    spendable = total - recovery_reserve
    granted: dict[str, int] = {}
    left = spendable
    for stage, floor in floors:
        take = min(floor, max(left, 0))
        granted[stage] = take
        left -= take
    granted["render"] = max(left, 0)
    return BudgetSpec(
        total=total,
        recovery_reserve=recovery_reserve,
        carry_over=carry_over,
        reservations=granted,
    )


class ProjectBudget(BaseModel):
    """One project's reservation, persisted in the shared ``budget.json``."""

    model_config = ConfigDict(extra="ignore")

    total: int
    recovery_reserve: int = 0
    carry_over: bool = False
    day: str
    granted: dict[str, int] = Field(default_factory=dict)
    spent: dict[str, int] = Field(default_factory=dict)
    paused_key: str | None = None


class BudgetFile(BaseModel):
    """Cross-project budget accounts. Shared, like ``quota.jsonl``."""

    model_config = ConfigDict(extra="ignore")

    projects: dict[str, ProjectBudget] = Field(default_factory=dict)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class BudgetSession:
    """Checks and commits one run's billable calls against its reservation."""

    def __init__(
        self,
        *,
        data_dir: Path,
        output_dir: Path,
        project_id: str,
        store: BudgetFile,
        project: ProjectBudget,
    ) -> None:
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.project_id = project_id
        self._store = store
        self._project = project

    @classmethod
    def open(
        cls,
        *,
        data_dir: str | Path,
        project_id: str,
        output_dir: str | Path,
        spec: BudgetSpec,
        today: str | None = None,
    ) -> BudgetSession:
        """Load the shared account and adopt ``spec`` without forgetting spent calls.

        A new calendar day re-arms spent counts only when this invocation
        passes ``carry_over``. The same day never re-arms: raising a
        reservation continues from what was already spent.
        """
        data = Path(data_dir)
        out = Path(output_dir)
        day = today or _today()
        path = data / "budget.json"
        if path.is_file():
            store = BudgetFile.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            store = BudgetFile()

        project = store.projects.get(project_id)
        if project is None:
            project = ProjectBudget(
                total=spec.total,
                recovery_reserve=spec.recovery_reserve,
                carry_over=spec.carry_over,
                day=day,
                granted=dict(spec.reservations),
            )
        else:
            # The switch on *this* invocation decides the new day. Yesterday's
            # stored flag must not block an explicit --carry-over, and today's
            # flag must not mint a second allowance before midnight.
            if day != project.day and spec.carry_over:
                project.spent = {}
                project.paused_key = None
                project.day = day
            project.total = spec.total
            project.recovery_reserve = spec.recovery_reserve
            project.carry_over = spec.carry_over
            project.granted = dict(spec.reservations)
        store.projects[project_id] = project
        session = cls(
            data_dir=data,
            output_dir=out,
            project_id=project_id,
            store=store,
            project=project,
        )
        session._save()
        return session

    def charge(self, stage: str, item_key: str) -> None:
        """Commit one call, or pause at ``item_key`` without issuing it."""
        if not self._allowed(stage):
            self._pause(stage, item_key)
        self._project.spent[stage] = self._project.spent.get(stage, 0) + 1
        self._project.paused_key = None
        self._save()
        _append_jsonl(
            self.data_dir / "quota.jsonl",
            {
                "ts": _now_iso(),
                "project_id": self.project_id,
                "stage": stage,
                "key": item_key,
            },
        )

    def _allowed(self, stage: str) -> bool:
        spent = sum(self._project.spent.values())
        spendable = self._project.total - self._project.recovery_reserve
        if spent >= spendable:
            return False
        return self._project.spent.get(stage, 0) < self._project.granted.get(stage, 0)

    def _pause(self, stage: str, item_key: str) -> None:
        if self._project.paused_key != item_key:
            _append_jsonl(
                self.output_dir / "runs.jsonl",
                {
                    "kind": "stop",
                    "key": item_key,
                    "stage": stage,
                    "reason": "budget",
                    "carry_over": self._project.carry_over,
                    "ts": _now_iso(),
                },
            )
            self._project.paused_key = item_key
            self._save()
        raise BudgetPaused(
            key=item_key,
            stage=stage,
            carry_over=self._project.carry_over,
        )

    def _save(self) -> None:
        _atomic_write(
            self.data_dir / "budget.json",
            self._store.model_dump_json(indent=2),
        )
