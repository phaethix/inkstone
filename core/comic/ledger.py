"""core.comic.ledger — the explicit consistency ledger (§12).

Records *which pages must be visually consistent with each other*. It is
authoritative state, not derived from artifacts (§12 invariant 9): it is loaded
and saved like ``state.json``, and it enters no ``action_key`` or fingerprint
(§8 invariant 3, §12 invariant 10). Page identities are the 0b positional form
``c{ci:04d}-p{idx:04d}`` only — never a model ``page_id`` (§4).

Phase 0d lands the schema, maintenance, and queries with zero quota and no CAS.
Phase 1 points the §4 tier-2 reference at this ledger's authoritative version;
``content_hash`` is reserved for that and stays ``None`` until the CAS exists.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from core.schemas import ProjectState


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


class ReferenceVersion(BaseModel):
    """A character's authoritative reference image and its version."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    # Reserved for Phase 1: the CAS content hash. ``None`` until the CAS lands.
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
    updated_at: str = Field(default_factory=_now_iso)


class ConsistencyLedger(BaseModel):
    """The consistency ledger persisted to ``consistency.json`` (§6)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    characters: dict[str, LedgerEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ConsistencyLedger":
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
    ) -> "ConsistencyLedger":
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
            except Exception:  # noqa: BLE001 - a corrupt ledger is recoverable
                pass
        led = cls()
        led.rebuild_from_state(state)
        return led

    def save(self, path: str | Path) -> None:
        """Persist atomically, so interruption cannot truncate the ledger."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                temp.write(self.model_dump_json(indent=2))
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, p)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

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
            pending = (
                list(entry.pages) if entry.reference.version > entry.reviewed_version else []
            )
            if entry.pending_pages != pending:
                entry.pending_pages = pending
                entry.updated_at = _now_iso()
                changed = True
        return changed