"""tests/test_export.py — PDF (manga2pdf CLI) and webtoon (PIL) export."""

from unittest.mock import patch

import pytest

from core.comic.export import ExportEngine


def test_export_pdf_invokes_manga2pdf_cli(tmp_path):
    eng = ExportEngine()
    with patch("core.comic.export.subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        out = eng.export_pdf(tmp_path, out="comic.pdf")
    assert out == "comic.pdf"
    run.assert_called_once()
    cmd = run.call_args.args[0]
    assert cmd[0] == "manga2pdf"
    assert "-p" in cmd and "TwoPageRight" in cmd
    assert "-d" in cmd and "R2L" in cmd


def test_export_pdf_raises_on_nonzero_exit(tmp_path):
    eng = ExportEngine()
    with patch("core.comic.export.subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 1, "stderr": "boom"})()
        with pytest.raises(RuntimeError, match="manga2pdf failed"):
            eng.export_pdf(tmp_path)


def test_export_webtoon_stacks_images(tmp_path):
    from PIL import Image

    eng = ExportEngine()
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    Image.new("RGB", (100, 50), (10, 20, 30)).save(p1)
    Image.new("RGB", (100, 70), (40, 50, 60)).save(p2)

    out = eng.export_webtoon([str(p1), str(p2)], out=str(tmp_path / "webtoon.png"))
    img = Image.open(out)
    assert img.size == (100, 120)
