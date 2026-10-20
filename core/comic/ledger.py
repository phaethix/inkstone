"""core.comic.ledger — the explicit consistency ledger (§12).

Records *which pages must be visually consistent with each other*. It is
authoritative state, not derived from artifacts (§12 invariant 9): it is loaded
and saved like ``state.json``, and it enters no ``action_key`` or fingerprint
(§8 invariant 3, §12 invariant 10). Page identities are the 0b positional form
``c{ci:04d}-p{idx:04d}`` only — never a model ``page_id`` (§4).

Phase 0d lands the schema, maintenance, and queries with zero quota.
A recorded portrait stores its bytes in ``cas/`` and sets ``content_hash`` to
that id. Ledgers written before that field stay ``None``.

The escalation policy (phase 5b, §4 invariant 11) lives here too: below
``LEDGER_ESCALATION_THRESHOLD`` a pending set is quiet (ledger and panel only);
above it the render phase is held until ``confirm_review`` catches the reviewed
version up.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.comic.runs import append_run
from core.schemas import ProjectState

logger = logging.getLogger(__name__)

# §4 invariant 11 / §13 item 18: a character's pending pages escalate to a
# human confirmation only above this count — by default the same order as
# ``gate.SAMPLE_GATE_SIZE``, held by a test. At or below it the flag stays
# quiet: recorded in the ledger and shown in the aggregated panel only.
LEDGER_ESCALATION_THRESHOLD = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_name(name: str) -> str:
    """Strip a ``Name@stage`` suffix so a stage portrait maps to its character."""
    return (name or "").split("@", 1)[0].strip()


def _pages_by_character(state: ProjectState) -> dict[str, list[str]]:
    """Derive, per character, the positional page ids it appears on (§12).

    Reads ``page_cache`` — the plans the pipeline already produced — so the
    derivation is zero-quota. Each character's list is de-duplicated.
    """
    from core.comic.identity import page_state_key  # local: avoid an import cycle

    out: dict[str, list[str]] = {}
    for cache_key, pageset in state.page_cache.items():
        try:
            chunk_index = int(cache_key)
        except (TypeError, ValueError):
            continue
        for page_index, plan in enumerate(pageset.pages):
            key = page_state_key(chunk_index, page_index)
            names: list[str] = list(plan.reference_characters)
            for panel in plan.panels:
                names.extend(panel.characters)
            for raw in names:
                name = _base_name(raw)
                if not name:
                    continue
                bucket = out.setdefault(name, [])
                if key not in bucket:
                    bucket.append(key)
    return out


class ContinuityReviewPaused(Exception):
    """The render phase is held until a human confirms an oversized pending set.

    Raised at the plan→render boundary (§12 invariant 11, phase 5b): at least
    one character's pending pages exceed ``LEDGER_ESCALATION_THRESHOLD``. The
    pause is recoverable; a resume re-checks and stops again until
    ``ConsistencyLedger.confirm_review`` catches ``reviewed_version`` up.
    """

    def __init__(self, escalated: dict[str, list[str]]) -> None:
        self.escalated = {name: list(pages) for name, pages in escalated.items()}
        detail = ", ".join(
            f"{name} ({len(pages)} {'page' if len(pages) == 1 else 'pages'})"
            for name, pages in sorted(self.escalated.items())
        )
        super().__init__(f"awaiting_human: continuity review pending for {detail}")


def _last_ledger_stop(output_dir: Path) -> dict | None:
    """The most recent continuity-review stop in ``runs.jsonl``, if any.

    Used to keep §6's rule — a pause at an unchanged boundary appends no second
    stop record — without adding a persisted field anywhere.
    """
    path = output_dir / "runs.jsonl"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if (
            record.get("kind") == "stop"
            and record.get("stage") == "ledger"
            and record.get("reason") == "continuity_review"
        ):
            return record
    return None


class ReferenceVersion(BaseModel):
    """A character's authoritative reference image and its version."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    # ``sha256:`` id of the portrait bytes in ``cas/``. ``None`` on older ledgers.
    content_hash: str | None = None
    version: int = 0


class LedgerEntry(BaseModel):
    """What the ledger knows about one character."""

    model_config = ConfigDict(extra="ignore")

    # Positional page identities the character appears on (§4).
    pages: list[str] = Field(default_factory=list)
    reference: ReferenceVersion = Field(default_factory=ReferenceVersion)
    # Pages awaiting review because the reference version changed (§12).
    pending_pages: list[str] = Field(default_factory=list)
    # The reference version a human has already reviewed; pending is the set of
    # pages for versions strictly above this (Phase 5b raises it on acceptance).
    reviewed_version: int = 0
    # Pages already accepted at ``reviewed_version``. A later reference bump
    # only puts pages that were never reviewed back into pending, so a
    # high-frequency character does not re-escalate on every portrait change.
    reviewed_pages: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now_iso)


