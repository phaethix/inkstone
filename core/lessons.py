"""core.lessons — approved craft lessons from human decisions.

A lesson is a durable, transferable note distilled from a human gate/alias/
continuity decision. It is stored per genre under
``<data_dir>/library/<genre>/<id>.json`` and injected into page planning through
the ``{{craft_notes}}`` slot.

Rules that keep the library honest:

- only a human action writes a lesson (``inkstone lesson add``);
- an entry needs a run, a decision, and a cue; ``NO_ENTRY`` is a valid choice;
- no measurement markers (``structure_report``, ``acceptance rate``, ...);
- retired entries stay on disk; planning reads only ``active`` ones;
- retrieval is genre + cue-keyword based, capped at 3 entries / 1,500 chars.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.config import data_dir as config_data_dir
from core.harness.spec import FORBIDDEN_MARKERS, available_genres, harness_digest

logger = logging.getLogger(__name__)

DECISIONS = frozenset(
    {"redraw", "accept_and_flag", "alias_merge", "alias_dismiss", "continuity_confirm"}
)
PAGE_DECISIONS = frozenset({"redraw", "accept_and_flag"})
APPLIES_TO = frozenset({"page_plan", "storyboard", "bible", "system"})
CONFIDENCE = frozenset({"low", "medium", "high"})
STATUSES = frozenset({"proposed", "active", "retired"})
MAX_CUE_CHARS = 240
MAX_LESSON_CHARS = 1200
MAX_RETRIEVED = 3
MAX_CRAFT_CHARS = 1500

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z0-9_]+")


class LessonError(ValueError):
    """A lesson is malformed or its evidence is missing."""


class LessonEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run: str = ""
    pages: list[str] = Field(default_factory=list)
    decision: str = ""
    reason_tier: str | None = None


class Lesson(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    status: Literal["proposed", "active", "retired"] = "active"
    cue: str
    lesson: str
    evidence: LessonEvidence
    confidence: Literal["low", "medium", "high"] = "medium"
    applies_to: Literal["page_plan", "storyboard", "bible", "system"] = "page_plan"
    genre: str = "general"
    harness_digest: str = ""
    created_at: str
    updated_at: str

    @field_validator("cue", "lesson")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("cue")
    @classmethod
    def _cue_length(cls, value: str) -> str:
        if len(value) > MAX_CUE_CHARS:
            raise ValueError(f"cue must be <= {MAX_CUE_CHARS} chars")
        return value

    @field_validator("lesson")
    @classmethod
    def _lesson_length(cls, value: str) -> str:
        if len(value) > MAX_LESSON_CHARS:
            raise ValueError(f"lesson must be <= {MAX_LESSON_CHARS} chars")
        return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "l-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]


def _tokens(text: str) -> set[str]:
    """Latin words plus CJK bigrams: enough for cue matching without embeddings."""
    low = (text or "").lower()
    out = set(_LATIN.findall(low))
    for run in _CJK.findall(low):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[index : index + 2] for index in range(len(run) - 1))
    return out


def format_craft_notes(lessons: list[Lesson]) -> str:
    """Render retrieved lessons as a compact, clearly-scoped prompt block."""
    if not lessons:
        return ""
    lines = ["Craft notes from earlier books (apply only when relevant):"]
    for lesson in lessons:
        cue = lesson.cue.strip().rstrip("。")
        lines.append(f"- When {cue}: {lesson.lesson.strip()}")
    return "\n".join(lines)


class LessonStore:
    """The per-genre library on disk. One JSON file per lesson, INDEX rebuilt."""

    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root) if root is not None else config_data_dir()
        self.root = base / "library"

    def genre_dir(self, genre: str) -> Path:
        name = (genre or "general").strip().lower()
        if name not in available_genres():
            raise LessonError(f"unknown genre {name!r}; available: {', '.join(available_genres())}")
        return self.root / name

    def path_for(self, genre: str, lesson_id: str) -> Path:
        return self.genre_dir(genre) / f"{lesson_id}.json"

    def _load(self, path: Path) -> Lesson | None:
        try:
            return Lesson.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("skipping unreadable lesson %s (%s)", path, exc)
            return None

    def list(
        self,
        *,
        genre: str | None = None,
        include_inactive: bool = False,
    ) -> list[Lesson]:
        genres = [genre] if genre is not None else sorted(available_genres())
        out: list[Lesson] = []
        for name in genres:
            directory = self.root / (name or "general")
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                lesson = self._load(path)
                if lesson is None:
                    continue
                if not include_inactive and lesson.status != "active":
                    continue
                out.append(lesson)
        return out

    def add(
        self,
        *,
        cue: str,
        lesson: str,
        run: str,
        decision: str,
        pages: list[str] | None = None,
        reason_tier: str | None = None,
        confidence: str = "medium",
        applies_to: str = "page_plan",
        genre: str | None = None,
        harness: str | None = None,
        proposed: bool = False,
        lesson_id: str | None = None,
    ) -> Lesson:
        normalized = (decision or "").strip().lower().replace("-", "_")
        if normalized not in DECISIONS:
            raise LessonError(
                f"unknown decision {decision!r}; expected one of {', '.join(sorted(DECISIONS))}"
            )
        if not (run or "").strip():
            raise LessonError("a lesson needs --from-run evidence")
        page_list = [page for page in (pages or []) if page]
        if normalized in PAGE_DECISIONS and not page_list:
            raise LessonError(f"a {normalized} lesson needs at least one page")
        if (confidence or "").strip().lower() not in CONFIDENCE:
            raise LessonError(f"confidence must be one of {', '.join(sorted(CONFIDENCE))}")
        if (applies_to or "").strip().lower() not in APPLIES_TO:
            raise LessonError(f"applies_to must be one of {', '.join(sorted(APPLIES_TO))}")
        combined = f"{cue}\n{lesson}".lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in combined:
                raise LessonError(
                    f"lesson contains measurement marker {marker!r}; "
                    "a lesson describes craft, not a score"
                )
        name = (genre or "general").strip().lower()
        directory = self.genre_dir(name)
        lesson_id = (lesson_id or _new_id()).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,80}", lesson_id):
            raise LessonError(
                "lesson id must be lowercase letters, digits, '_' or '-' (max 81 chars)"
            )
        path = directory / f"{lesson_id}.json"
        if path.exists():
            raise LessonError(f"lesson id already exists: {lesson_id}")
        now = _now_iso()
        record = Lesson(
            id=lesson_id,
            status="proposed" if proposed else "active",
            cue=cue,
            lesson=lesson,
            evidence=LessonEvidence(
                run=run.strip(),
                pages=page_list,
                decision=normalized,
                reason_tier=reason_tier,
            ),
            confidence=confidence.strip().lower(),
            applies_to=applies_to.strip().lower(),
            genre=name,
            harness_digest=harness or harness_digest(),
            created_at=now,
            updated_at=now,
        )
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.rebuild_index(name)
        return record

    def retire(self, lesson_id: str) -> Lesson:
        for genre in sorted(available_genres()):
            path = self.root / genre / f"{lesson_id}.json"
            if not path.is_file():
                continue
            record = self._load(path)
            if record is None:
                raise LessonError(f"lesson is unreadable: {path}")
            record.status = "retired"
            record.updated_at = _now_iso()
            path.write_text(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.rebuild_index(genre)
            return record
        raise LessonError(f"lesson not found: {lesson_id}")

    def retrieve(
        self,
        *,
        genre: str,
        query: str = "",
        applies_to: str = "page_plan",
        limit: int = MAX_RETRIEVED,
        max_chars: int = MAX_CRAFT_CHARS,
    ) -> list[Lesson]:
        """Active lessons for this genre, ranked by cue overlap and confidence.

        The general genre is a fallback for every genre. A non-empty query with
        no token overlap yields nothing: injecting an unrelated note is worse
        than injecting none.
        """
        query_tokens = _tokens(query)
        candidates: list[tuple[float, Lesson]] = []
        names = [genre] if genre == "general" else [genre, "general"]
        for name in names:
            directory = self.root / name
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                lesson = self._load(path)
                if lesson is None or lesson.status != "active":
                    continue
                if lesson.applies_to != applies_to:
                    continue
                score = self._score(lesson, query_tokens, genre)
                if score > 0:
                    candidates.append((score, lesson))
        candidates.sort(
            key=lambda item: (
                -item[0],
                -{"high": 3, "medium": 2, "low": 1}.get(item[1].confidence, 0),
                item[1].updated_at,
            )
        )
        out: list[Lesson] = []
        used = 0
        for _score, lesson in candidates:
            if len(out) >= limit:
                break
            cost = len(lesson.cue) + len(lesson.lesson) + 12
            if used + cost > max_chars:
                continue
            out.append(lesson)
            used += cost
        return out

    @staticmethod
    def _score(lesson: Lesson, query_tokens: set[str], genre: str) -> float:
        cue_tokens = _tokens(f"{lesson.cue} {lesson.lesson}")
        if query_tokens:
            overlap = query_tokens & cue_tokens
            if not overlap:
                return 0.0
            score = len(overlap) / math.sqrt(max(1, len(cue_tokens)))
        else:
            score = 1.0
        score *= {"high": 1.2, "medium": 1.0, "low": 0.8}.get(lesson.confidence, 1.0)
        if lesson.genre == genre:
            score *= 1.1
        return score

    def rebuild_index(self, genre: str) -> Path:
        directory = self.root / genre
        directory.mkdir(parents=True, exist_ok=True)
        entries = [self._load(path) for path in sorted(directory.glob("*.json"))]
        entries = [entry for entry in entries if entry is not None]
        lines = [
            f"# Lesson library — {genre}",
            "",
            "Entries are written by a human decision and read by page planning. "
            "Retired entries stay here as history.",
            "",
        ]
        if not entries:
            lines.append("_(empty)_")
        for entry in sorted(entries, key=lambda item: item.id):
            lines.append(f"- `{entry.id}` ({entry.status}, {entry.confidence}) — {entry.cue}")
        path = directory / "INDEX.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
