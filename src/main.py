from __future__ import annotations

import sys

from control_cli import main as control_main


def main() -> int:
    """Run the V3 control CLI.

    The optional ``control`` prefix is retained only as command-line spelling;
    it does not select a legacy execution path.
    """
    arguments = sys.argv[1:]
    if arguments[:1] == ["control"]:
        arguments = arguments[1:]
    return control_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
