"""tests/test_cli_progress_monotonic.py — the terminal bar must not rewind.

Bug: ``on_progress`` assigned ``pbar.n = percent * 100`` directly. When a
resumed run's derived fraction drops (a later chunk enlarges the character
registry, so the portrait share is re-divided), the bar visibly rewound
(10% -> 9%) even though the run had only moved forward. The Web UI already
clamps with ``max(prev, percent)``; the CLI must do the same.
"""

import io
import re

from core.cli_generate import _progress_display


def _percents(stream: io.StringIO) -> list[int]:
    """Percentages the user actually saw, in render order."""
    return [int(value) for value in re.findall(r"(\d+)%", stream.getvalue())]


def test_progress_bar_never_rewinds_when_derived_fraction_drops():
    """A smaller derived percent must not move the visible bar backwards."""
    stream = io.StringIO()
    with _progress_display(stream=stream) as on_progress:
        on_progress("extract", 0.10)
        on_progress("portrait", 0.09)  # derived fraction dropped after canon grew
        on_progress("pages 1/5", 0.26)
    percents = _percents(stream)
    assert all(b >= a for a, b in zip(percents, percents[1:], strict=False)), (
        f"bar rewound: {percents}"
    )
