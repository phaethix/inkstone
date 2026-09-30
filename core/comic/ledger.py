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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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