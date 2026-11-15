"""Python types behind the Quickshell stand-in modules (QML module ``DropwheelHost``).

The QML files of the shell are the Linux ones, unchanged: ``qml/Quickshell*`` re-export these
types under the Quickshell names, plus a few QML-only types (windows, loaders, enums).

* :class:`Process` — Quickshell.Io ``Process`` on top of ``QProcess``.  The commands the shell
  runs for Hyprland and its pickers (``hyprctl``, ``hyprpicker``, ``zenity`` and their ``sh``
  probes) are answered in-process by :mod:`.virtual` instead.
* :class:`SplitParser`, :class:`StdioCollector`, :class:`FileView`, :class:`IpcEndpoint`,
  :class:`GlobalShortcut`, the ``WlrLayershell`` attached properties.
* :class:`Desktop` — the ``DesktopHost`` singleton: screens, cursor, mouse buttons, the Shift
  watcher, the hotkey and the per-window input styles.
"""

from __future__ import annotations

import codecs
import logging
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import (QAbstractNativeEventFilter, QByteArray, QCoreApplication, QFileSystemWatcher, QObject,
                            QPointF, QProcess, QProcessEnvironment, Qt, QTimer, Property, Signal, Slot)
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtQml import QmlAnonymous, QmlAttached, QmlElement, qmlRegisterSingletonInstance

from . import gnome, macos, virtual, win32

#: the desktop's answers: Win32 calls, Quartz and AppKit, or the GNOME Shell extension and X11 shapes
plat: Any = win32 if win32.WINDOWS else macos if macos.MACOS else gnome

log = logging.getLogger("dropwheel.qtshell")

QML_IMPORT_NAME = "DropwheelHost"
QML_IMPORT_MAJOR_VERSION = 1


# ================================================================================ stdio parsers

@QmlElement
class SplitParser(QObject):
    """Emits ``read(data)`` for every chunk ending with ``splitMarker`` (the rest at the end)."""

    read = Signal(str, arguments=["data"])
    splitMarkerChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._marker = "\n"
        self._buf = ""

    def _get_marker(self) -> str:
        return self._marker

    def _set_marker(self, value: str) -> None:
        self._marker = value or "\n"
        self.splitMarkerChanged.emit()

    splitMarker = Property(str, _get_marker, _set_marker, notify=splitMarkerChanged)

    def feed(self, text: str) -> None:
        self._buf += text
        while True:
            i = self._buf.find(self._marker)
            if i < 0:
                return
            chunk, self._buf = self._buf[:i], self._buf[i + len(self._marker):]
            self.read.emit(chunk)

    def finish(self) -> None:
        if self._buf:
            chunk, self._buf = self._buf, ""
            self.read.emit(chunk)


@QmlElement
class StdioCollector(QObject):
    """Collects the whole stream in ``text``; ``streamFinished`` once it is closed."""

    textChanged = Signal()
    streamFinished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._text = ""

    def _get_text(self) -> str:
        return self._text

    text = Property(str, _get_text, notify=textChanged)

    def feed(self, text: str) -> None:
        if text:
            self._text += text
            self.textChanged.emit()

    def reset(self) -> None:
        self._text = ""

    def finish(self) -> None:
        self.streamFinished.emit()


# ===================================================================================== Process

_LIVE: set["Process"] = set()


