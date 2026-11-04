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
    for command in ("init", "run", "sweep", "pair", "compare"):
        assert command in result.stdout
    assert "IMPLEMENTATION_PLAN" not in result.stdout


def test_run_respects_run_dir_equals_form(monkeypatch) -> None:
    import verdi_agents.cli as cli
    import verdi_agents.society.live as live

    seen: list[list[str]] = []
    monkeypatch.setattr(live, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["run", "examples/starter.yaml", "--run-dir=out/mine"]) == 0
    assert seen == [["--scenario", "examples/starter.yaml", "--run-dir=out/mine"]]


def test_pair_and_compare_accept_a_positional_path(monkeypatch) -> None:
    import verdi_agents.analysis.outcome_metrics as outcome_metrics
    import verdi_agents.cli as cli
    import verdi_agents.experiments.paired_run as paired_run

    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(paired_run, "main", lambda argv: seen.append(("pair", argv)) or 0)
    monkeypatch.setattr(outcome_metrics, "main", lambda argv: seen.append(("compare", argv)) or 0)
    assert cli.main(["pair", "examples/paired.yaml", "--seeds", "1,2"]) == 0
    assert cli.main(["compare", "runs/p"]) == 0
    assert cli.main(["compare", "--pair-root", "runs/q"]) == 0
    assert seen == [
        ("pair", ["--config", "examples/paired.yaml", "--seeds", "1,2"]),
        ("compare", ["--pair-root", "runs/p"]),
        ("compare", ["--pair-root", "runs/q"]),
    ]
