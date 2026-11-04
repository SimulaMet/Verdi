from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ENV_TEMPLATE = """\
# Endpoint key (never commit this file). Model name and base_url live in each
# scenario's `model:` block.
MODEL_API_KEY=
"""


def examples_dir() -> Path:
    """Packaged examples (wheel install), else the repo's examples/ (dev checkout)."""
    packaged = Path(__file__).resolve().parent / "examples"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "examples"


def init(target: Path, *, force: bool = False) -> list[Path]:
    """Copy the starter, full and sweep examples into ``target/examples``."""
    src = examples_dir()
    files = sorted(p for p in src.rglob("*.yaml") if p.is_file())
    if not files:
        raise SystemExit(f"verdi-agents init: no examples found in {src}")
    dest_root = target / "examples"
    clashes = [dest_root / p.relative_to(src) for p in files
               if (dest_root / p.relative_to(src)).exists()]
    if clashes and not force:
        raise SystemExit("verdi-agents init: these files already exist (use --force to overwrite):\n  "
                         + "\n  ".join(str(c) for c in clashes))
    written = []
    for path in files:
        dest = dest_root / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        written.append(dest)
    env = target / ".env"
    if not env.exists():
        env.write_text(ENV_TEMPLATE, encoding="utf-8")
        written.append(env)
    return written


def _init_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="verdi-agents init",
        description="Create a folder with ready-to-run example scenarios and sweeps.")
    parser.add_argument("directory", nargs="?", default=".", help="where to create it (default: here)")
    parser.add_argument("--force", action="store_true", help="overwrite existing example files")
    args = parser.parse_args(argv)
    target = Path(args.directory)
    written = init(target, force=args.force)
    print(f"Created {len(written)} file(s) in {target.resolve()}:")
    for path in written:
        print(f"  {path}")
    where = "" if args.directory == "." else f"cd {args.directory}\n  "
    print(f"""
Next:
  {where}# 1. put your endpoint key in .env (MODEL_API_KEY=...)
  # 2. set base_url/name in examples/starter.yaml's `model:` block
  verdi-agents run examples/starter.yaml          # results: runs/starter/report.html
  verdi-agents sweep examples/sweeps/model_comparison.yaml --dry-run
(Not installed? Use uvx: uvx verdi-agents run ...)""")
    return 0


def _run_main(argv: list[str]) -> int:
    """`verdi-agents run <scenario>`: one simulation; run dir defaults to runs/<scenario name>."""
    from verdi_agents.society.live import main as live_main

    if argv and not argv[0].startswith("-"):
        scenario, rest = argv[0], argv[1:]
        argv = ["--scenario", scenario, *rest]
        if not any(a == "--run-dir" or a.startswith("--run-dir=") for a in rest):
            argv += ["--run-dir", str(Path("runs") / Path(scenario).stem)]
    return live_main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["sweep"]:
        from verdi_agents.experiments.sweep import main as sweep_main

        return sweep_main(argv[1:])
    if argv[:1] == ["init"]:
        return _init_main(argv[1:])
    if argv[:1] == ["run"]:
        return _run_main(argv[1:])
    parser = argparse.ArgumentParser(prog="verdi-agents", description="Verdi research testbed CLI",
                                     epilog="commands:\n"
                                            "  init    create a folder with example scenarios "
                                            "and sweeps\n"
                                            "  run     run one scenario "
                                            "(verdi-agents run examples/starter.yaml)\n"
                                            "  sweep   rerun a scenario many times, changing "
                                            "only what you list (verdi-agents sweep --help)",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    # No subcommand given: show usage (the old text pointed at a doc that
    # does not exist).
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