@QmlElement
class Process(QObject):
    """Quickshell.Io ``Process``: ``command``, ``running``, ``environment``, ``stdinEnabled``,
    ``stdout``/``stderr`` parsers, ``write()``, ``signal()``, ``started``/``exited``.

    ``running = false`` closes stdin when it is enabled (the daemon shuts down cleanly on EOF)
    and kills the process 3 s later; on Windows there is no SIGTERM to send otherwise."""

    commandChanged = Signal()
    runningChanged = Signal()
    environmentChanged = Signal()
    stdinEnabledChanged = Signal()
    stdoutChanged = Signal()
    stderrChanged = Signal()
    started = Signal()
    exited = Signal(int, int, arguments=["exitCode", "exitStatus"])

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._command: list[str] = []
        self._env: dict[str, Any] = {}
        self._stdin = False
        self._stdout: QObject | None = None
        self._stderr: QObject | None = None
        self._running = False
        self._proc: QProcess | None = None
        self._virtual = False
        self._decoders: dict[str, Any] = {}
        self._kill_timer = QTimer(self, singleShot=True, interval=3000)
        self._kill_timer.timeout.connect(self._kill)

    # ------------------------------------------------------------------ properties
    def _get_command(self) -> list:
        return list(self._command)

    def _set_command(self, value: Any) -> None:
        self._command = [str(v) for v in (value or [])]
        self.commandChanged.emit()

    command = Property("QVariantList", _get_command, _set_command, notify=commandChanged)

    def _get_env(self) -> dict:
        return dict(self._env)

    def _set_env(self, value: Any) -> None:
        self._env = dict(value or {})
        self.environmentChanged.emit()

    environment = Property("QVariantMap", _get_env, _set_env, notify=environmentChanged)

    def _get_stdin(self) -> bool:
        return self._stdin

    def _set_stdin(self, value: bool) -> None:
        self._stdin = bool(value)
        self.stdinEnabledChanged.emit()

    stdinEnabled = Property(bool, _get_stdin, _set_stdin, notify=stdinEnabledChanged)

    def _get_stdout(self) -> QObject | None:
        return self._stdout

    def _set_stdout(self, value: QObject | None) -> None:
        self._stdout = value
        self.stdoutChanged.emit()

    stdout = Property(QObject, _get_stdout, _set_stdout, notify=stdoutChanged)

    def _get_stderr(self) -> QObject | None:
        return self._stderr

    def _set_stderr(self, value: QObject | None) -> None:
        self._stderr = value
        self.stderrChanged.emit()

    stderr = Property(QObject, _get_stderr, _set_stderr, notify=stderrChanged)

    def _get_running(self) -> bool:
        return self._running

    def _set_running(self, value: bool) -> None:
        if bool(value) == self._running:
            return
        if value:
            self._start()
        else:
            self._stop()

    running = Property(bool, _get_running, _set_running, notify=runningChanged)

    # ------------------------------------------------------------------ control
    def _mark(self, running: bool) -> None:
        if running != self._running:
            self._running = running
            (_LIVE.add if running else _LIVE.discard)(self)
            self.runningChanged.emit()

    def _start(self) -> None:
        argv = list(self._command)
        if not argv:
            return
        for parser in (self._stdout, self._stderr):
            if isinstance(parser, StdioCollector):
                parser.reset()
        self._decoders = {name: codecs.getincrementaldecoder("utf-8")("replace") for name in ("out", "err")}
        handler = virtual.lookup(argv)
        if handler is not None:
            self._virtual = True
            self._mark(True)
            QTimer.singleShot(0, lambda: handler(self, argv))
            return
        self._virtual = False
        proc = QProcess(self)
        proc.setProgram(argv[0])
        proc.setArguments(argv[1:])
        env = QProcessEnvironment.systemEnvironment()
        for key, value in self._env.items():
            env.insert(str(key), str(value))
        proc.setProcessEnvironment(env)
        if not self._stdin:
            proc.setStandardInputFile(QProcess.nullDevice())
        proc.started.connect(self.started)
        proc.readyReadStandardOutput.connect(lambda: self._feed("out", bytes(proc.readAllStandardOutput().data())))
        proc.readyReadStandardError.connect(lambda: self._feed("err", bytes(proc.readAllStandardError().data())))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        self._mark(True)
        proc.start()

    def _stop(self) -> None:
        if self._virtual:
            self._virtual = False
            self._mark(False)
            return
        proc = self._proc
        if proc is None:
            self._mark(False)
            return
        if self._stdin:
            proc.closeWriteChannel()
            self._kill_timer.start()
        elif win32.WINDOWS:
            proc.kill()
        else:
            proc.terminate()
            self._kill_timer.start()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _parser(self, stream: str) -> QObject | None:
        return self._stdout if stream == "out" else self._stderr

    def _feed(self, stream: str, data: bytes) -> None:
        dec = self._decoders.get(stream)
        text = dec.decode(data) if dec else data.decode("utf-8", "replace")
        parser = self._parser(stream)
        if text and parser is not None and hasattr(parser, "feed"):
            parser.feed(text)

    def _finish_streams(self) -> None:
        for stream in ("out", "err"):
            dec = self._decoders.get(stream)
            if dec:
                self._feed(stream, b"")
                tail = dec.decode(b"", final=True)
                parser = self._parser(stream)
                if tail and parser is not None and hasattr(parser, "feed"):
                    parser.feed(tail)
            parser = self._parser(stream)
            if parser is not None and hasattr(parser, "finish"):
                parser.finish()

    def _on_finished(self, code: int, status: Any) -> None:
        self._kill_timer.stop()
        proc, self._proc = self._proc, None
        if proc is not None:
            self._feed("out", bytes(proc.readAllStandardOutput().data()))
            self._feed("err", bytes(proc.readAllStandardError().data()))
            proc.deleteLater()
        self._finish_streams()
        crashed = status == QProcess.ExitStatus.CrashExit
        self.exited.emit(int(code), 1 if crashed else 0)
        self._mark(False)

    def _on_error(self, error: Any) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._proc is not None:
            log.warning("cannot start %s: %s", self._command[:1], self._proc.errorString())
            self._proc.deleteLater()
            self._proc = None
            self._mark(False)

    # ------------------------------------------------------------------ virtual commands
    def virtual_started(self) -> bool:
        """For :mod:`.virtual`: emit ``started``; ``False`` when the run was cancelled."""
        if not (self._virtual and self._running):
            return False
        self.started.emit()
        return True

    def virtual_finish(self, stdout: str = "", code: int = 0) -> None:
        if not (self._virtual and self._running):
            return
        parser = self._stdout
        if stdout and parser is not None and hasattr(parser, "feed"):
            parser.feed(stdout)
        self._finish_streams()
        self._virtual = False
        self.exited.emit(int(code), 0)
        self._mark(False)

    # ------------------------------------------------------------------ slots
    @Slot(str)
    def write(self, data: str) -> None:
        if self._proc is not None and self._stdin:
            self._proc.write(QByteArray(data.encode("utf-8", "surrogatepass")))

    @Slot(int)
    def signal(self, signum: int) -> None:
        if self._virtual:
            self._stop()
        elif self._proc is not None:
            self._proc.kill() if signum == 9 or win32.WINDOWS else self._proc.terminate()


