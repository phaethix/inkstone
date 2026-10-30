"""scripts/start.sh must not execute .env as shell."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "scripts" / "dotenv.sh"


def _load(env_file: Path, inherited: dict[str, str] | None = None) -> tuple[str, str, int]:
    script = f"""
set -euo pipefail
source "{LOADER}"
inkstone_load_dotenv "{env_file}"
printf '%s' "${{AGNES_API_KEY-}}"
printf '\\n'
printf '%s' "${{OTHER_KEY-}}"
"""
    env = os.environ.copy()
    env.pop("AGNES_API_KEY", None)
    env.pop("OTHER_KEY", None)
    if inherited:
        env.update(inherited)
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = result.stdout.split("\n")
    key = lines[0] if lines else ""
    other = lines[1] if len(lines) > 1 else ""
    return key, other, result.returncode


def test_dotenv_loader_does_not_execute_command_substitution(tmp_path):
    marker = tmp_path / "pwned"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"AGNES_API_KEY=$(touch {marker})",
                f"OTHER_KEY=`touch {marker}`",
                'export QUOTED="already-set"',
            ]
        ),
        encoding="utf-8",
    )
    key, other, code = _load(env_file)
    assert code == 0
    assert not marker.exists()
    assert key == f"$(touch {marker})"
    assert other == f"`touch {marker}`"


def test_dotenv_loader_does_not_override_existing_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AGNES_API_KEY=from-file\nOTHER_KEY=from-file\n", encoding="utf-8")
    key, other, code = _load(env_file, {"AGNES_API_KEY": "already"})
    assert code == 0
    assert key == "already"
    assert other == "from-file"
