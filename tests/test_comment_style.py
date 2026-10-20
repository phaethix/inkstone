"""Comment-style guard: no ``# --- section title ---`` banners.

A full line of dashes remains allowed as a visual separator. A dash run that
carries a title is not: use a plain ``# Section title`` comment instead.
"""

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("core", "web", "scripts", "tests", "utils", "examples")


def _banner_comment(comment: str) -> bool:
    body = comment[1:].strip()
    if not body or re.fullmatch(r"[-#\s]+", body):
        return False
    return bool(re.match(r"^-{3,}\s*\S", body))


def test_python_sources_have_no_section_banner_comments():
    offenders: list[str] = []
    for name in SOURCE_ROOTS:
        root = ROOT / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type != tokenize.COMMENT:
                    continue
                if _banner_comment(token.string):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{token.start[0]}: {token.string.strip()}"
                    )
    assert not offenders, (
        "use a plain '# Section title' comment instead of '# --- Section title ---':\n"
        + "\n".join(offenders)
    )