def shutdown_processes(timeout_ms: int = 5000) -> None:
    """At exit: close every stdin (the daemon finishes its jobs' clean-up on EOF), then kill."""
    procs = [p._proc for p in list(_LIVE) if p._proc is not None]
    for proc in procs:
        proc.closeWriteChannel()
    for proc in procs:
        if not proc.waitForFinished(timeout_ms):
            proc.kill()
            proc.waitForFinished(1000)


# ==================================================================================== FileView

@QmlElement
class FileView(QObject):
    """Quickshell.Io ``FileView`` (read side only): ``path``, ``watchChanges``, ``text()``,
    ``reload()``, ``loaded``/``loadFailed``/``fileChanged``."""

    pathChanged = Signal()
    watchChangesChanged = Signal()
    printErrorsChanged = Signal()
    loaded = Signal()
    loadFailed = Signal(int, arguments=["error"])
    fileChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = ""
        self._watch = False
        self._print = True
        self._text = ""
        self._stamp: tuple | None = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._changed)
        self._watcher.directoryChanged.connect(self._changed)
        self._pending = False

    def _get_path(self) -> str:
        return self._path

    def _set_path(self, value: str) -> None:
        value = str(value or "")
        if value.startswith("file://"):
            value = value[7:]
        if value == self._path:
            return
        self._path = value
        self.pathChanged.emit()
        self._rewatch()
        self._schedule()

    path = Property(str, _get_path, _set_path, notify=pathChanged)

    def _get_watch(self) -> bool:
        return self._watch

    def _set_watch(self, value: bool) -> None:
        self._watch = bool(value)
        self.watchChangesChanged.emit()
        self._rewatch()

    watchChanges = Property(bool, _get_watch, _set_watch, notify=watchChangesChanged)

    def _get_print(self) -> bool:
        return self._print

    def _set_print(self, value: bool) -> None:
        self._print = bool(value)
        self.printErrorsChanged.emit()

    printErrors = Property(bool, _get_print, _set_print, notify=printErrorsChanged)

    def _rewatch(self) -> None:
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if not (self._watch and self._path):
            return
        p = Path(self._path)
        targets = [str(t) for t in (p, p.parent) if t.exists()]
        if targets:
            self._watcher.addPaths(targets)

    def _stat(self) -> tuple | None:
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _changed(self, _path: str) -> None:
        stamp = self._stat()
        if stamp == self._stamp:
            return
        self._rewatch()  # an atomic replace drops the watch on the old file
        self.fileChanged.emit()

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        self._pending = False
        self._stamp = self._stat()
        try:
            self._text = Path(self._path).read_text(encoding="utf-8", errors="replace") if self._path else ""
        except OSError as exc:
            self._text = ""
            if self._print and self._path:
                log.warning("cannot read %s: %s", self._path, exc)
            self.loadFailed.emit(1)
            return
        if not self._path:
            self.loadFailed.emit(1)
            return
        self.loaded.emit()

    @Slot(result=str)
    def text(self) -> str:
        return self._text

    @Slot()
    def reload(self) -> None:
        self._schedule()


