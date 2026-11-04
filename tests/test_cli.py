import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_runs() -> None:
    env_python = sys.executable
    result = subprocess.run(
        [env_python, "-m", "verdi_agents.cli"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: verdi-agents" in result.stdout
    for command in ("init", "run", "sweep"):
        assert command in result.stdout
    assert "IMPLEMENTATION_PLAN" not in result.stdout


def test_run_respects_run_dir_equals_form(monkeypatch) -> None:
    import verdi_agents.cli as cli
    import verdi_agents.society.live as live

    seen: list[list[str]] = []
    monkeypatch.setattr(live, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["run", "examples/starter.yaml", "--run-dir=out/mine"]) == 0
    assert seen == [["--scenario", "examples/starter.yaml", "--run-dir=out/mine"]]
