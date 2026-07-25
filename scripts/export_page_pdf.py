#!/usr/bin/env python3
"""Rebuild flip-page PDF from an existing comic project (no image regeneration).

Use when a run finished as ``--format webtoon`` but you want a Preview-friendly
multi-page PDF instead. Reads ``state.json`` + ``panels/``, writes ``page_NN.png``
and ``comic.pdf`` under the project directory.

Colab downloads often keep remote absolute paths in ``state.json``
(``/content/inkstone/comic_out/...``); this script remaps them to local
``panels/<basename>``.

Usage (from repo root):
    python scripts/export_page_pdf.py comic_out/three-body
    python scripts/export_page_pdf.py --project three-body
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

# Allow ``python scripts/...`` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.comic.export import ExportEngine
from core.comic.layout import LayoutEngine, PanelImage
from core.pipelines.creative_comic import _ordered_generated_panels
from core.schemas import ProjectState


def _resolve_project(path: Path | None, project: str | None, root: Path) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    if project:
        return (root / "comic_out" / project).resolve()
    raise SystemExit("pass a project directory or --project <id>")


def _local_panel_path(project_dir: Path, remote_or_local: str) -> Path | None:
    """Map state panel path to a file under ``project_dir/panels``."""
    raw = Path(remote_or_local)
    candidates = [
        raw if raw.is_file() else None,
        project_dir / "panels" / raw.name,
        project_dir / raw.name,
    ]
    for cand in candidates:
        if cand is not None and cand.is_file():
            return cand
    return None


def rebuild_page_pdf(project_dir: Path, *, keep_webtoon: bool = True) -> Path:
    state_path = project_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"missing {state_path}")

    state = ProjectState.load(state_path)
    pages_dir = project_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # Drop previous page sheets so stale page_99.png cannot linger; keep webtoon
    # strip unless the user asks to remove it.
    for old in pages_dir.glob("page_*.png"):
        old.unlink()
    if not keep_webtoon:
        webtoon = pages_dir / "webtoon.png"
        if webtoon.is_file():
            webtoon.unlink()

    panel_imgs: list[PanelImage] = []
    missing = 0
    for state_key, generated in _ordered_generated_panels(state):
        local = _local_panel_path(project_dir, generated.local)
        if local is None:
            missing += 1
            print(f"  skip missing panel {state_key}: {generated.local}", file=sys.stderr)
            continue
        panel_imgs.append(PanelImage(Image.open(local), dialogue=generated.dialogue))

    if not panel_imgs:
        raise RuntimeError(f"no panel images found under {project_dir / 'panels'}")

    print(f"composing {len(panel_imgs)} panel(s) into page grid"
          + (f" ({missing} missing)" if missing else "") + "...")
    page_paths = LayoutEngine().compose(panel_imgs, pages_dir, layout_mode="page")
    print(f"wrote {len(page_paths)} page image(s) under {pages_dir}")

    pdf_path = project_dir / "comic.pdf"
    # Prefer page_*.png only — never fold a leftover webtoon.png into the PDF.
    out = ExportEngine().export_pdf(pages_dir, out=str(pdf_path))
    size_mb = Path(out).stat().st_size / (1024 * 1024)
    print(f"PDF -> {out} ({size_mb:.1f} MB)")
    return Path(out)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_dir",
        nargs="?",
        type=Path,
        help="path to comic_out/<id> (alternative: --project)",
    )
    parser.add_argument(
        "--project",
        help="project id under comic_out/ (e.g. three-body)",
    )
    parser.add_argument(
        "--remove-webtoon",
        action="store_true",
        help="delete pages/webtoon.png after rebuilding (saves disk)",
    )
    args = parser.parse_args()
    project_dir = _resolve_project(args.project_dir, args.project, root)
    if not project_dir.is_dir():
        raise SystemExit(f"project directory not found: {project_dir}")

    rebuild_page_pdf(project_dir, keep_webtoon=not args.remove_webtoon)


if __name__ == "__main__":
    main()
