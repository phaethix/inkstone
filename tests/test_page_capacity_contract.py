"""The packing limit is one fact: the estimator and the engine must agree.

``core.density`` turns panels into pages and ``LayoutEngine`` actually packs
them. Both need the same number, and a page count the estimator reports has to
be a page count the engine can produce. When that number is written down twice,
changing one copy leaves the other silently wrong: the estimate stays cheap and
plausible while every page, cost, and duration figure it prints drifts from what
the run will really do.
"""

from math import ceil

from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage
from core.config import DEFAULT_PANELS_PER_PAGE
from core.density import PANELS_PER_PAGE, estimate


def _img(w=40, h=40, color=(200, 200, 200)):
    return Image.new("RGB", (w, h), color)


def test_the_estimator_uses_the_shared_packing_limit():
    assert PANELS_PER_PAGE == DEFAULT_PANELS_PER_PAGE


def test_the_engine_packs_exactly_the_shared_limit_onto_a_page(tmp_path):
    """One more panel than the limit has to become a second page.

    A literal inside ``_compose_pages`` would keep packing by its own number and
    the estimator's page count would never describe this output.
    """
    panels = [PanelImage(_img()) for _ in range(DEFAULT_PANELS_PER_PAGE)]
    paths = LayoutEngine().compose(panels, tmp_path, layout_mode="page")
    assert len(paths) == 1

    over = [PanelImage(_img()) for _ in range(DEFAULT_PANELS_PER_PAGE + 1)]
    paths_over = LayoutEngine().compose(over, tmp_path / "over", layout_mode="page")
    assert len(paths_over) == 2


def test_paginate_defaults_to_the_shared_limit():
    """The default argument is the other place a literal can hide."""
    panels = [PanelImage(_img()) for _ in range(DEFAULT_PANELS_PER_PAGE + 1)]
    pages = LayoutEngine._paginate(panels)
    assert len(pages) == 2
    assert len(pages[0]) == DEFAULT_PANELS_PER_PAGE


def test_the_estimate_reports_pages_the_engine_could_actually_pack(tmp_path):
    """The page count the user is quoted must be reachable by the engine.

    The estimate is free and the layout is not, so a wrong estimate is only
    discovered after the pages are rendered. This ties the quoted number to the
    engine rather than to a copy of the constant.
    """
    book = tmp_path / "book.txt"
    book.write_text("第一章\n方鸿渐在甲板上。", encoding="utf-8")
    est = estimate(book, density="B")
    panels = [PanelImage(_img()) for _ in range(est.panels)]
    paths = LayoutEngine().compose(panels, tmp_path / "pages", layout_mode="page")
    assert len(paths) == est.pages
    assert est.pages == ceil(est.panels / DEFAULT_PANELS_PER_PAGE)
