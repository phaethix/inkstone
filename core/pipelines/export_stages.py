"""core.pipelines.export_stages — the export and layout stages, cut out of the pipeline.

These four stages share one shape: turn what is on disk into a bound artifact, and
let ``run_stage`` decide whether the same bytes were already produced. They were
inline in ``creative_comic.py``, where they made up a third of the file without
touching the run state at all.

The stage-source hashes live here because they are the input to these keys. They
are ``stage_source_hash`` over the drawing code in ``core.comic.layout``,
``core.comic.export`` and ``core.comic.page_lettering`` — never over functions in
this file — so moving this module does not move a stored key.

``creative_comic`` re-exports the four constants for
``core.comic.migrate._STAGE_SRC_CONSTANTS``, which reads them off that module by
name. That is why they are imported there with an explicit ``noqa``.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from core.comic.cas import (
    export_action_key,
    export_inputs,
    layout_action_key,
    webtoon_action_key,
)
from core.comic.env_snapshot import h_env
from core.comic.export import (
    ExportEngine,
    _comic_info_xml,
    _img2pdf_layout,
    _pdf_dpi,
)
from core.comic.fonts import text_requires_cjk
from core.comic.layout import LayoutEngine, PanelImage, _line_height
from core.comic.stage_contract import stage_source_hash
from core.pipelines.stage_runner import run_stage

# Every drawing decision a page or a strip depends on. The hash of these is the
# stage identity; the layout engine's packing limits are part of it because a
# different pagination means different bytes.
_DRAW_SOURCES = (
    LayoutEngine._bubble_height,
    LayoutEngine._caption_block_height,
    LayoutEngine._draw_bubble,
    LayoutEngine._draw_caption,
    LayoutEngine._fitted_caption,
    LayoutEngine._draw_sfx,
    LayoutEngine._rebreak_for_punctuation,
    LayoutEngine._hang_date_mark,
    LayoutEngine._hang_duration,
    LayoutEngine._hang_operator,
    LayoutEngine._hang_spaced_unit,
    LayoutEngine._wrap_clock,
    LayoutEngine._wrap_grouped_number,
    LayoutEngine._wrap_number_unit,
    LayoutEngine._wrap_date,
    LayoutEngine._wrap_numeric_expression,
    LayoutEngine._wrap_range,
    LayoutEngine._wrap_chars,
    LayoutEngine._wrap_mixed,
    LayoutEngine._wrap_text,
    LayoutEngine._wrap_words,
    _line_height,
    text_requires_cjk,
)

_LAYOUT_SOURCES = (
    *_DRAW_SOURCES,
    LayoutEngine._cols_for,
    LayoutEngine._compose_pages,
    LayoutEngine._lettering_reserve,
    LayoutEngine._paginate,
    LayoutEngine._place_panel,
    LayoutEngine._render_page,
)
_WEBTOON_SOURCES = (
    *_DRAW_SOURCES,
    LayoutEngine._compose_webtoon,
    LayoutEngine._panel_strip_height,
)
_EXPORT_SOURCES = (
    ExportEngine._export_pdf_pil_batched,
    ExportEngine._merge_pdf_parts,
    ExportEngine._save_pil_batch,
    ExportEngine._try_img2pdf,
    ExportEngine.export_pdf,
    _img2pdf_layout,
    _pdf_dpi,
)
_EXPORT_STAGE_SRC = stage_source_hash(*_EXPORT_SOURCES)
# Kept off the PDF hash. A CBZ is a different binder, and adding it must not
# change the key of a PDF that was already stored.
_CBZ_STAGE_SRC = stage_source_hash(ExportEngine.export_cbz, _comic_info_xml)
_WEBTOON_STAGE_SRC = stage_source_hash(*_WEBTOON_SOURCES)
_LAYOUT_STAGE_SRC = stage_source_hash(*_LAYOUT_SOURCES)


def _bound_image_size(blobs: list[bytes]) -> str | None:
    """Pixel sizes of the images a binder consumes, as ``WIDTHxHEIGHT``.

    One size shared by every image stays a single string, so a book drawn at
    the configured size keeps the key already stored. Mixed images join their
    sizes in bind order.
    """
    sizes: list[str] = []
    for blob in blobs:
        with Image.open(io.BytesIO(blob)) as image:
            sizes.append(f"{image.size[0]}x{image.size[1]}")
    if not sizes:
        return None
    if len(set(sizes)) == 1:
        return sizes[0]
    return ",".join(sizes)


def _directory_page_files(pages_dir: Path) -> list[Path]:
    """Every page image a binder would glob, in filename order."""
    files = sorted(pages_dir.glob("page_*.png"))
    if not files:
        files = sorted(p for p in pages_dir.glob("*.png") if p.name.lower() != "webtoon.png")
    return files


@contextmanager
def _bind_directory(pages_dir: Path, files: list[Path]) -> Iterator[Path]:
    """Directory whose ``page_*.png`` glob is exactly ``files``.

    When that is already ``pages_dir``, bind it in place. Otherwise stage
    copies so a dropped page still sitting in the project is not zipped in.
    """
    selected = [path.resolve() for path in files]
    on_disk = [path.resolve() for path in _directory_page_files(pages_dir)]
    if selected == on_disk:
        yield pages_dir
        return
    staging = Path(tempfile.mkdtemp(prefix="inkstone-export-"))
    try:
        for path in files:
            (staging / path.name).write_bytes(path.read_bytes())
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def export_pdf_with_manifest(
    pages_dir: Path, output_dir: Path, files: list[Path] | None = None
) -> str:
    """Build ``comic.pdf``, or copy it when the same pages were already bound."""
    out = output_dir / "comic.pdf"
    chosen = list(files) if files is not None else _directory_page_files(pages_dir)
    blobs = [path.read_bytes() for path in chosen]
    env = h_env(text="", resolved_size=_bound_image_size(blobs), scope="export")
    key = export_action_key(
        pages=blobs,
        layout="TwoPageRight",
        direction="R2L",
        env=env,
        stage_src=_EXPORT_STAGE_SRC,
    )

    def _bind() -> list[tuple[str, bytes]]:
        with _bind_directory(pages_dir, chosen) as bind_dir:
            pdf = ExportEngine().export_pdf(bind_dir, out=str(out))
        return [(out.name, Path(pdf).read_bytes())]

    run = run_stage(
        output_dir,
        "export",
        key,
        _bind,
        stage_src=_EXPORT_STAGE_SRC,
        env=env,
        inputs=export_inputs(blobs),
    )
    if run.tombstoned or not run.files:
        return str(out)
    if run.replayed:
        out.write_bytes(run.files[0][1])
    return str(out)


def export_cbz_with_manifest(
    pages_dir: Path, output_dir: Path, files: list[Path] | None = None
) -> str:
    """Build ``comic.cbz``, or copy it when the same pages were already bound."""
    out = output_dir / "comic.cbz"
    chosen = list(files) if files is not None else _directory_page_files(pages_dir)
    blobs = [path.read_bytes() for path in chosen]
    env = h_env(text="", resolved_size=_bound_image_size(blobs), scope="export")
    key = export_action_key(
        pages=blobs,
        layout="cbz",
        direction="R2L",
        env=env,
        stage_src=_CBZ_STAGE_SRC,
    )

    def _bind() -> list[tuple[str, bytes]]:
        with _bind_directory(pages_dir, chosen) as bind_dir:
            cbz = ExportEngine().export_cbz(bind_dir, out=str(out))
        return [(out.name, Path(cbz).read_bytes())]

    run = run_stage(
        output_dir,
        "export",
        key,
        _bind,
        stage_src=_CBZ_STAGE_SRC,
        env=env,
        inputs=export_inputs(blobs),
    )
    if run.tombstoned or not run.files:
        return str(out)
    if run.replayed:
        out.write_bytes(run.files[0][1])
    return str(out)


def _panel_png_bytes(panel: PanelImage) -> bytes:
    buffer = io.BytesIO()
    panel.image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _webtoon_lettering(panels: list[PanelImage]) -> list[dict[str, str]]:
    return [
        {
            "caption": panel.caption or "",
            "dialogue": panel.dialogue or "",
            "sfx": panel.sfx or "",
        }
        for panel in panels
    ]


def export_webtoon_with_manifest(
    panels: list[PanelImage], pages_dir: Path, output_dir: Path
) -> list[str]:
    """Stack a webtoon strip, or copy it when the same panels were already bound."""
    if not panels:
        return []
    out = pages_dir / "webtoon.png"
    blobs = [_panel_png_bytes(panel) for panel in panels]
    lettering = _webtoon_lettering(panels)
    text = "\n".join(piece for row in lettering for piece in row.values() if piece)
    env = h_env(text=text, resolved_size=_bound_image_size(blobs), scope="webtoon")
    engine = LayoutEngine()
    key = webtoon_action_key(
        pages=blobs,
        lettering=lettering,
        page_width=engine.page_width,
        env=env,
        stage_src=_WEBTOON_STAGE_SRC,
    )

    def _stack() -> list[tuple[str, bytes]]:
        pages_dir.mkdir(parents=True, exist_ok=True)
        paths = engine.compose(panels, pages_dir, layout_mode="webtoon")
        return [(Path(paths[0]).name, Path(paths[0]).read_bytes())] if paths else []

    run = run_stage(
        output_dir,
        "webtoon",
        key,
        _stack,
        stage_src=_WEBTOON_STAGE_SRC,
        env=env,
        inputs=export_inputs(blobs),
    )
    if run.tombstoned:
        return [str(out)]
    if not run.files:
        return []
    if run.replayed:
        pages_dir.mkdir(parents=True, exist_ok=True)
        for name, data in run.files:
            (pages_dir / Path(name).name).write_bytes(data)
    return [str(out)]


def drop_stale_panel_sheets(pages_dir: Path, keep: list[str]) -> None:
    """Remove panel sheets the current collage did not write.

    Finished-page files (``page_c*_p*.png``) stay. A shorter collage must not
    leave ``page_03.png`` behind for the PDF binder to pick up.
    """
    kept = {Path(path).name for path in keep}
    if not pages_dir.is_dir():
        return
    for path in pages_dir.glob("page_*.png"):
        if path.match("page_c*_p*.png") or path.name in kept:
            continue
        path.unlink()


def layout_pages_with_manifest(
    panels: list[PanelImage], pages_dir: Path, output_dir: Path
) -> list[str] | None:
    """Collage panel sheets, or copy them when the same panels were already bound.

    ``None`` means a non-ok manifest: the collage is not rebuilt, and the caller
    should not bind a PDF from whatever sheets happen to be on disk.
    """
    if not panels:
        return []
    blobs = [_panel_png_bytes(panel) for panel in panels]
    lettering = _webtoon_lettering(panels)
    text = "\n".join(piece for row in lettering for piece in row.values() if piece)
    env = h_env(text=text, resolved_size=_bound_image_size(blobs), scope="layout")
    engine = LayoutEngine()
    key = layout_action_key(
        pages=blobs,
        lettering=lettering,
        page_width=engine.page_width,
        cell_height=engine.cell_height,
        bg=[int(channel) for channel in engine.bg],
        env=env,
        stage_src=_LAYOUT_STAGE_SRC,
    )

    def _compose() -> list[tuple[str, bytes]]:
        # Report an empty collage instead of recording an ``ok`` manifest whose
        # ``outputs`` is empty: that would be a hit no reader can satisfy.
        pages_dir.mkdir(parents=True, exist_ok=True)
        paths = engine.compose(panels, pages_dir, layout_mode="page")
        return [(Path(path).name, Path(path).read_bytes()) for path in paths]

    run = run_stage(
        output_dir,
        "layout",
        key,
        _compose,
        stage_src=_LAYOUT_STAGE_SRC,
        env=env,
        inputs=export_inputs(blobs),
    )
    if run.tombstoned:
        return None
    if not run.files:
        # Nothing was composed and nothing was replayed. Returning here keeps an
        # empty ``keep`` list from deleting the sheets a previous run wrote.
        return []
    pages_dir.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    for name, data in run.files:
        dest = pages_dir / Path(name).name
        if run.replayed:
            dest.write_bytes(data)
        restored.append(str(dest))
    drop_stale_panel_sheets(pages_dir, restored)
    return restored


def finished_page_files(pages_dir: Path) -> list[Path]:
    """Return finished-page assets only, excluding panel-compose ``page_NN.png`` leftovers."""
    return sorted(p for p in pages_dir.glob("page_*.png") if p.match("page_c*_p*.png"))


def prepare_finished_page_export(pages_dir: Path, state=None) -> list[Path]:
    """Collect finished-page assets and drop stale panel-compose ``page_NN.png`` files.

    With a project state, the list is the current plan (skipped pages omitted).
    Without one, every ``page_c*_p*.png`` is collected, which is what older
    callers did.
    """
    if state is None:
        finished = finished_page_files(pages_dir)
    else:
        from core.pipelines.resume import finished_plan_files

        finished = finished_plan_files(state, pages_dir)
    for stale in pages_dir.glob("page_*.png"):
        if not stale.match("page_c*_p*.png"):
            stale.unlink()
    return finished
