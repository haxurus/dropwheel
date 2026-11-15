"""The Qt host for Windows, macOS, GNOME and KDE Plasma: loads the Linux shell (``shell/shell.qml``) with
PySide6, the Quickshell stand-in modules in ``qml/`` first on the import path.

* one instance per user (a lock file next to the state file);
* the daemon is started by the shell as on Linux (``DROPWHEEL_BACKEND``, with ``pythonw``);
* IPC on ``127.0.0.1``: ``{token, fn, args}`` → ``{ok, result | error}`` (see :mod:`.control`).
  ``ping`` and ``quit`` are answered here, the shell's ``IpcHandler`` functions are forwarded.
  ``open`` calls with ``merge`` (the Explorer context menu starts one process per selected
  file) are gathered for 400 ms into one wheel;
* QML and Python messages go to ``qtshell.log`` in the cache folder.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import shlex
import sys
from pathlib import Path
from typing import Any

from .. import config as cfgmod
from .. import shellctl, tools, util
from . import control, macos, win32

log = logging.getLogger("dropwheel.qtshell")

QML_DIR = Path(__file__).resolve().parent / "qml"
#: The functions of the shell's ``IpcHandler`` (shell.qml) that the host forwards.
IPC_FUNCTIONS = frozenset({"open", "openAtCursor", "close", "setHotzone", "reload", "ping"})
MERGE_MS = 400


def font_dirs() -> list[Path]:
    """Where the host looks for extra fonts (the icon font installed by ``dropwheel setup windows``)."""
    return [util.xdg_data_home() / "dropwheel" / "fonts", shellctl.data_dir() / "fonts"]


def _setup_logging(foreground: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = control.log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if foreground and sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)


def _backend_env() -> None:
    """``DROPWHEEL_BACKEND`` (the daemon under ``pythonw``: no console window), ``DROPWHEEL_DATA``,
    ``PYTHONPATH`` for checkouts — what ``shellctl.shell_env`` gives ``qs`` on Linux."""
    tools.extend_path()
    env = shellctl.shell_env()
    argv = [control.gui_python(), "-P", "-m", "dropwheel", "daemon"]
    env["DROPWHEEL_BACKEND"] = shlex.join(argv)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.update(env)


class IpcServer:
    def __init__(self, token: str) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QHostAddress, QTcpServer

        self.token = token
        self.server = QTcpServer()
        if not self.server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            raise RuntimeError(self.server.errorString())
        self.server.newConnection.connect(self._accept)
        self.merge_files: list[str] = []
        self.merge_timer = QTimer(singleShot=True, interval=MERGE_MS)
        self.merge_timer.timeout.connect(self._flush_merge)
        self.quit_requested = False

    @property
    def port(self) -> int:
        return int(self.server.serverPort())

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def ready(sock: Any = sock, buf: bytearray = buf) -> None:
                buf.extend(bytes(sock.readAll().data()))
                if len(buf) > 1 << 20:
                    sock.abort()
                    return
                if b"\n" in buf:
                    line = bytes(buf).split(b"\n", 1)[0]
                    self._handle(sock, line)

            sock.readyRead.connect(ready)
            sock.disconnected.connect(sock.deleteLater)

    def _send(self, sock: Any, reply: dict) -> None:
        sock.write((json.dumps(reply, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        sock.flush()
        sock.disconnectFromHost()

    def _handle(self, sock: Any, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            msg = None
        if not isinstance(msg, dict) or not hmac.compare_digest(str(msg.get("token", "")), self.token):
            sock.abort()  # no reply for a wrong token
            return
        fn = str(msg.get("fn", ""))
        args = msg.get("args") if isinstance(msg.get("args"), list) else []
        if fn == "quit":
            self._send(sock, {"ok": True, "result": None})
            self.quit_requested = True
            from PySide6.QtCore import QCoreApplication, QTimer
            QTimer.singleShot(100, QCoreApplication.quit)
            return
        if fn == "open" and args:
            args = [_shell_payload(args[0]), *args[1:]]
        if fn == "open" and args and self._merge(args[0]):
            self._send(sock, {"ok": True, "result": None})
            return
        if fn not in IPC_FUNCTIONS:
            self._send(sock, {"ok": False, "error": f"unknown function {fn}"})
            return
        self._call_shell(fn, [str(a) for a in args], lambda result, error: self._send(
            sock, {"ok": not error, "result": result, "error": error}))

    def _merge(self, payload: Any) -> bool:
        """Gather ``open`` payloads with ``"merge": true``; ``False`` for any other payload."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else None
        except ValueError:
            return False
        if not (isinstance(data, dict) and data.get("merge") is True and isinstance(data.get("files"), list)):
            return False
        for f in data["files"]:
            if isinstance(f, str) and f and f not in self.merge_files:
                self.merge_files.append(f)
        self.merge_timer.start()
        return True

    def _flush_merge(self) -> None:
        files, self.merge_files = self.merge_files, []
        if files:
            self._call_shell("open", [json.dumps({"files": files}, ensure_ascii=False)], lambda r, e: None)

    def _call_shell(self, fn: str, args: list[str], done: Any) -> None:
        from PySide6.QtCore import QTimer

        from . import native

        ep = native.IPC_ENDPOINTS.get(shellctl.IPC_TARGET)
        if ep is None:
            done(None, "the shell is not loaded")
            return
        ticket = secrets.randbits(31)
        ep.callRequested.emit(ticket, fn, args)
        if ticket in ep.replies:
            result, error = ep.replies.pop(ticket)
            done(result, error)
            return
        QTimer.singleShot(0, lambda: done(*ep.replies.pop(ticket, (None, "no reply"))))


