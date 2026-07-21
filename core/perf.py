"""core.perf — lightweight stage timing / instrumentation.

A ``PerfCollector`` accumulates labelled ``time.monotonic()`` measurements
across a pipeline run and logs a structured summary at the end. It is designed
to be lightweight (no external deps, no background threads) so it can be left
on in production.

Usage::

    from core.perf import PerfCollector

    perf = PerfCollector()
    with perf.measure("segment"):
        chunks = segment_text(text)

    ... more stages ...

    perf.log_summary()
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _Record:
    label: str
    elapsed: float


@dataclass
class PerfCollector:
    """Collect labelled wall-clock measurements and log a summary."""

    records: list[_Record] = field(default_factory=list, init=False)
    _t0: float = field(default_factory=time.monotonic, init=False)

    # -- helpers for labelled stages that repeat (e.g. "panel" per panel) ------
    _acc: dict[str, list[float]] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @contextmanager
    def measure(self, label: str) -> Generator[None, None, None]:
        """Context manager: record elapsed wall-clock for a labelled stage.

        Every invocation logs an info line for that stage.  Repeated labels
        accumulate into ``_acc`` so the summary can show count / avg / total.
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - t0
            self.records.append(_Record(label=label, elapsed=elapsed))
            self._acc.setdefault(label, []).append(elapsed)
            logger.info("⏱  %-36s %8.1fs", label, elapsed)

    def log_summary(self, level: int = logging.DEBUG) -> None:
        """Print a structured timing summary to stdout (and log at ``level``).

        Stages that appeared only once are shown as a single line.  Stages that
        repeated (e.g. per-panel generation) show count / avg / total.
        """
        total = time.monotonic() - self._t0
        lines: list[str] = ["timing summary:"]

        # Separate unique vs repeated labels.
        repeated = {k: v for k, v in self._acc.items() if len(v) > 1}
        seen_repeated: set[str] = set()

        for rec in self.records:
            if rec.label in repeated:
                if rec.label in seen_repeated:
                    continue
                seen_repeated.add(rec.label)
                vals = repeated[rec.label]
                cnt = len(vals)
                t = sum(vals)
                avg = t / cnt
                pct = t / total * 100 if total else 0
                lines.append(
                    f"  {rec.label:<36} x{cnt:>3}  total={t:7.1f}s  avg={avg:6.1f}s  ({pct:5.1f}%)"
                )
            else:
                pct = rec.elapsed / total * 100 if total else 0
                lines.append(f"  {rec.label:<36} {rec.elapsed:8.1f}s ({pct:5.1f}%)")

        lines.append(f"  {'TOTAL':<36} {total:8.1f}s")
        summary = "\n".join(lines)
        print(summary)
        logger.log(level, summary)
