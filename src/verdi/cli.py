from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["sweep"]:
        from verdi.experiments.sweep import main as sweep_main

        return sweep_main(argv[1:])
    parser = argparse.ArgumentParser(prog="verdi", description="Verdi research testbed CLI",
                                     epilog="commands:\n  sweep   rerun a scenario many "
                                            "times, changing only what you list "
                                            "(verdi sweep --help)",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    print("verdi CLI scaffold — see docs/IMPLEMENTATION_PLAN.md")
    print("Commands: verdi sweep <file.yaml>  (see docs/SWEEPS.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
