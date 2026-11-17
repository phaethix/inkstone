"""tests/test_portrait_style_chrome_ban.py — the shared portrait style must
declare chrome-free output before the model paints.

The boy's portrait invented a speech bubble on its own (the source text and the
portrait prompt never contained the line). Declaring chrome-free output in
DEFAULT_PORTRAIT_STYLE is the cheapest layer: it stops the bubble at portrait
generation time instead of relying on the neutralizer to catch every case.
Option A of the 2026-11-12 decision.
"""

from core.comic.consistency import DEFAULT_PORTRAIT_STYLE


def test_portrait_style_declares_no_lettering():
    """The style must positively declare a chrome-free portrait."""
    low = DEFAULT_PORTRAIT_STYLE.lower()
    assert "no speech bubble" in low
    assert "no text" in low


def test_portrait_style_keeps_existing_locks():
    """Adding the chrome ban must not drop the earlier invariants."""
    low = DEFAULT_PORTRAIT_STYLE.lower()
    assert "empty hands" in low
    assert "upper body" in low
    assert "person alone" in low
