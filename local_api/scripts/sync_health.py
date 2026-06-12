"""Phase 38 — CLI entry for read-only background sync health checks."""

from __future__ import annotations

import argparse
import sys

from local_api.health import collect_background_health, to_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync-health",
        description="Print a read-only background sync health report as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print(to_json(collect_background_health()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
