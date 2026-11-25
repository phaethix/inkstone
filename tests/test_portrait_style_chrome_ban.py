"""tests/test_portrait_style_chrome_ban.py — the shared portrait style must
declare a pure portrait before the model paints.

The Agnes text encoder reads nouns literally, so the portrait style uses
positive wording for a pure painted portrait and open-eye geometry. It avoids
chrome vocabulary that the model could paint into the reference image.
"""

from core.comic.consistency import DEFAULT_PORTRAIT_STYLE


def test_portrait_style_declares_a_pure_painted_portrait():
    """The style must positively declare a pure portrait and open eyes."""
    low = DEFAULT_PORTRAIT_STYLE.lower()
    assert "pure painted portrait" in low
    assert "fully open eyes" in low
    assert "clearly defined pupils and irises" in low
    assert "speech bubble" not in low
    assert "lettering" not in low
    assert "caption" not in low
    assert "watermark" not in low


def test_portrait_style_keeps_existing_locks():
    """Adding the chrome ban must not drop the earlier invariants."""
    low = DEFAULT_PORTRAIT_STYLE.lower()
    assert "empty hands" in low
    assert "upper body" in low
    assert "person alone" in low