def _shell_payload(payload: Any) -> Any:
    """An ``open`` payload with its files in the shell's path form (``/C:/…``)."""
    from .virtual import to_shell_path

    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except ValueError:
        return payload
    if not (isinstance(data, dict) and isinstance(data.get("files"), list)):
        return payload
    data["files"] = [to_shell_path(f) if isinstance(f, str) else f for f in data["files"]]
    return json.dumps(data, ensure_ascii=False)


def _qt_message(mode: Any, context: Any, message: str) -> None:
    from PySide6.QtCore import QtMsgType

    level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
             QtMsgType.QtWarningMsg: logging.WARNING}.get(mode, logging.ERROR)
    where = f" ({context.file}:{context.line})" if context.file else ""
    logging.getLogger("dropwheel.qml").log(level, "%s%s", message, where)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    foreground = "--foreground" in args
    _setup_logging(foreground)
    shell = shellctl.shell_dir() / "shell.qml"
    if not shell.is_file():
        log.error("the shell was not found: %s", shell)
        return 1
    _backend_env()

    from PySide6.QtCore import QLockFile, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    state = control.state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(state.with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        log.info("another Dropwheel host is running")
        return 0

    qInstallMessageHandler(_qt_message)
    # (not on macOS: XQuartz sets DISPLAY there, and PySide6 has no xcb plugin for macOS)
    if not (win32.WINDOWS or macos.MACOS) and not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("DISPLAY"):
        # GNOME, KDE: under XWayland the host may place its windows and keep them above the others
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    if macos.MACOS:
        # keep Qt from turning the process into a regular Dock app before init_app makes it an accessory
        os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")
    app = QApplication(sys.argv[:1])
    from . import gnome

    gnome.ACTIVE = not win32.WINDOWS and app.platformName() == "xcb"
    macos.ACTIVE = macos.MACOS and app.platformName() == "cocoa"
    if macos.ACTIVE:
        macos.init_app()
    app.setApplicationName("Dropwheel")
    app.setQuitOnLastWindowClosed(False)
    for folder in font_dirs():
        for font in sorted(folder.glob("*.tt[fc]")) + sorted(folder.glob("*.otf")):
            if QFontDatabase.addApplicationFont(str(font)) < 0:
                log.warning("cannot load the font %s", font)

    from . import native

    desktop = native.Desktop()
    native.register(desktop)
    if native.plat is native.kde and gnome.ACTIVE:
        native.kde.serve(lambda: desktop.fire("open"))  # the KWin script's pointer and shortcut
    config = cfgmod.load()
    shortcut = str(config.get("trigger", {}).get("shortcut", "") or "")
    if shortcut and win32.WINDOWS and not desktop.set_hotkey(shortcut):
        log.warning("the wheel shortcut %s is not available", shortcut)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell)))
    if not engine.rootObjects():
        log.error("the shell did not load: see the messages above")
        return 1

    token = secrets.token_hex(32)
    server = IpcServer(token)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": server.port, "token": token}), encoding="utf-8")
    os.replace(tmp, state)
    log.info("Dropwheel host ready (port %d, shortcut %s)", server.port, desktop.hotkey or "none")
    app.aboutToQuit.connect(native.shutdown_processes)
    try:
        code = app.exec()
    finally:
        # the engine is torn down after this: its bindings complain about objects already gone
        qInstallMessageHandler(None)
        try:
            state.unlink()
        except OSError:
            pass
        lock.unlock()
    return int(code)
