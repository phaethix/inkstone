"""The exported book must be art-only (no deferred lettering bubbles).

Regression: ``prepare_finished_page_export`` used to return the lettered
``pages/page_c*_p*.png`` files, so every regeneration put speech bubbles back
into ``comic.pdf``. A manual re-export from ``pages/blank/`` fixed one PDF but
was overwritten by the next ``--fresh`` run. The binder is now pointed at the
clean blank art by default.
"""

from core.pipelines.export_stages import prepare_finished_page_export


def test_export_uses_blank_art_not_the_lettered_page(tmp_path):
    pages = tmp_path / "pages"
    (pages / "blank").mkdir(parents=True)
    lettered = pages / "page_c0000_p0000.png"
    blank = pages / "blank" / "page_c0000_p0000.png"
    lettered.write_bytes(b"lettered-with-bubbles")
    blank.write_bytes(b"clean-art-only")
    stale = pages / "page_01.png"
    stale.write_bytes(b"stale-panelcompose-sheet")

    files = prepare_finished_page_export(pages)

    assert files == [blank]
    assert blank.read_bytes() == b"clean-art-only"
    assert lettered.exists()
    assert not stale.exists()


def test_export_falls_back_to_lettered_when_blank_is_missing(tmp_path):
    """An older project with no blank/ must not lose the page entirely."""
    pages = tmp_path / "pages"
    pages.mkdir()
    lettered = pages / "page_c0000_p0000.png"
    lettered.write_bytes(b"lettered")

    files = prepare_finished_page_export(pages)

    assert files == [lettered]
