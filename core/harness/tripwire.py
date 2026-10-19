"""core.harness.tripwire — keep measurement out of generative prompts.

A prompt must describe the work, not how the work is scored. Structure checks
and gate decisions are review evidence; if they enter a prompt, the model can
optimise the metric instead of the comic. The scan covers the system prompt and
the tool schemas only: a novel may legitimately contain the phrase "acceptance
rate", and rejecting a user's source text would be worse than the leak.
"""

from __future__ import annotations

import json

from core.harness.spec import FORBIDDEN_MARKERS, HarnessError


def assert_no_quality_verdicts(*, system_text: str, tools=()) -> None:
    """Raise when the harness half of a request carries a measurement marker."""
    haystack = (system_text or "") + "\n" + json.dumps(list(tools), ensure_ascii=False)
    low = haystack.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker in low:
            raise HarnessError(
                f"quality marker {marker!r} would enter a generative prompt; "
                "keep structure/gate evidence in the review packet, not the request"
            )
