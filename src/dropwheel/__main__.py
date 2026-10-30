"""``python -m dropwheel`` entry point: delegates to :func:`dropwheel.cli.main`."""

from __future__ import annotations

import sys


def main() -> int:
    """Run the command line interface and return its exit status."""
    try:
        from dropwheel.cli import main as cli_main
    except ImportError as exc:  # pragma: no cover - only when the package is incomplete
        if getattr(exc, "name", None) in ("dropwheel.cli", "cli"):
            sys.stderr.write(f"dropwheel: command line module is missing ({exc})\n")
            return 1
        raise
    rv = cli_main()
    if rv is None:
        return 0
    if isinstance(rv, bool):
        return 0 if rv else 1
    if isinstance(rv, int):
        return rv
    return 0


if __name__ == "__main__":
    sys.exit(main())