# ========================================================================================= IPC

IPC_ENDPOINTS: dict[str, "IpcEndpoint"] = {}


@QmlElement
class IpcEndpoint(QObject):
    """Base of the ``IpcHandler`` QML type: the host emits ``callRequested`` and the QML side
    answers with ``reply(ticket, result, error)``."""

    targetChanged = Signal()
    callRequested = Signal(int, str, "QVariantList", arguments=["ticket", "fn", "args"])

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target = ""
        self.replies: dict[int, Any] = {}

    def _get_target(self) -> str:
        return self._target

    def _set_target(self, value: str) -> None:
        if IPC_ENDPOINTS.get(self._target) is self:
            del IPC_ENDPOINTS[self._target]
        self._target = str(value or "")
        if self._target:
            IPC_ENDPOINTS[self._target] = self
        self.targetChanged.emit()

    target = Property(str, _get_target, _set_target, notify=targetChanged)

    @Slot(int, "QVariant", str)
    def reply(self, ticket: int, result: Any, error: str) -> None:
        self.replies[ticket] = (result, error)


# ============================================================================ GlobalShortcut

@QmlElement
class GlobalShortcut(QObject):
    """Quickshell.Hyprland ``GlobalShortcut``: ``name`` "shift" is the Shift watcher, "open"
    the configured hotkey (``trigger.shortcut``)."""

    appidChanged = Signal()
    nameChanged = Signal()
    descriptionChanged = Signal()
    pressed = Signal()
    released = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._appid = self._name = self._desc = ""

    def _get_appid(self) -> str:
        return self._appid

    def _set_appid(self, value: str) -> None:
        self._appid = str(value)
        self.appidChanged.emit()

    appid = Property(str, _get_appid, _set_appid, notify=appidChanged)

    def _get_name(self) -> str:
        return self._name

    def _set_name(self, value: str) -> None:
        if DESKTOP is not None:
            DESKTOP.shortcuts.get(self._name, set()).discard(self)
        self._name = str(value)
        if DESKTOP is not None:
            DESKTOP.shortcuts.setdefault(self._name, set()).add(self)
        self.nameChanged.emit()

    name = Property(str, _get_name, _set_name, notify=nameChanged)

    def _get_desc(self) -> str:
        return self._desc

    def _set_desc(self, value: str) -> None:
        self._desc = str(value)
        self.descriptionChanged.emit()

    description = Property(str, _get_desc, _set_desc, notify=descriptionChanged)


# ============================================================================ WlrLayershell

@QmlAnonymous
class LayerAttached(QObject):
    """``WlrLayershell.layer / namespace / keyboardFocus`` (keyboardFocus: 0 None, 1 Exclusive,
    2 OnDemand, as in the WlrKeyboardFocus QML enum)."""

    layerChanged = Signal()
    namespaceChanged = Signal()
    keyboardFocusChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._layer = 3
        self._ns = ""
        self._focus = 0

    def _get_layer(self) -> int:
        return self._layer

    def _set_layer(self, value: int) -> None:
        self._layer = int(value)
        self.layerChanged.emit()

    layer = Property(int, _get_layer, _set_layer, notify=layerChanged)

    def _get_ns(self) -> str:
        return self._ns

    def _set_ns(self, value: str) -> None:
        self._ns = str(value)
        self.namespaceChanged.emit()

    namespace = Property(str, _get_ns, _set_ns, notify=namespaceChanged)

    def _get_focus(self) -> int:
        return self._focus

    def _set_focus(self, value: int) -> None:
        if int(value) != self._focus:
            self._focus = int(value)
            self.keyboardFocusChanged.emit()

    keyboardFocus = Property(int, _get_focus, _set_focus, notify=keyboardFocusChanged)


