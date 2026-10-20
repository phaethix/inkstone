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


def test_section_comments_are_attached_to_the_following_definition():
    """A top-level comment introducing a def/class has no blank line between.

    Full-dash separator lines are allowed inside the comment block (for
    example ``# ---`` / ``# Title`` / ``# ---``); only a genuinely blank line
    between the block and the definition is a violation.
    """
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
            lines = text.splitlines()
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
            for index, token in enumerate(tokens):
                if token.type != tokenize.COMMENT or token.start[1] != 0:
                    continue
                source_line = lines[token.start[0] - 1]
                if not source_line.lstrip().startswith("#"):
                    continue
                body = token.string[1:].strip()
                if body and set(body) <= set("- #"):
                    continue
                nxt = index + 1
                while nxt < len(tokens) and tokens[nxt].type in (
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.COMMENT,
                ):
                    nxt += 1
                if nxt >= len(tokens):
                    continue
                following = tokens[nxt]
                starts_definition = following.string in ("def", "class", "@")
                if following.string == "async":
                    later = nxt + 1
                    while later < len(tokens) and tokens[later].type in (
                        tokenize.NL,
                        tokenize.NEWLINE,
                    ):
                        later += 1
                    starts_definition = later < len(tokens) and tokens[later].string == "def"
                if not starts_definition or following.start[0] <= token.end[0]:
                    continue
                between = lines[token.end[0] : following.start[0] - 1]
                if any(not line.strip() for line in between):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{token.start[0]}: {token.string.strip()}"
                    )
    assert not offenders, (
        "attach a section comment directly to the def/class it introduces "
        "(no blank line between):\n" + "\n".join(offenders)
    )