class ConsistencyLedger(BaseModel):
    """The consistency ledger persisted to ``consistency.json`` (§6)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    characters: dict[str, LedgerEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> ConsistencyLedger:
        """Load the ledger; a missing file yields an empty ledger.

        The ledger is reconstructible from ``page_cache`` (§12 invariant 9), so
        a missing file is not an error.
        """
        p = Path(path)
        if not p.is_file():
            return cls()
        return cls.model_validate_json(p.read_text(encoding="utf-8"))

    @classmethod
    def load_or_rebuild(
        cls,
        path: str | Path,
        state: ProjectState,
    ) -> ConsistencyLedger:
        """Load the ledger; reconstruct it when the file is absent or corrupt.

        A corrupt ledger must not block a resumable run (§12 invariant 9 is about
        authority, not fragility): fall back to an empty ledger rebuilt from
        ``page_cache``. A *valid* file wins — it may hold human decisions a
        rebuild cannot reproduce.
        """
        p = Path(path)
        if p.is_file():
            try:
                return cls.load(p)
            except (ValidationError, json.JSONDecodeError) as exc:
                # A corrupt ledger is recoverable; a transient OSError is not,
                # and must surface before any caller can overwrite human state.
                logger.warning("corrupt consistency ledger at %s (%s); rebuilding", p, exc)
        led = cls()
        led.rebuild_from_state(state)
        return led

    def save(self, path: str | Path) -> None:
        """Persist atomically, so interruption cannot truncate the ledger."""
        from core.comic.atomic import atomic_write_text

        atomic_write_text(path, self.model_dump_json(indent=2))

    def rebuild_from_state(self, state: ProjectState) -> bool:
        """Refresh every character's ``pages`` and pending set from ``page_cache``.

        Idempotent and zero-quota. ``pages`` is derived; ``pending_pages`` is
        recomputed as "pages for a reference version above the reviewed one", so
        it is independent of whether a portrait was regenerated before or after
        the plans were known. A character's ``reference`` is never cleared here:
        it records a product decision (§12 invariant 9), not a derivation.
        Returns ``True`` when anything changed.
        """
        derived = _pages_by_character(state)
        changed = False

        # A character that no longer appears on any planned page must not keep
        # a stale page set. The entry itself is retained: its reference and
        # reviewed_version are product decisions a rebuild cannot reproduce.
        for name, entry in self.characters.items():
            if name in derived:
                continue
            if entry.pages or entry.pending_pages:
                entry.pages = []
                entry.pending_pages = []
                entry.updated_at = _now_iso()
                changed = True

        for name, pages in derived.items():
            entry = self.characters.get(name)
            if entry is None:
                entry = LedgerEntry(pages=list(pages))
                self.characters[name] = entry
                changed = True
            elif entry.pages != pages:
                entry.pages = list(pages)
                entry.updated_at = _now_iso()
                changed = True
            if entry.reference.version > entry.reviewed_version:
                # A new reference invalidates every page review: the portrait
                # changed, so the whole cast page set is pending again.
                if entry.reviewed_pages:
                    entry.reviewed_pages = []
                    changed = True
                pending = list(entry.pages)
            else:
                if entry.reviewed_version > 0 and not entry.reviewed_pages:
                    # A ledger written before reviewed_pages existed already
                    # carries a full confirmation at this version.
                    entry.reviewed_pages = list(entry.pages)
                    changed = True
                reviewed = set(entry.reviewed_pages)
                pending = [page for page in entry.pages if page not in reviewed]
                kept_reviewed = [page for page in entry.pages if page in reviewed]
                if entry.reviewed_pages != kept_reviewed:
                    entry.reviewed_pages = kept_reviewed
                    changed = True
            if entry.pending_pages != pending:
                entry.pending_pages = pending
                entry.updated_at = _now_iso()
                changed = True
        return changed

    def pages_for(self, name: str) -> list[str]:
        """The positional page ids ``name`` appears on (empty when unknown)."""
        entry = self.characters.get(name)
        return list(entry.pages) if entry is not None else []

    def record_reference(
        self,
        name: str,
        path: str,
        content_hash: str | None = None,
    ) -> None:
        """Record that ``name``'s authoritative reference was (re)generated.

        Bumps the reference version; ``rebuild_from_state`` then marks the
        character's pages pending for review. Called on a real generation event,
        not on a mere path comparison, because a regeneration overwrites the same
        path and only the event distinguishes it.
        """
        entry = self.characters.get(name)
        if entry is None:
            entry = LedgerEntry()
            self.characters[name] = entry
        entry.reference = ReferenceVersion(
            path=path,
            content_hash=content_hash,
            version=entry.reference.version + 1,
        )
        entry.updated_at = _now_iso()

    def pending_pages(self) -> dict[str, list[str]]:
        """Characters with pending pages, for the aggregated UI panel (§4)."""
        return {
            name: list(entry.pending_pages)
            for name, entry in self.characters.items()
            if entry.pending_pages
        }

    def escalated(self, threshold: int | None = None) -> dict[str, list[str]]:
        """Characters whose pending set exceeds the escalation threshold (§4).

        Strictly above: "exceed a threshold" and the quiet default both hold
        only when the comparison is ``>``. ``threshold`` is for tests; the
        production default lives in ``LEDGER_ESCALATION_THRESHOLD``.
        """
        limit = LEDGER_ESCALATION_THRESHOLD if threshold is None else threshold
        return {
            name: list(entry.pending_pages)
            for name, entry in self.characters.items()
            if entry.pages and len(entry.pending_pages) > limit
        }

    def confirm_review(self, name: str, pages: list[str] | None = None) -> bool:
        """Accept some or all of a character's pending pages (§12, phase 5b).

        With ``pages=None`` (the CLI default) every current page is accepted.
        With a subset, those pages are recorded and only the rest stay pending,
        so a 31-page character can be reviewed in batches. Returns whether
        anything changed; idempotent.
        """
        entry = self.characters.get(name)
        if entry is None:
            return False
        target = set(entry.pages if pages is None else pages)
        target &= set(entry.pages)
        if not target:
            return False
        reviewed = set(entry.reviewed_pages) | target
        new_reviewed = [page for page in entry.pages if page in reviewed]
        new_pending = [page for page in entry.pages if page not in reviewed]
        if (
            entry.reviewed_version >= entry.reference.version
            and entry.reviewed_pages == new_reviewed
            and entry.pending_pages == new_pending
        ):
            return False
        entry.reviewed_version = entry.reference.version
        entry.reviewed_pages = new_reviewed
        entry.pending_pages = new_pending
        entry.updated_at = _now_iso()
        return True

    def record_escalation_stop(
        self,
        output_dir: str | Path,
        threshold: int | None = None,
    ) -> bool:
        """Append the §6 stop record for an escalated boundary, once per change.

        The record is audit only and is never replayed. An unchanged boundary
        (same pending counts and reference versions) appends no second record,
        so an unconfirmed resume loop cannot pollute ``runs.jsonl``. Returns
        whether a record was written.
        """
        limit = LEDGER_ESCALATION_THRESHOLD if threshold is None else threshold
        characters = {
            name: {"pending": len(entry.pending_pages), "version": entry.reference.version}
            for name, entry in sorted(self.characters.items())
            if len(entry.pending_pages) > limit
        }
        if not characters:
            return False
        out = Path(output_dir)
        previous = _last_ledger_stop(out)
        if previous is not None and previous.get("characters") == characters:
            return False
        append_run(
            out,
            {
                "kind": "stop",
                "stage": "ledger",
                "reason": "continuity_review",
                "characters": characters,
                "ts": _now_iso(),
            },
        )
        return True

    def rename_character(self, old: str, new: str) -> None:
        """Merge ``old``'s entry into ``new`` (alias merge, §12 rule 3)."""
        if old == new:
            return
        incoming = self.characters.pop(old, None)
        if incoming is None:
            return
        target = self.characters.get(new)
        if target is None:
            self.characters[new] = incoming
            return
        for key in incoming.pages:
            if key not in target.pages:
                target.pages.append(key)
        for key in incoming.pending_pages:
            if key not in target.pending_pages:
                target.pending_pages.append(key)
        union_pending = list(target.pending_pages)
        if incoming.reference.version > target.reference.version:
            target.reference = incoming.reference
        merged_reviewed = set(target.reviewed_pages) | set(incoming.reviewed_pages)
        target.reviewed_pages = [page for page in target.pages if page in merged_reviewed]
        # A merge must never leave reviewed_version ahead of the merged
        # reference. Doing so would make the pending test (reference > reviewed)
        # permanently false and silently close continuity review.
        target.reviewed_version = min(
            target.reviewed_version,
            incoming.reviewed_version,
            target.reference.version,
        )
        # When the merged reference moved ahead of the reviewed version, every
        # merged page is pending. Otherwise keep the recorded pending union: a
        # ledger can mark pages pending before any portrait exists (v0 == v0).
        if target.reference.version > target.reviewed_version:
            target.pending_pages = list(target.pages)
        elif target.reviewed_pages:
            reviewed = set(target.reviewed_pages)
            target.pending_pages = [page for page in target.pages if page not in reviewed]
        else:
            target.pending_pages = union_pending
        target.updated_at = _now_iso()
