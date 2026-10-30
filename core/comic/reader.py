"""Browser reader for page images already stored under a project.

The page list is the files on disk. When ``state.json`` names a plan, only
those pages are listed: a file the plan dropped is not a page of this book.
Writing ``reader.html`` does not call a model and does not decide that a
chapter is validated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.schemas import ProjectState

_CHUNK_RE = re.compile(r"^page_c(\d{4})_p\d{4}\.png$", re.IGNORECASE)


def _project_state(pages_dir: Path) -> ProjectState | None:
    path = pages_dir.parent / "state.json"
    if not path.is_file():
        return None
    try:
        return ProjectState.load(path)
    except Exception:  # noqa: BLE001 — a reader with no plan lists the files
        return None


def page_images(pages_dir: Path) -> list[Path]:
    """Page images in filename order. A webtoon strip is not a page."""
    state = _project_state(pages_dir)
    if state is not None and state.render_mode == "finished_page":
        from core.pipelines.resume import finished_plan_files

        return finished_plan_files(state, pages_dir)
    pages = sorted(pages_dir.glob("page_*.png"))
    if state is not None and state.render_mode == "panel_compose":
        return [path for path in pages if _CHUNK_RE.match(path.name) is None]
    if not pages:
        pages = sorted(
            path for path in pages_dir.glob("*.png") if path.name.lower() != "webtoon.png"
        )
    return pages


def chapter_pages(pages_dir: Path) -> list[tuple[str, list[Path]]]:
    """Group stored pages by the chunk id in ``page_cNNNN_pMMMM.png``."""
    groups: list[tuple[str, list[Path]]] = []
    for path in page_images(pages_dir):
        match = _CHUNK_RE.match(path.name)
        label = f"c{match.group(1)}" if match else "pages"
        if groups and groups[-1][0] == label:
            groups[-1][1].append(path)
        else:
            groups.append((label, [path]))
    return groups


def write_reader(project_dir: Path) -> Path:
    """Write ``reader.html`` beside ``pages/``. Missing pages raise."""
    project_dir = Path(project_dir)
    pages_dir = project_dir / "pages"
    groups = chapter_pages(pages_dir)
    if not groups:
        raise RuntimeError(f"no page images found in {pages_dir}")
    relative = [
        (label, [path.relative_to(project_dir).as_posix() for path in paths])
        for label, paths in groups
    ]
    out = project_dir / "reader.html"
    out.write_text(_document(relative), encoding="utf-8")
    return out


def _document(groups: list[tuple[str, list[str]]]) -> str:
    pages = [name for _label, names in groups for name in names]
    chapters: list[dict[str, int | str]] = []
    start = 0
    for label, names in groups:
        chapters.append({"id": label, "start": start})
        start += len(names)
    payload = json.dumps({"pages": pages, "chapters": chapters}, ensure_ascii=False)
    payload = payload.replace("<", r"\u003c")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Inkstone reader</title>
<style>
body {{ margin: 0; background: #111; color: #eee; font-family: sans-serif; }}
nav {{ display: flex; gap: 8px; flex-wrap: wrap; padding: 8px; }}
button {{ background: #222; color: #eee; border: 1px solid #444; padding: 6px 10px; }}
#count {{ text-align: center; margin: 8px; }}
img {{ max-height: 88vh; max-width: 100%; display: block; margin: 0 auto; }}
</style>
</head>
<body>
<nav id="chapters"></nav>
<p id="count"></p>
<img id="page" alt="">
<script>
const book = {payload};
let index = 0;
const img = document.getElementById("page");
const nav = document.getElementById("chapters");
function show() {{
  img.src = book.pages[index];
  img.alt = book.pages[index];
  document.getElementById("count").textContent = (index + 1) + " / " + book.pages.length;
}}
for (const chapter of book.chapters) {{
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = chapter.id;
  button.addEventListener("click", () => {{ index = chapter.start; show(); }});
  nav.appendChild(button);
}}
document.addEventListener("keydown", (event) => {{
  if (event.key === "ArrowRight") index = Math.min(book.pages.length - 1, index + 1);
  if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
  show();
}});
show();
</script>
</body>
</html>
"""
