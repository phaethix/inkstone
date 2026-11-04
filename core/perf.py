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
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Set by the CLI front-end while a dynamic progress bar is live. Static report
# lines must go through it (tqdm.write) so they are inserted above the bar
# instead of interleaving with its carriage-return redraw. None = plain print.
_safe_write: Callable[[str], None] | None = None


def set_safe_write(writer: Callable[[str], None] | None) -> None:
    """Register a writer for report lines (e.g. ``tqdm.write``), or reset it."""
    global _safe_write
    _safe_write = writer


def _write_block(text: str) -> None:
    """Emit a multi-line report block without corrupting a live progress bar."""
    if _safe_write is not None:
        _safe_write(text)
    else:
        print(text)


@dataclass
class _Record:
    label: str
    elapsed: float


@dataclass
class PerfCollector:
    """Collect labelled wall-clock measurements and log a summary."""

    records: list[_Record] = field(default_factory=list, init=False)
    _t0: float = field(default_factory=time.monotonic, init=False)

    # Helpers for labelled stages that repeat (e.g. "panel" per panel).
    _acc: dict[str, list[float]] = field(default_factory=dict, init=False)

    # Public API
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
        """Print an aligned timing summary (and log at ``level``).

        Every stage uses the same column layout: label, count, total, avg,
        percentage. Single-shot stages leave count/avg blank; repeated ones
        fill them in. ``TOTAL`` closes the block on the same grid.
        """
        total = time.monotonic() - self._t0
        repeated = {k: v for k, v in self._acc.items() if len(v) > 1}
        seen: set[str] = set()

        # Unified grid: label | count | total | avg | pct. Single-shot stages
        # use a '-' placeholder so every column lines up across all rows.
        label_w = 12
        lines: list[str] = [
            "timing summary:",
            f"  {'stage':<{label_w}}  {'count':>5}  {'total':>9}  {'avg':>9}  {'pct':>7}",
        ]
        for rec in self.records:
            if rec.label in seen:
                continue
            seen.add(rec.label)
            if rec.label in repeated:
                vals = repeated[rec.label]
                cnt = len(vals)
                t = sum(vals)
                avg = t / cnt
                pct = t / total * 100 if total else 0
                lines.append(
                    f"  {rec.label:<{label_w}}  {cnt:>5}  {t:8.1f}s  {avg:8.1f}s  {pct:6.1f}%"
                )
            else:
                pct = rec.elapsed / total * 100 if total else 0
                lines.append(
                    f"  {rec.label:<{label_w}}  {'-':>5}  {rec.elapsed:8.1f}s  "
                    f"{'-':>9}  {pct:6.1f}%"
                )
        lines.append(f"  {'TOTAL':<{label_w}}  {'-':>5}  {total:8.1f}s  {'-':>9}  {100.0:6.1f}%")

        summary = "\n".join(lines)
        _write_block(summary)
        logger.log(level, summary)
