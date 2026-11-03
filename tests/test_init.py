"""`verdi init` copies the shipped examples into a working folder."""
from __future__ import annotations

import pytest

from verdi.cli import init, main


def test_init_creates_examples_and_env(tmp_path, capsys):
    assert main(["init", str(tmp_path / "study")]) == 0
    root = tmp_path / "study"
    for rel in ("examples/starter.yaml", "examples/full.yaml",
                "examples/sweeps/model_comparison.yaml", ".env"):
        assert (root / rel).is_file(), rel
    assert "MODEL_API_KEY=" in (root / ".env").read_text()
    assert "Next:" in capsys.readouterr().out


def test_init_refuses_to_overwrite_and_keeps_env(tmp_path):
    init(tmp_path)
    (tmp_path / ".env").write_text("MODEL_API_KEY=mine\n")
    with pytest.raises(SystemExit, match="already exist"):
        init(tmp_path)
    init(tmp_path, force=True)
    assert (tmp_path / ".env").read_text() == "MODEL_API_KEY=mine\n"


def test_initialized_sweep_validates_from_its_folder(tmp_path, monkeypatch, capsys):
    from verdi.experiments import sweep as sw

    init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VERDI_DOTENV_PATH", str(tmp_path / "no.env"))
    monkeypatch.setattr(sw, "preflight", lambda runs: [])
    assert main(["sweep", "examples/sweeps/model_comparison.yaml", "--dry-run"]) == 0
    assert "scenario(s) valid" in capsys.readouterr().out


def test_dotenv_in_current_folder_is_loaded(tmp_path, monkeypatch):
    from verdi.experiments.g5_judge_live import _load_dotenv_into_environ

    (tmp_path / ".env").write_text("VERDI_TEST_CWD_KEY=from-cwd\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VERDI_DOTENV_PATH", raising=False)
    monkeypatch.delenv("VERDI_TEST_CWD_KEY", raising=False)
    _load_dotenv_into_environ()
    import os
    assert os.environ.get("VERDI_TEST_CWD_KEY") == "from-cwd"
    monkeypatch.delenv("VERDI_TEST_CWD_KEY")


def test_verdi_run_defaults_run_dir(monkeypatch):
    import verdi.society.live as live
    from verdi.cli import main as cli_main

    seen = {}
    monkeypatch.setattr(live, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    cli_main(["run", "examples/starter.yaml", "--seed", "7"])
    assert seen["argv"] == ["--scenario", "examples/starter.yaml", "--seed", "7",
                            "--run-dir", "runs/starter"]