@QmlElement
@QmlAttached(LayerAttached)
class WlrLayershell(QObject):
    @staticmethod
    def qmlAttachedProperties(cls: Any, obj: QObject) -> LayerAttached:  # noqa: ARG004 - PySide signature
        return LayerAttached(obj)


# ===================================================================================== screens

class ShellScreen(QObject):
    """A screen in global logical pixels (like Quickshell's ShellScreen), plus the work area
    without the taskbar (``available*``)."""

    changed = Signal()

    def __init__(self, screen: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.qscreen = screen
        screen.geometryChanged.connect(self.changed)
        screen.availableGeometryChanged.connect(self.changed)

    def _g(self) -> Any:
        return self.qscreen.geometry()

    def _a(self) -> Any:
        return self.qscreen.availableGeometry()

    name = Property(str, lambda self: self.qscreen.name(), notify=changed)
    x = Property(int, lambda self: self._g().x(), notify=changed)
    y = Property(int, lambda self: self._g().y(), notify=changed)
    width = Property(int, lambda self: self._g().width(), notify=changed)
    height = Property(int, lambda self: self._g().height(), notify=changed)
    availableX = Property(int, lambda self: self._a().x(), notify=changed)
    availableY = Property(int, lambda self: self._a().y(), notify=changed)
    availableWidth = Property(int, lambda self: self._a().width(), notify=changed)
    availableHeight = Property(int, lambda self: self._a().height(), notify=changed)
    devicePixelRatio = Property(float, lambda self: self.qscreen.devicePixelRatio(), notify=changed)


# ===================================================================================== Desktop

class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, desktop: "Desktop") -> None:
        super().__init__()
        self.desktop = desktop

    def nativeEventFilter(self, event_type: Any, message: Any) -> Any:
        if bytes(event_type) == b"windows_generic_MSG":
            try:
                ident = win32.hotkey_id(int(message))
            except (TypeError, ValueError):
                return False, 0
            if ident == Desktop.HOTKEY_ID:
                QTimer.singleShot(0, lambda: self.desktop.fire("open"))
                return True, 0
        return False, 0


DESKTOP: "Desktop | None" = None


