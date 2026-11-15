"""Entry point of the Explorer entries: ``pythonw -P -m dropwheel.winshell.open FILE…``.

Starts the host when needed and asks it to open the wheel at the cursor with the files.  The
call is marked ``merge``: the context menu starts one process per selected item, and the host
gathers the files that arrive within a moment into one wheel.  No console, no output: problems
go to the host log.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from .. import shellctl
from ..errors import UserError
from . import control, win32


def main(argv: list[str] | None = None) -> int:
    files = [os.path.abspath(a) for a in (sys.argv[1:] if argv is None else argv) if a.strip()]
    if not files:
        return 0
    # the user just clicked here: let the host take the foreground when the wheel opens
    win32.allow_foreground()
    try:
        if not control.ping():
            info = control.start(shellctl.shell_dir(), shellctl.shell_env(), wait=15.0)
            if not info.get("responding"):
                return 1
        control.request("open", [json.dumps({"files": files, "merge": True}, ensure_ascii=False)], timeout=10.0)
    except UserError as exc:
        logging.basicConfig(filename=str(control.log_path().with_name("winshell-open.log")), level=logging.INFO)
        logging.getLogger("dropwheel.winshell").error("cannot open %s: %s", files, exc.text())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
