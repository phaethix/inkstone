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


def test_start_sh_loads_dotenv_when_the_api_key_is_already_exported():
    text = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert 'if [ -z "${AGNES_API_KEY:-}" ] && [ -f .env ]' not in text
    assert "inkstone_load_dotenv .env" in text
    assert 'source "$ROOT/.env"' not in text
    assert "source .env" not in text


def test_colab_run_parses_dotenv_instead_of_sourcing_it():
    text = (ROOT / "scripts" / "colab_run.sh").read_text(encoding="utf-8")
    assert 'source "$ROOT/.env"' not in text
    assert "set -a" not in text
    assert "inkstone_load_dotenv" in text
    assert "write_job_settings" in text


def test_preset_key_still_loads_the_rest_of_dotenv_without_executing_it(tmp_path):
    marker = tmp_path / "pwned"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"AGNES_API_KEY=$(touch {marker})\nINKSTONE_PAGE_SIZE=1024x1536\n",
        encoding="utf-8",
    )
    script = f"""
set -euo pipefail
export AGNES_API_KEY=preset
source "{LOADER}"
inkstone_load_dotenv "{env_file}"
printf '%s\\n' "$AGNES_API_KEY"
printf '%s\\n' "${{INKSTONE_PAGE_SIZE-}}"
"""
    env = os.environ.copy()
    env["AGNES_API_KEY"] = "preset"
    env.pop("INKSTONE_PAGE_SIZE", None)
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split("\n")
    assert lines[0] == "preset"
    assert lines[1] == "1024x1536"
    assert not marker.exists()
