import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_runs() -> None:
    env_python = sys.executable
    result = subprocess.run(
        [env_python, "-m", "verdi.cli"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "verdi CLI scaffold" in result.stdout
