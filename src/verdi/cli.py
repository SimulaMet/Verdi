from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verdi", description="Verdi research testbed CLI")
    parser.parse_args(argv)
    print("verdi CLI scaffold — see docs/IMPLEMENTATION_PLAN.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
