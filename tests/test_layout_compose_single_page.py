"""tests/test_layout_compose_single_page.py — LayoutEngine.compose_single_page."""

from PIL import Image

from core.comic.layout import LayoutEngine, PanelImage


def test_compose_single_page_places_all_panels_on_one_canvas():
    engine = LayoutEngine(page_width=800, cell_height=600)
    panels = [
        PanelImage(Image.new("RGB", (300, 300), (255, 0, 0))),
        PanelImage(
            Image.new("RGB", (300, 300), (0, 255, 0)),
            dialogue="你好。",
            caption="旁白",
        ),
    ]
    canvas = engine.compose_single_page(panels)
    assert canvas.size[0] == 800
    assert canvas.size[1] >= 600
