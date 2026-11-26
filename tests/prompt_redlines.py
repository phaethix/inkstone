"""Shared redline checks for model-facing image prompts."""

from __future__ import annotations

import re

NEGATIVE_WORD_RE = re.compile(
    r"(?i)\b(?:no|not|never|avoid|without|unless|forbidden|nothing|do not|does not)\b"
)
AUDIT_QUOTE_RE = re.compile(r"(?i)\bsource:\s*")
CHROME_WORD_RE = re.compile(
    r"(?i)\b(?:caption|dialogue|sfx|lettering|speech bubble|thought bubble|"
    r"chrome|design sheet|model sheet|turnaround|line art|readable text|glyph|"
    r"pseudo-script|comic panel|text)\b"
)
EXACT_LETTERING_RE = re.compile(r"(?i)(?:CAPTION|DIALOGUE|SFX)\s*\(exact\)|^\s*lettering:")


def assert_positive_image_prompt(prompt: str) -> None:
    """Assert that an image prompt obeys the Agnes i2i positive-only redlines."""
    assert isinstance(prompt, str)
    assert prompt
    assert not NEGATIVE_WORD_RE.search(prompt), prompt
    assert not AUDIT_QUOTE_RE.search(prompt), prompt
    assert not CHROME_WORD_RE.search(prompt), prompt
    assert not EXACT_LETTERING_RE.search(prompt), prompt
    assert "source: 倚着栏杆" not in prompt
    assert "CAPTION (exact):" not in prompt
    assert "DIALOGUE (exact):" not in prompt
    assert "SFX (exact):" not in prompt
