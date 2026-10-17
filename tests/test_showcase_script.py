"""The showcase script reads a stored project when one is present."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_showcase.sh"


def _fake_inkstone(bin_dir: Path) -> None:
    tool = bin_dir / "inkstone"
    tool.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$INKSTONE_FAKE_LOG"\n',
        encoding="utf-8",
    )
    tool.chmod(0o755)


def test_showcase_plans_from_the_experience_rate_without_a_project(tmp_path):
    log = tmp_path / "log.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_inkstone(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["INKSTONE_FAKE_LOG"] = str(log)
    env["INKSTONE_SHOWCASE_OUT"] = str(tmp_path / "missing")
    subprocess.run([str(SCRIPT)], check=True, env=env, cwd=REPO_ROOT)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "plan --book "
        f"{REPO_ROOT / 'examples/showcase/journey-west-ch1/source.txt'} "
        "--density B --format page"
    ]


def test_showcase_uses_a_stored_project_for_plan_and_structure(tmp_path):
    project = tmp_path / "journey"
    project.mkdir()
    (project / "state.json").write_text("{}", encoding="utf-8")
    log = tmp_path / "log.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_inkstone(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["INKSTONE_FAKE_LOG"] = str(log)
    env["INKSTONE_SHOWCASE_OUT"] = str(project)
    subprocess.run([str(SCRIPT)], check=True, env=env, cwd=REPO_ROOT)
    lines = log.read_text(encoding="utf-8").splitlines()
    book = REPO_ROOT / "examples/showcase/journey-west-ch1/source.txt"
    assert lines == [
        f"plan --book {book} --density B --format page --out {project}",
        f"structure --out {project}",
    ]
