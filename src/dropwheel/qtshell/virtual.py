"""In-process answers for the commands the Linux shell runs for its desktop (``Process`` asks
:func:`lookup` before starting anything).

=====================================  ==================================================
``hyprctl -j cursorpos``               ``{"x", "y"}`` from the Qt cursor
``hyprctl -j monitors``                the screens; ``solitaryBlockedBy: ["DND"]`` while the
                                       primary mouse button is held (a drag is under way)
``sh -c "command -v hyprpicker …"``    exit 0: the host has its own colour picker
``hyprpicker -f hex``                  :class:`ColourPick`, prints ``#rrggbb``
``sh -c "command -v zenity …"``        prints ``zenity``
``zenity --file-selection …``          the native file dialog
any other ``hyprctl`` / ``sh``         exit 127 (not installed)
=====================================  ==================================================

Paths go to the shell in its URL-path form (``/C:/Users/…``, see ``daemon`` on Windows).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .native import Process

Handler = Callable[["Process", list[str]], None]

_DRIVE_URLPATH = re.compile(r"^/[A-Za-z]:[/\\]")


def to_shell_path(path: str) -> str:
    """``C:\\x\\y`` → ``/C:/x/y`` (the form the QML path helpers understand)."""
    p = path.replace("\\", "/")
    return "/" + p if re.match(r"^[A-Za-z]:/", p) else p


def from_shell_path(path: str) -> str:
    return path[1:] if _DRIVE_URLPATH.match(path) else path


def _desktop() -> Any:
    from . import native
    return native.DESKTOP


def _cursorpos(proc: "Process", argv: list[str]) -> None:
    if not proc.virtual_started():
        return
    x, y = _desktop().cursor_pos()
    proc.virtual_finish(json.dumps({"x": x, "y": y}))


def _monitors(proc: "Process", argv: list[str]) -> None:
    if not proc.virtual_started():
        return
    desk = _desktop()
    blocked = ["DND"] if desk.dragging() else []
    mons = [{"id": i, "name": s.qscreen.name(), "x": s.qscreen.geometry().x(), "y": s.qscreen.geometry().y(),
             "width": s.qscreen.geometry().width(), "height": s.qscreen.geometry().height(),
             "focused": s.qscreen.name() == desk._focused, "solitaryBlockedBy": blocked}
            for i, s in enumerate(desk._screens)]
    proc.virtual_finish(json.dumps(mons))


def _exit(code: int, out: str = "") -> Handler:
    def run(proc: "Process", argv: list[str]) -> None:
        if proc.virtual_started():
            proc.virtual_finish(out, code)
    return run


def _missing(proc: "Process", argv: list[str]) -> None:
    # never started: like a binary that is not installed
    proc.virtual_finish("", 127)


# ================================================================================ colour picker

class ColourPick:
    """A near-transparent full-screen window on every screen: a click samples the pixel under
    the cursor (after the windows are hidden), Esc or a right click cancels."""

    def __init__(self, done: Callable[[str], None]) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QWidget

        from . import macos

        self.done = done
        self.windows: list[Any] = []
        outer = self

        class Catcher(QWidget):
            def mousePressEvent(self, event: Any) -> None:  # noqa: N802
                if event.button() == Qt.MouseButton.LeftButton:
                    outer.sample(event.globalPosition().toPoint())
                else:
                    outer.finish("")

            def keyPressEvent(self, event: Any) -> None:  # noqa: N802
                if event.key() == Qt.Key.Key_Escape:
                    outer.finish("")

        for screen in QGuiApplication.screens():
            w = Catcher()
            w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                             | Qt.WindowType.Tool)
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            w.setStyleSheet("background: rgba(0, 0, 0, 1);")  # alpha 1/255: invisible but takes the click
            w.setAutoFillBackground(True)
            w.setCursor(Qt.CursorShape.CrossCursor)
            w.setGeometry(screen.geometry())
            w.show()
            if macos.ACTIVE:  # above full-screen apps and on every Space, like the wheel
                macos.set_window_style(int(w.winId()), click_through=False, no_activate=False)
            self.windows.append(w)
        if self.windows:
            self.windows[0].activateWindow()
            self.windows[0].setFocus()

    def sample(self, pos: Any) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        for w in self.windows:
            w.hide()

        def grab() -> None:
            screen = QGuiApplication.screenAt(pos)
            colour = ""
            if screen is not None:
                g = screen.geometry()
                img = screen.grabWindow(0, pos.x() - g.x(), pos.y() - g.y(), 1, 1).toImage()
                if not img.isNull():
                    colour = img.pixelColor(0, 0).name()
            self.finish(colour)

        QTimer.singleShot(80, grab)  # let the compositor drop the catcher windows first

    def finish(self, colour: str) -> None:
        for w in self.windows:
            w.close()
            w.deleteLater()
        self.windows = []
        cb, self.done = self.done, None
        if cb:
            cb(colour)


_PICKS: list[ColourPick] = []


def _hyprpicker(proc: "Process", argv: list[str]) -> None:
    if not proc.virtual_started():
        return

    def done(colour: str) -> None:
        _PICKS.clear()
        proc.virtual_finish(colour + "\n" if colour else "", 0)

    _PICKS.append(ColourPick(done))


# ================================================================================= file picker

def zenity_args(argv: list[str]) -> dict:
    """The options of ``zenity --file-selection`` the shell passes."""
    opts: dict[str, Any] = {"title": "", "directory": False, "start": "", "filter": ""}
    for arg in argv[1:]:
        if arg == "--directory":
            opts["directory"] = True
        elif arg.startswith("--title="):
            opts["title"] = arg[len("--title="):]
        elif arg.startswith("--filename="):
            opts["start"] = from_shell_path(arg[len("--filename="):])
        elif arg.startswith("--file-filter="):
            name, _, patterns = arg[len("--file-filter="):].partition("|")
            opts["filter"] = f"{name.strip()} ({patterns.strip()})" if patterns.strip() else ""
    return opts


def _zenity(proc: "Process", argv: list[str]) -> None:
    if "--file-selection" not in argv:
        _missing(proc, argv)
        return
    if not proc.virtual_started():
        return
    from PySide6.QtWidgets import QFileDialog

    o = zenity_args(argv)
    if o["directory"]:
        path = QFileDialog.getExistingDirectory(None, o["title"], o["start"])
    else:
        path, _ = QFileDialog.getOpenFileName(None, o["title"], o["start"], o["filter"])
    if path:
        proc.virtual_finish(to_shell_path(path) + "\n", 0)
    else:
        proc.virtual_finish("", 1)


# ====================================================================================== lookup

def lookup(argv: list[str]) -> Handler | None:
    """The in-process handler for ``argv``, or ``None`` to start it for real."""
    prog = argv[0].lower() if argv else ""
    if prog == "hyprctl":
        if argv[1:] == ["-j", "cursorpos"]:
            return _cursorpos
        if argv[1:] == ["-j", "monitors"]:
            return _monitors
        return _missing
    if prog == "sh":
        script = argv[2] if len(argv) > 2 and argv[1] == "-c" else ""
        if "command -v hyprpicker" in script:
            return _exit(0)
        if "command -v zenity" in script:
            return _exit(0, "zenity\n")
        return _missing
    if prog == "hyprpicker":
        return _hyprpicker
    if prog in ("zenity", "kdialog"):
        return _zenity if prog == "zenity" else _missing
    return None