class Desktop(QObject):
    """The ``DesktopHost`` QML singleton."""

    HOTKEY_ID = 0xD70

    screensChanged = Signal()
    focusedScreenNameChanged = Signal()
    globalPress = Signal(float, float, arguments=["x", "y"])

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shortcuts: dict[str, set[GlobalShortcut]] = {}
        self._screens: list[ShellScreen] = []
        self._focused = ""
        self._buttons = False
        self._shift = False
        self.hotkey = ""
        self._filter: _HotkeyFilter | None = None
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._rescan)
        app.screenRemoved.connect(self._rescan)
        app.primaryScreenChanged.connect(self._rescan)
        self._rescan()
        self._poll = QTimer(self, interval=25)
        self._poll.timeout.connect(self._tick)
        self._poll.start()

    # ------------------------------------------------------------------ screens
    def _rescan(self, *_args: Any) -> None:
        old = {s.qscreen: s for s in self._screens}
        self._screens = [old.pop(q, None) or ShellScreen(q, self) for q in QGuiApplication.screens()]
        for gone in old.values():
            gone.deleteLater()
        self.screensChanged.emit()

    screens = Property("QVariantList", lambda self: list(self._screens), notify=screensChanged)

    def _primary(self) -> QObject | None:
        prim = QGuiApplication.primaryScreen()
        for s in self._screens:
            if s.qscreen is prim:
                return s
        return self._screens[0] if self._screens else None

    primaryScreen = Property(QObject, _primary, notify=screensChanged)
    focusedScreenName = Property(str, lambda self: self._focused, notify=focusedScreenNameChanged)
    isWindows = Property(bool, lambda self: win32.WINDOWS, constant=True)
    #: input masks and the drop-only hot zone are emulated (Windows, macOS, GNOME); offscreen tests skip them
    emulatesInput = Property(bool, lambda self: win32.WINDOWS or macos.ACTIVE or gnome.ACTIVE, constant=True)

    def screen_at(self, x: float, y: float) -> ShellScreen | None:
        for s in self._screens:
            g = s.qscreen.geometry()
            if g.x() <= x < g.x() + g.width() and g.y() <= y < g.y() + g.height():
                return s
        return self._primary()  # type: ignore[return-value]

    # ------------------------------------------------------------------ polling
    def cursor_pos(self) -> tuple[int, int]:
        c = None if win32.WINDOWS else plat.cursor()
        if c is not None:
            return c
        p = QCursor.pos()
        return p.x(), p.y()

    def dragging(self) -> bool:
        """What stands in for Hyprland's DND state: the primary button is held."""
        return plat.primary_button_down()

    def fire(self, name: str) -> None:
        for sc in list(self.shortcuts.get(name, ())):
            sc.pressed.emit()

    def _tick(self) -> None:
        x, y = self.cursor_pos()
        s = self.screen_at(x, y)
        name = s.qscreen.name() if s else ""
        if name != self._focused:
            self._focused = name
            self.focusedScreenNameChanged.emit()
        if not (win32.WINDOWS or macos.ACTIVE or gnome.ACTIVE):
            return
        buttons = plat.any_button_down()
        if buttons and not self._buttons:
            self.globalPress.emit(float(x), float(y))
        self._buttons = buttons
        shift = plat.key_down(win32.VK_SHIFT)
        if shift and not self._shift:
            self.fire("shift")
        self._shift = shift

    # ------------------------------------------------------------------ hotkey
    def set_hotkey(self, text: str) -> bool:
        """Register ``text`` (``"Ctrl+Alt+D"``) as the "open" shortcut; ``False`` when it is not
        valid or another program already owns it."""
        win32.unregister_hotkey(self.HOTKEY_ID)
        self.hotkey = ""
        parsed = win32.parse_shortcut(text) if text else None
        if parsed is None:
            if text:
                log.warning("trigger.shortcut %r is not a valid shortcut", text)
            return False
        if self._filter is None and win32.WINDOWS:
            self._filter = _HotkeyFilter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        if not win32.register_hotkey(self.HOTKEY_ID, *parsed):
            if win32.WINDOWS:
                log.warning("the shortcut %s is already used by another program", text)
            return False
        self.hotkey = text
        return True

    # ------------------------------------------------------------------ slots for the QML side
    @Slot(str, result=str)
    def env(self, name: str) -> str:
        value = os.environ.get(name)
        if value is None and name == "HOME":
            value = os.environ.get("USERPROFILE")
        return value or ""

    @Slot(result=bool)
    def buttonDown(self) -> bool:  # noqa: N802
        return plat.primary_button_down()

    @Slot(result=QPointF)
    def cursor(self) -> QPointF:
        x, y = self.cursor_pos()
        return QPointF(x, y)

    @Slot(QObject, bool, bool)
    def updateWindow(self, window: QObject, click_through: bool, no_activate: bool) -> None:  # noqa: N802
        """Input pass-through and activation of a PanelWindow's ``Window`` (Windows only)."""
        if window is None:
            return
        window.setProperty("_q_showWithoutActivating", bool(no_activate))
        if not win32.WINDOWS:
            if bool(window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus) != bool(no_activate):
                window.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, bool(no_activate))
            if not window.isVisible():
                plat.forget_window(int(window.winId()))
                return
        if window.isVisible():
            plat.set_window_style(int(window.winId()), click_through=click_through, no_activate=no_activate)

    @Slot(QObject)
    def activate(self, window: QObject) -> None:
        if window is None:
            return
        window.requestActivate()
        plat.foreground(int(window.winId()))


def register(desktop: Desktop) -> None:
    """Expose ``desktop`` as the ``DesktopHost`` singleton (call before loading any QML)."""
    global DESKTOP
    DESKTOP = desktop
    qmlRegisterSingletonInstance(Desktop, QML_IMPORT_NAME, 1, 0, "DesktopHost", desktop)
