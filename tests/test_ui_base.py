"""Tests for the QML foundation (shell/services, shell/components) and the ui_base locale fragment.

Two layers:

* static checks that always run (locale parity, every ``I18n.t("…")`` key exists, built-in fallback tables match the
  JSON fragments, qmldir files list every component, balanced braces);
* runtime checks that load the real QML in an offscreen Qt engine (PySide6 ≥ 6.9) with small Python stand-ins for
  the Quickshell types used by the services (Singleton, Quickshell.env/screens, Process, SplitParser,
  StdioCollector, FileView, ClippingRectangle).  They are skipped when PySide6 is not installed.  The stand-ins mirror
  the Quickshell 0.3.2 semantics that matter here (deferred start, ``exited`` before ``runningChanged``, no
  ``exited`` when the binary is missing, FileView watching).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
SERVICES = SHELL / "services"
COMPONENTS = SHELL / "components"
LOCALES = ROOT / "src" / "dropwheel" / "locales"

SERVICE_FILES = ["Theme.qml", "Backend.qml", "Hypr.qml", "I18n.qml", "Jobs.qml"]
COMPONENT_FILES = [
    "MaterialIcon.qml", "StyledText.qml", "StyledRect.qml", "StateLayer.qml", "Anim.qml", "CAnim.qml",
    "Elevation.qml", "ButtonBase.qml", "IconButton.qml", "TextButton.qml", "M3Slider.qml", "M3Switch.qml",
    "FieldFrame.qml", "M3TextField.qml", "M3Dropdown.qml", "ColorPicker.qml", "Chip.qml", "CircularProgress.qml",
    "LinearProgress.qml", "Tooltip.qml", "FileIcon.qml", "ScrollArea.qml",
]


# --------------------------------------------------------------------------------------------- static helpers
def _load_locale(lang: str) -> dict[str, str]:
    path = LOCALES / lang / "ui_base.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _strip_qml(text: str) -> str:
    """Remove comments, string literals and regex literals (roughly) so braces can be counted."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "\"'`":
            q = c
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append('""')
            continue
        if c == "/" and out:
            prev = "".join(out).rstrip()[-1:] if "".join(out).rstrip() else ""
            if prev in "(,=:[!&|?{};":
                j = i + 1
                while j < n and text[j] != "/" and text[j] != "\n":
                    if text[j] == "\\":
                        j += 1
                    elif text[j] == "[":
                        while j < n and text[j] != "]":
                            j += 1
                    j += 1
                if j < n and text[j] == "/":
                    i = j + 1
                    while i < n and text[i].isalpha():
                        i += 1
                    out.append("/r/")
                    continue
        out.append(c)
        i += 1
    return "".join(out)


def _qml_files() -> list[Path]:
    return sorted(list(SERVICES.glob("*.qml")) + list(COMPONENTS.glob("*.qml")))


def _used_keys() -> tuple[set[str], set[str]]:
    plain: set[str] = set()
    plural: set[str] = set()
    for f in _qml_files():
        text = f.read_text(encoding="utf-8")
        plain.update(re.findall(r'\bt\(\s*"([A-Za-z0-9_.]+)"', text))
        plural.update(re.findall(r'\btn\(\s*"([A-Za-z0-9_.]+)"', text))
        # makeError("code", "ui.key", …) in Backend
        plain.update(re.findall(r'makeError\(\s*"[a-z_]+"\s*,\s*"([A-Za-z0-9_.]+)"', text))
    return plain, plural


def _builtin_table(name: str) -> dict[str, str]:
    text = (SERVICES / "I18n.qml").read_text(encoding="utf-8")
    m = re.search(r"readonly property var %s: \(\{(.*?)\}\)" % name, text, re.S)
    assert m, f"built-in table {name} not found"
    return dict(re.findall(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', m.group(1)))


# --------------------------------------------------------------------------------------------- static tests
def test_expected_files_exist():
    for name in SERVICE_FILES:
        assert (SERVICES / name).is_file(), name
    for name in COMPONENT_FILES:
        assert (COMPONENTS / name).is_file(), name


def test_locales_parity_and_placeholders():
    it = _load_locale("it")
    en = _load_locale("en")
    assert set(it) == set(en), set(it) ^ set(en)
    for key in en:
        assert isinstance(en[key], str) and en[key].strip(), key
        assert isinstance(it[key], str) and it[key].strip(), key
        assert key.startswith("ui."), key
        assert set(re.findall(r"\{(\w+)\}", en[key])) == set(re.findall(r"\{(\w+)\}", it[key])), key


def test_every_used_key_is_defined():
    en = _load_locale("en")
    plain, plural = _used_keys()
    assert plain, "no I18n keys found — regex broken?"
    missing = sorted(k for k in plain if k not in en)
    missing += sorted(k + s for k in plural for s in (".one", ".other") if k + s not in en)
    assert not missing, missing


def test_builtin_fallback_tables_match_locales():
    for lang in ("en", "it"):
        table = _builtin_table(lang)
        data = _load_locale(lang)
        assert table, lang
        for key, value in table.items():
            assert key in data, (lang, key)
            assert json.loads('"%s"' % value) == data[key], (lang, key)
    assert set(_builtin_table("en")) == set(_builtin_table("it"))


def test_qmldir_lists_all_files():
    comp = (COMPONENTS / "qmldir").read_text(encoding="utf-8").split("\n")
    listed = {line.split()[-1] for line in comp if line.strip() and not line.startswith("#")}
    assert listed == {p.name for p in COMPONENTS.glob("*.qml")}
    serv = (SERVICES / "qmldir").read_text(encoding="utf-8").split("\n")
    entries = [line.split() for line in serv if line.strip() and not line.startswith("#")]
    assert {e[-1] for e in entries} == {p.name for p in SERVICES.glob("*.qml")}
    for e in entries:
        assert e[0] == "singleton" and e[1] + ".qml" == e[-1], e
        assert "pragma Singleton" in (SERVICES / e[-1]).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", _qml_files(), ids=lambda p: p.name)
def test_qml_balanced(path: Path):
    text = _strip_qml(path.read_text(encoding="utf-8"))
    for o, c in ("{}", "()", "[]"):
        assert text.count(o) == text.count(c), (path.name, o, text.count(o), text.count(c))
    assert "\t" not in path.read_text(encoding="utf-8"), "tabs in " + path.name


@pytest.mark.parametrize("path", _qml_files(), ids=lambda p: p.name)
def test_qml_header_documents_api(path: Path):
    head = path.read_text(encoding="utf-8").lstrip()
    if head.startswith("pragma"):
        head = "\n".join(line for line in head.splitlines() if not line.startswith("pragma")).lstrip()
    assert head.startswith("//"), path.name + " lacks the API comment header"


# --------------------------------------------------------------------------------------------- runtime harness
def _prepare_pyside():
    if sys.platform == "win32":
        try:
            import PySide6  # noqa: F401
        except ImportError:
            pytest.skip("PySide6 not installed")
        import ctypes
        import glob

        base = os.path.dirname(PySide6.__file__)
        os.add_dll_directory(base)
        for dll in sorted(glob.glob(os.path.join(base, "Qt6*.dll"))):
            try:
                ctypes.WinDLL(dll)
            except OSError:
                pass
    qtqml = pytest.importorskip("PySide6.QtQml")
    from PySide6 import QtCore

    major, minor = (int(x) for x in QtCore.qVersion().split(".")[:2])
    if (major, minor) < (6, 9):
        pytest.skip("Qt >= 6.9 required (RectangularShadow)")
    return qtqml


_STATE: dict = {}


def _harness():
    """Create the QGuiApplication and register the Quickshell stand-ins once per session."""
    if "registered" in _STATE:
        return _STATE
    _prepare_pyside()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import (ClassInfo, Property, QByteArray, QFileSystemWatcher, QObject, QProcess,
                                QProcessEnvironment, QTimer, Signal, Slot)
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import ListProperty, qmlRegisterSingletonType, qmlRegisterType

    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

    @ClassInfo(DefaultProperty="data")
    class Singleton(QObject):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._items = []

        def _append(self, obj):
            self._items.append(obj)

        def _count(self):
            return len(self._items)

        def _at(self, i):
            return self._items[i]

        data = ListProperty(QObject, append=_append, count=_count, at=_at)

    class ShellScreen(QObject):
        geometryChanged = Signal()

        def __init__(self, name, x, y, w, h, parent=None):
            super().__init__(parent)
            self._name, self._x, self._y, self._w, self._h = name, x, y, w, h

        name = Property(str, lambda s: s._name, constant=True)
        x = Property(int, lambda s: s._x, notify=geometryChanged)
        y = Property(int, lambda s: s._y, notify=geometryChanged)
        width = Property(int, lambda s: s._w, notify=geometryChanged)
        height = Property(int, lambda s: s._h, notify=geometryChanged)

    class QuickshellGlobal(QObject):
        screensChanged = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self._screens = [ShellScreen("DP-1", 0, 0, 2560, 1440, self), ShellScreen("HDMI-A-1", 2560, 200, 1920, 1080, self)]
            self.detached = []

        @Slot(str, result="QVariant")
        def env(self, name):
            return os.environ.get(name)

        @Slot(list)
        def execDetached(self, argv):
            self.detached.append(list(argv))

        def _get_screens(self):
            return self._screens

        screens = Property(list, _get_screens, notify=screensChanged)

    class SplitParser(QObject):
        read = Signal(str)
        splitMarkerChanged = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self._marker = "\n"
            self._buf = b""

        def _gm(self):
            return self._marker

        def _sm(self, v):
            self._marker = v
            self.splitMarkerChanged.emit()

        splitMarker = Property(str, _gm, _sm, notify=splitMarkerChanged)

        def _reset(self):
            self._buf = b""

        def _feed(self, data: bytes):
            if not self._marker:
                self.read.emit(data.decode("utf-8", "replace"))
                return
            self._buf += data
            m = self._marker.encode()
            while m in self._buf:
                line, self._buf = self._buf.split(m, 1)
                self.read.emit(line.decode("utf-8", "replace"))

        def _end(self):
            if self._buf:
                buf, self._buf = self._buf, b""
                self.read.emit(buf.decode("utf-8", "replace"))

    class StdioCollector(QObject):
        dataChanged = Signal()
        streamFinished = Signal()
        waitForEndChanged = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self._buf = b""
            self._data = b""
            self._wait = True

        text = Property(str, lambda s: s._data.decode("utf-8", "replace"), notify=dataChanged)
        data = Property(QByteArray, lambda s: QByteArray(s._data), notify=dataChanged)

        def _gw(self):
            return self._wait

        def _sw(self, v):
            self._wait = bool(v)
            self.waitForEndChanged.emit()

        waitForEnd = Property(bool, _gw, _sw, notify=waitForEndChanged)

        def _reset(self):
            self._buf = b""

        def _feed(self, data: bytes):
            self._buf += data
            if not self._wait:
                self._data = self._buf
                self.dataChanged.emit()

        def _end(self):
            if self._wait:
                self._data = self._buf
                self.dataChanged.emit()
            self.streamFinished.emit()

    class Process(QObject):
        runningChanged = Signal()
        processIdChanged = Signal()
        commandChanged = Signal()
        environmentChanged = Signal()
        stdinEnabledChanged = Signal()
        stdoutParserChanged = Signal()
        stderrParserChanged = Signal()
        started = Signal()
        exited = Signal(int, int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._proc = None
            self._target = False
            self._command = []
            self._env = {}
            self._stdin = False
            self._out = None
            self._err = None

        def _gr(self):
            return self._proc is not None

        def _sr(self, v):
            self._target = bool(v)
            if v:
                QTimer.singleShot(0, self._start_if_ready)  # Quickshell defers to post-reload
            elif self._proc is not None:
                self._proc.terminate()

        running = Property(bool, _gr, _sr, notify=runningChanged)

        def _gc(self):
            return self._command

        def _sc(self, v):
            self._command = [str(x) for x in (v or [])]
            self.commandChanged.emit()
            QTimer.singleShot(0, self._start_if_ready)

        command = Property(list, _gc, _sc, notify=commandChanged)

        def _ge(self):
            return self._env

        def _se(self, v):
            self._env = dict(v or {})
            self.environmentChanged.emit()

        environment = Property("QVariantMap", _ge, _se, notify=environmentChanged)

        def _gs(self):
            return self._stdin

        def _ss(self, v):
            self._stdin = bool(v)
            self.stdinEnabledChanged.emit()

        stdinEnabled = Property(bool, _gs, _ss, notify=stdinEnabledChanged)

        def _go(self):
            return self._out

        def _so(self, v):
            self._out = v
            self.stdoutParserChanged.emit()

        stdout = Property(QObject, _go, _so, notify=stdoutParserChanged)

        def _gerr(self):
            return self._err

        def _serr(self, v):
            self._err = v
            self.stderrParserChanged.emit()

        stderr = Property(QObject, _gerr, _serr, notify=stderrParserChanged)

        processId = Property("QVariant", lambda s: s._proc.processId() if s._proc else None, notify=processIdChanged)

        def _start_if_ready(self):
            if self._proc is not None or not self._target or not self._command:
                return
            self._target = False
            p = QProcess(self)
            env = QProcessEnvironment.systemEnvironment()
            for k, v in self._env.items():
                if v is None:
                    env.remove(k)
                else:
                    env.insert(k, str(v))
            p.setProcessEnvironment(env)
            p.started.connect(self._on_started)
            p.finished.connect(self._on_finished)
            p.errorOccurred.connect(self._on_error)
            p.readyReadStandardOutput.connect(self._on_out)
            p.readyReadStandardError.connect(self._on_err)
            for parser in (self._out, self._err):
                if parser is not None:
                    parser._reset()
            self._proc = p
            argv = list(self._command)
            fake = os.environ.get("DROPWHEEL_TEST_FAKE_" + argv[0].upper().replace("-", "_"))
            if fake:  # test hook: run a Python stand-in for a Linux-only tool (hyprctl)
                argv = [sys.executable, fake] + argv[1:]
            p.start(argv[0], argv[1:])
            if not self._stdin:
                p.closeWriteChannel()

        def _on_started(self):
            self.processIdChanged.emit()
            self.runningChanged.emit()
            self.started.emit()

        def _on_finished(self, code, status):
            p = self._proc
            if p is None:
                return
            for sig in (p.started, p.finished, p.errorOccurred, p.readyReadStandardOutput, p.readyReadStandardError):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            rest_out = bytes(p.readAllStandardOutput())
            rest_err = bytes(p.readAllStandardError())
            self._proc = None
            p.deleteLater()
            if self._out is not None:
                if rest_out:
                    self._out._feed(rest_out)
                self._out._end()
            if self._err is not None:
                if rest_err:
                    self._err._feed(rest_err)
                self._err._end()
            self.exited.emit(int(code), int(status.value) if hasattr(status, "value") else int(status))
            self.runningChanged.emit()
            self.processIdChanged.emit()
            self._start_if_ready()

        def _on_error(self, error):
            if error == QProcess.ProcessError.FailedToStart:
                p = self._proc
                self._proc = None
                if p is not None:
                    for sig in (p.started, p.finished, p.errorOccurred):
                        try:
                            sig.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                    p.deleteLater()
                self.runningChanged.emit()

        def _on_out(self):
            if self._proc is not None and self._out is not None:
                self._out._feed(bytes(self._proc.readAllStandardOutput()))

        def _on_err(self):
            if self._proc is not None and self._err is not None:
                self._err._feed(bytes(self._proc.readAllStandardError()))

        @Slot(str)
        def write(self, data):
            if self._proc is not None:
                self._proc.write(data.encode("utf-8"))

        @Slot(int)
        def signal(self, sig):
            if self._proc is not None:
                self._proc.kill() if sig == 9 else self._proc.terminate()

        @Slot(list)
        def exec(self, argv):
            self._sr(False)
            self._sc(argv)
            self._sr(True)

        @Slot()
        def startDetached(self):
            if self._command:
                QProcess.startDetached(self._command[0], self._command[1:])

    class FileView(QObject):
        pathChanged = Signal()
        loaded = Signal()
        loadFailed = Signal(int)
        fileChanged = Signal()
        watchChangesChanged = Signal()
        printErrorsChanged = Signal()
        loadedOrAsyncChanged = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self._path = ""
            self._text = ""
            self._watch = False
            self._print = True
            self._loaded = False
            self._watcher = None

        def _gp(self):
            return self._path

        def _sp(self, v):
            if v == self._path:
                return
            self._path = v
            self.pathChanged.emit()
            self._update_watch()
            QTimer.singleShot(0, self.reload)

        def _native(self):
            # The QML only accepts absolute POSIX paths; on Windows the tests pass "/C:/…".
            if sys.platform == "win32" and re.match(r"^/[A-Za-z]:/", self._path):
                return self._path[1:]
            return self._path

        path = Property(str, _gp, _sp, notify=pathChanged)

        def _gw(self):
            return self._watch

        def _sw(self, v):
            self._watch = bool(v)
            self.watchChangesChanged.emit()
            self._update_watch()

        watchChanges = Property(bool, _gw, _sw, notify=watchChangesChanged)
        printErrors = Property(bool, lambda s: s._print, lambda s, v: setattr(s, "_print", bool(v)), notify=printErrorsChanged)
        loadedProp = Property(bool, lambda s: s._loaded, notify=loadedOrAsyncChanged)

        def _update_watch(self):
            if self._watcher is not None:
                self._watcher.deleteLater()
                self._watcher = None
            path = self._native()
            if path and self._watch:
                w = QFileSystemWatcher(self)
                if os.path.exists(path):
                    w.addPath(path)
                d = os.path.dirname(path)
                if os.path.isdir(d):
                    w.addPath(d)
                w.fileChanged.connect(self._on_file)
                w.directoryChanged.connect(self._on_dir)
                self._watcher = w

        def _watched(self, path):
            return any(os.path.normcase(f) == os.path.normcase(path) for f in self._watcher.files())

        def _on_file(self, _p):
            path = self._native()
            if self._watcher is not None and os.path.exists(path) and not self._watched(path):
                self._watcher.addPath(path)
            self.fileChanged.emit()

        def _on_dir(self, _p):
            path = self._native()
            if self._watcher is not None and not self._watched(path) and os.path.exists(path):
                self._watcher.addPath(path)
                self.fileChanged.emit()

        @Slot(result=str)
        def text(self):
            return self._text

        @Slot()
        def reload(self):
            if not self._path:
                return
            try:
                with open(self._native(), encoding="utf-8") as fh:
                    self._text = fh.read()
            except OSError:
                self._text = ""
                self.loadFailed.emit(2)
                return
            self._loaded = True
            self.loadedOrAsyncChanged.emit()
            self.loaded.emit()

    instances: list = []

    def make_global(engine):
        obj = QuickshellGlobal()
        instances.append(obj)
        return obj

    qmlRegisterType(Singleton, "Quickshell", 1, 0, "Singleton")
    qmlRegisterSingletonType(QuickshellGlobal, "Quickshell", 1, 0, "Quickshell", make_global)
    qmlRegisterType(Process, "Quickshell.Io", 1, 0, "Process")
    qmlRegisterType(SplitParser, "Quickshell.Io", 1, 0, "SplitParser")
    qmlRegisterType(StdioCollector, "Quickshell.Io", 1, 0, "StdioCollector")
    qmlRegisterType(FileView, "Quickshell.Io", 1, 0, "FileView")

    _STATE.update(registered=True, app=app, instances=instances)
    return _STATE


FAKE_DAEMON = r'''
import json, sys, time, threading
out_lock = threading.Lock()
def emit(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
if mode == "silent":
    time.sleep(30)
    sys.exit(0)
sys.stderr.write("fake daemon starting\n"); sys.stderr.flush()
print("this line is not json", flush=True)
emit({"event": "ready", "version": 1})
jobs = 0
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    rid, method, params = req["id"], req["method"], req.get("params", {})
    if method == "hello":
        emit({"id": rid, "result": {"version": "1.0.0", "lang": "it",
              "strings": {"ui.ok": "Va bene", "test.hello": "Ciao {name}"},
              "config": {"wheel": {"scale": 1.25}, "language": "it"}, "warnings": []}})
    elif method == "menu":
        emit({"id": rid, "result": {"files": params.get("files"), "context": params.get("context"),
              "pages": [{"id": "main", "label": "Strumenti", "items": []}]}})
    elif method == "run":
        jobs += 1
        jid = "j%d" % jobs
        # job event before the response, to exercise placeholder adoption
        emit({"event": "job", "job": jid, "state": "queued", "action": params["action"], "label": "Comprimi",
              "files": [p.rsplit("/", 1)[-1] for p in params["files"]], "progress": None})
        emit({"id": rid, "result": {"job": jid}})
        emit({"event": "job", "job": jid, "state": "running", "progress": 0.5, "message": "half"})
        if params["action"] == "fail.me":
            emit({"event": "job", "job": jid, "state": "failed", "error": {"message": "Boom", "details": "tb", "install": "sudo pacman -S x"}})
        else:
            emit({"event": "job", "job": jid, "state": "done", "result": {"kind": "files", "outputs": [{"path": "/tmp/a b.png", "name": "a b.png"}], "undoable": True}})
    elif method == "undo":
        emit({"id": rid, "result": {"ok": True, "message": "Annullato"}})
    elif method == "error":
        emit({"id": rid, "error": {"message": "Nope", "details": "d"}})
    elif method == "slow":
        pass  # never answers
    elif method == "crash":
        sys.exit(3)
    else:
        emit({"id": rid, "result": {"echo": params}})
'''


def _run_qml(tmp_path: Path, name: str, body: str, *, timeout: float = 15.0, env: dict | None = None,
             allow_warnings: tuple[str, ...] = (), driver=None) -> dict:
    """Copy the shell tree to tmp, write tests/<name>.qml, run it until `done`, return its `report`.

    `driver(root, wait)` (optional) runs after loading, before waiting for `done`; `wait(ms)` spins the event loop.
    """
    _harness()
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtQml import QQmlApplicationEngine

    shell = tmp_path / "shell"
    if not shell.exists():
        shutil.copytree(SERVICES, shell / "services")
        shutil.copytree(COMPONENTS, shell / "components")
        stub = tmp_path / "stubs" / "Quickshell" / "Widgets"
        stub.mkdir(parents=True)
        (stub / "qmldir").write_text("module Quickshell.Widgets\nClippingRectangle 1.0 ClippingRectangle.qml\n")
        (stub / "ClippingRectangle.qml").write_text("import QtQuick\nRectangle { clip: true }\n")
    tests = shell / "tests"
    tests.mkdir(exist_ok=True)
    qml = tests / f"{name}.qml"
    qml.write_text(textwrap.dedent(body), encoding="utf-8")

    saved = {}
    for k, v in (env or {}).items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    messages: list[str] = []

    def handler(mode, ctx, msg):
        messages.append(msg)

    old = qInstallMessageHandler(handler)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(tmp_path / "stubs"))
    errors: list[str] = []
    engine.warnings.connect(lambda ws: errors.extend(w.toString() for w in ws))
    try:
        engine.load(QUrl.fromLocalFile(str(qml)))
        roots = engine.rootObjects()
        assert roots, "QML failed to load:\n" + "\n".join(errors + messages)
        root = roots[0]
        if driver is not None:
            def wait(ms):
                end = time.monotonic() + ms / 1000
                while time.monotonic() < end:
                    QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
                    time.sleep(0.005)

            driver(root, wait)
        loop = QEventLoop()
        deadline = time.monotonic() + timeout

        def poll():
            if root.property("done") or time.monotonic() > deadline:
                loop.quit()

        timer = QTimer()
        timer.timeout.connect(poll)
        timer.start(20)
        loop.exec()
        timer.stop()
        report = root.property("report")
        if hasattr(report, "toVariant"):
            report = report.toVariant()
        done = root.property("done")
    finally:
        engine.deleteLater()
        del engine
        QCoreApplication.processEvents()
        qInstallMessageHandler(old)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    noise = ("Cannot load font", "QFont::", "QFontDatabase", "no longer ships fonts", "OpenType support missing",
             "propagateSizeHints")
    bad = [m for m in errors + messages
           if not any(a in m for a in allow_warnings) and not any(n in m for n in noise)
           and ("Error" in m or "error" in m or "Warning" in m or "warning" in m or "TypeError" in m
                or "ReferenceError" in m or "Unable to assign" in m or "Cannot" in m or "undefined" in m
                or "Binding loop" in m or "Unknown" in m or "not a type" in m or "is not" in m)]
    assert done, "QML test did not finish. Report: %r\n%s" % (report, "\n".join(errors + messages))
    assert not bad, "unexpected QML messages:\n" + "\n".join(bad)
    assert isinstance(report, dict), report
    failures = report.get("failures") or []
    assert not failures, "\n".join(str(f) for f in failures) + "\nlog:\n" + "\n".join(messages)
    return report


QML_PRELUDE = """
import QtQuick
import "../services"
import "../components"

Window {
    id: win
    width: 640
    height: 480
    visible: true
    property bool done: false
    property var report: ({ failures: [] })
    function check(cond, what) { if (!cond) report.failures.push(String(what)); }
    function eq(a, b, what) { if (a !== b) report.failures.push(what + ": got " + JSON.stringify(a) + ", expected " + JSON.stringify(b)); }
    function finish() { done = true; }
"""


def _qml(body: str) -> str:
    return QML_PRELUDE + textwrap.dedent(body) + "\n}\n"


# --------------------------------------------------------------------------------------------- runtime tests
def test_theme_fallback_scheme_tokens_and_live_reload(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "caelestia").mkdir(parents=True)
    cfg_dir = tmp_path / "config"
    (cfg_dir / "caelestia").mkdir(parents=True)
    (cfg_dir / "caelestia" / "shell.json").write_text(json.dumps(
        {"appearance": {"transparency": {"enabled": True, "base": 0.8, "layers": 0.5}, "rounding": {"scale": 1}}}))
    scheme = {"name": "test", "flavour": "x", "mode": "light", "variant": "tonalspot",
              "colours": {"primary": "112233", "surface": "fafafa", "onSurface": "101010", "term0": "000000",
                          "primaryDim": "ffffff", "success": "00ff00"}}
    script = """
        property int phase: 0
        Timer {
            interval: 50; repeat: true; running: true
            onTriggered: {
                if (win.phase === 0 && Theme.transparency.enabled) {
                    eq(Theme.palette.m3primary.toString(), "#ffb0ca", "fallback primary");
                    eq(Theme.light, false, "fallback dark");
                    eq(Theme.schemeLoaded, false, "no scheme");
                    eq(Theme.rounding.small, 8, "rounding.small");
                    eq(Theme.rounding.extraLarge, 28, "rounding.extraLarge");
                    eq(Theme.spacing.medium, 12, "spacing.medium");
                    eq(Theme.padding.large, 16, "padding.large");
                    eq(Theme.font.size.normal, 13, "font.size.normal");
                    eq(Theme.font.iconSize.large, 24, "iconSize.large");
                    eq(Theme.anim.durations.expressiveFastSpatial, 350, "duration");
                    eq(Theme.anim.curves.emphasized.length, 12, "emphasized curve");
                    eq(Theme.anim.curves.standard.length, 6, "standard curve");
                    check(Theme.font.family.sans !== "", "sans family");
                    eq(Theme.font.family.icon, "Material Symbols Rounded", "icon family");
                    eq(Theme.scale, 1, "scale without backend");
                    eq(Math.round(Theme.transparency.base * 100), 80, "transparency base (dark)");
                    eq(Math.round(Theme.tPalette.m3surface.a * 100), 80, "tPalette layer 0 alpha");
                    eq(Math.round(Theme.tPalette.m3surfaceContainer.a * 100), 50, "tPalette layer 1 alpha");
                    check(Theme.tPalette.m3shadow.r >= 0, "black is lifted, not NaN");
                    eq(Theme.layer(Theme.palette.m3primary, 0).a.toFixed(2), "0.80", "layer(0)");
                    eq(Theme.s(10), 10, "s()");
                    win.phase = 1;
                } else if (win.phase === 1 && Theme.schemeLoaded) {
                    eq(Theme.palette.m3primary.toString(), "#112233", "scheme primary");
                    eq(Theme.palette.m3onSurface.toString(), "#101010", "scheme onSurface");
                    eq(Theme.palette.m3success.toString(), "#00ff00", "scheme success");
                    eq(Theme.palette.m3onSuccess.toString(), "#ffffff", "light success fallback");
                    eq(Theme.light, true, "light mode");
                    eq(Theme.schemeName, "test", "scheme name");
                    eq(Math.round(Theme.transparency.base * 100), 70, "transparency base (light)");
                    win.finish();
                }
            }
        }
    """
    # scheme.json appears only after start-up, to exercise the FileView directory watcher.
    _harness()
    from PySide6.QtCore import QTimer

    target = state_dir / "caelestia" / "scheme.json"
    QTimer.singleShot(1200, lambda: target.write_text(json.dumps(scheme), encoding="utf-8"))
    _run_qml(tmp_path, "ThemeTest", _qml(script), env={
        "XDG_STATE_HOME": _posix(state_dir),
        "XDG_CONFIG_HOME": _posix(cfg_dir),
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
    }, allow_warnings=("Backend", "Process failed", "definitely-not"))


def _posix(p: Path) -> str:
    """FileView paths in QML must start with '/' (XDG rule); on Windows map C:\\x to /C:/x which Qt accepts."""
    s = str(p).replace("\\", "/")
    return s if s.startswith("/") else "/" + s


def _backend_env(tmp_path: Path, mode: str = "normal") -> dict:
    daemon = tmp_path / "fake_daemon.py"
    daemon.write_text(FAKE_DAEMON, encoding="utf-8")
    return {
        "DROPWHEEL_BACKEND": "'%s' -u '%s' %s" % (sys.executable, daemon, mode),
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": None,
        "LC_MESSAGES": None,
    }


def test_backend_protocol_jobs_and_i18n(tmp_path):
    script = """
        property var log: []
        property int step: 0
        Component.onCompleted: {
            eq(I18n.t("ui.ok"), "OK", "builtin english before hello");
            eq(I18n.t("missing.key"), "missing.key", "unknown key");
            eq(I18n.t("ui.install_hint", { command: "sudo pacman -S x" }), "Install with: sudo pacman -S x", "placeholder");
            eq(I18n.formatSize(1), "1 byte", "size 1");
            eq(I18n.formatSize(999), "999 bytes", "size 999");
            eq(I18n.formatSize(1500), "1.5 kB", "size kB");
            eq(I18n.formatSize(2400000), "2.4 MB", "size MB");
            eq(I18n.formatSize(999960), "1.0 MB", "size rounding promotes");
            eq(I18n.formatDuration(65), "1:05", "duration");
            eq(I18n.formatDuration(3723), "1:02:03", "duration h");
            eq(I18n.formatPercent(0.425), "43%", "percent");
            eq(I18n.formatCount(12345), "12,345", "count en");
            eq(Backend.fileUrl("/home/u/a b#1.png"), "file:///home/u/a%20b%231.png", "fileUrl");
            eq(Backend.fileUrl("https://x.y/z"), "https://x.y/z", "fileUrl keeps urls");
            eq(Backend.pathFromUrl("file:///home/u/a%20b%231.png"), "/home/u/a b#1.png", "pathFromUrl");
            eq(Backend.pathFromUrl("file://localhost/tmp/x"), "/tmp/x", "pathFromUrl host");
            eq(JSON.stringify(Backend.parseCommand("python3 -m 'drop wheel' \\"a b\\" c\\\\ d")), JSON.stringify(["python3", "-m", "drop wheel", "a b", "c d"]), "parseCommand");
            eq(JSON.stringify(Backend.parseCommand("")), JSON.stringify(["dropwheel", "daemon"]), "parseCommand default");
            // queued before the daemon is ready
            Backend.call("echo", { a: 1 }, (r, e) => { win.log.push("echo:" + JSON.stringify(r)); });
        }
        Connections {
            target: Backend
            function onReady() {
                win.log.push("ready");
                eq(I18n.t("ui.ok"), "Va bene", "daemon strings");
                eq(I18n.t("test.hello", { name: "Ale" }), "Ciao Ale", "daemon placeholder");
                eq(I18n.lang, "it", "lang from daemon");
                eq(I18n.formatSize(1500), "1,5 kB", "italian decimal comma");
                eq(Theme.scale, 1.25, "scale from config");
                eq(Theme.rounding.small, 10, "scaled rounding");
                if (win.step === 0) {
                    win.step = 1;
                    Backend.menu(["file:///tmp/a%20b.png", "/tmp/c.txt"], "drag", (r, e) => {
                        eq(e, null, "menu error");
                        eq(JSON.stringify(r.files), JSON.stringify(["file:///tmp/a%20b.png", "/tmp/c.txt"]), "menu files");
                        eq(r.context, "drag", "menu context");
                        Backend.call("error", {}, (r2, e2) => {
                            eq(r2, null, "error result");
                            eq(e2.message, "Nope", "error message");
                            eq(e2.details, "d", "error details");
                            eq(e2.install, "", "error install");
                            Backend.call("slow", {}, (r3, e3) => {
                                eq(e3 && e3.code, "timeout", "timeout code");
                                win.log.push("timeout");
                                Jobs.run("image.compress", ["file:///tmp/a%20b.png"], { q: 1 }, "Compress");
                                eq(Jobs.count, 1, "placeholder row");
                                eq(Jobs.jobs[0].label, "Compress", "placeholder label");
                                eq(JSON.stringify(Jobs.jobs[0].files), JSON.stringify(["a b.png"]), "placeholder files");
                            }, 300);
                        });
                    });
                } else if (win.step === 2) {
                    win.log.push("restarted");
                    win.step = 3;
                    Jobs.run("fail.me", ["/tmp/x.pdf"], {}, "");
                }
            }
        }
        Connections {
            target: Jobs
            function onJobFinished(job) {
                win.log.push("finished:" + job.id + ":" + job.state);
                if (job.state === "done") {
                    eq(Jobs.count, 1, "placeholder adopted, no duplicate row");
                    eq(Jobs.model.count, 1, "model rows");
                    eq(Jobs.model.get(0).jobId, "j1", "model jobId");
                    eq(Jobs.model.get(0).job.state, "done", "model job role");
                    eq(Jobs.get("j1").result.outputs[0].name, "a b.png", "result outputs");
                    eq(Jobs.resolveId("local-1"), "j1", "resolveId");
                    eq(Jobs.activeCount, 0, "activeCount");
                    Jobs.undo("j1", (r, e) => {
                        eq(Jobs.get("j1").undone, true, "undone");
                        Jobs.dismiss("j1");
                        eq(Jobs.count, 0, "dismissed");
                        win.step = 2;
                        Backend.call("crash", {}, (r2, e2) => { eq(e2 && e2.code, "backend_exited", "crash error code"); });
                    });
                } else if (job.state === "failed") {
                    eq(job.error.message, "Boom", "job error");
                    eq(job.error.install, "sudo pacman -S x", "job install");
                    win.finish();
                }
            }
        }
    """
    report = _run_qml(tmp_path, "BackendTest", _qml(script), env=_backend_env(tmp_path), timeout=25,
                      allow_warnings=("non-JSON", "dropwheel-daemon", "exited with code", "fake daemon"))
    assert report["failures"] == []


CONFIG_DAEMON = r'''
import json, sys
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
emit({"event": "ready", "version": 1})
hellos = 0
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    rid, method, params = req["id"], req["method"], req.get("params", {})
    if method == "hello":
        hellos += 1
        lang = "it" if hellos == 1 else "en"
        emit({"id": rid, "result": {"version": "1.0.0", "lang": lang,
              "strings": {"ui.ok": "Va bene" if lang == "it" else "Okay"},
              "config": {"language": lang, "wheel": {"scale": 1.0}}, "warnings": []}})
        if hellos == 1:
            # the config file changed on disk: new language and a warning
            emit({"event": "config_changed", "config": {"language": "en", "wheel": {"scale": 1.5}},
                  "lang": "en", "lang_changed": True, "warnings": ["bad value"]})
    elif method == "run":
        emit({"event": "job", "job": "j1", "state": "queued", "action": params["action"], "label": "X",
              "files": ["a b.txt"], "paths": params["files"], "progress": None})
        emit({"id": rid, "result": {"job": "j1"}})
        emit({"event": "job", "job": "j1", "state": "done", "paths": params["files"],
              "result": {"kind": "text", "text": "hi", "outputs": [], "undoable": False}})
    else:
        emit({"id": rid, "result": {}})
'''


def test_backend_config_changed_and_job_paths(tmp_path):
    """{"event":"config_changed"} updates config/warnings and re-runs hello on a language change; job
    snapshots keep the full input paths of the job events."""
    daemon = tmp_path / "config_daemon.py"
    daemon.write_text(CONFIG_DAEMON, encoding="utf-8")
    env = _backend_env(tmp_path)
    env["DROPWHEEL_BACKEND"] = "'%s' -u '%s'" % (sys.executable, daemon)
    script = """
        property int readies: 0
        Connections {
            target: Backend
            function onReady() {
                win.readies++;
                if (win.readies === 2) {
                    eq(Backend.lang, "en", "lang after config_changed");
                    eq(I18n.t("ui.ok"), "Okay", "strings reloaded");
                    eq(Theme.scale, 1.0, "config from the second hello");
                    Jobs.run("text.copy", ["/tmp/a b.txt"], {}, "X");
                }
            }
            function onEvent(ev) {
                if (ev.event === "config_changed") {
                    eq(Backend.config.wheel.scale, 1.5, "config applied from the event");
                    eq(JSON.stringify(Backend.warnings), JSON.stringify(["bad value"]), "warnings from the event");
                }
            }
        }
        Connections {
            target: Jobs
            function onJobFinished(job) {
                eq(JSON.stringify(job.paths), JSON.stringify(["/tmp/a b.txt"]), "job paths");
                eq(JSON.stringify(job.files), JSON.stringify(["a b.txt"]), "job files");
                win.finish();
            }
        }
    """
    _run_qml(tmp_path, "ConfigChangedTest", _qml(script), env=env, timeout=20,
             allow_warnings=("dropwheel-daemon",))


def test_backend_missing_binary_and_hypr_without_hyprctl(tmp_path):
    script = """
        property int answered: 0
        Component.onCompleted: {
            Backend.call("menu", {}, (r, e) => {
                check(e !== null, "error expected without daemon");
                check(["timeout", "unavailable", "backend_exited"].indexOf(e.code) !== -1, "code " + e.code);
                check(e.message !== "", "translated message");
                win.answered++;
            }, 1500);
            Hypr.queryDrag((dragging, cursor) => {
                eq(dragging, false, "no drag without hyprctl");
                eq(cursor, null, "no cursor without hyprctl");
                eq(Hypr.available, false, "hyprctl unavailable");
                win.answered++;
            });
            eq(Hypr.isDragging([{ solitaryBlockedBy: null }, { solitaryBlockedBy: ["DND"] }]), true, "isDragging");
            eq(Hypr.isDragging([{ solitaryBlockedBy: ["WINDOWED"] }, {}]), false, "not dragging");
            eq(Hypr.isDragging("garbage"), false, "garbage");
            eq(Hypr.screenAt(100, 100).name, "DP-1", "screenAt first");
            eq(Hypr.screenAt(3000, 500).name, "HDMI-A-1", "screenAt second");
            eq(Hypr.screenAt(3000, 50).name, "HDMI-A-1", "screenAt nearest");
            eq(Hypr.screenByName("DP-1").width, 2560, "screenByName");
            const h = Hypr.watchDrag(60, () => { win.answered++; });
            check(h.active, "watch handle active");
        }
        Timer {
            interval: 50; repeat: true; running: true
            onTriggered: if (win.answered >= 3) win.finish()
        }
    """
    env = {
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel --x",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        "PATH": str(tmp_path),  # no hyprctl anywhere
    }
    _run_qml(tmp_path, "MissingTest", _qml(script), env=env, timeout=20,
             allow_warnings=("Backend", "Process failed", "definitely-not", "hyprctl"))


COMPONENTS_QML = r"""
    property int clicks: 0
    property int moves: 0
    property int toggles: 0
    property var activations: []
    property var picks: []
    property int phase: 0

    ScrollArea {
        id: page
        anchors.fill: parent
        Column {
            width: parent.width
            spacing: 4
            MaterialIcon { id: mi; text: "close"; fill: 1 }
            StyledText { id: st; text: "hello"; animate: true }
            StyledRect {
                id: sr; width: 40; height: 20; radius: 6; color: Theme.palette.m3primary
                StateLayer { id: sl; color: Theme.palette.m3onPrimary }
            }
            Item {
                width: 40; height: 20
                Elevation { anchors.fill: parent; level: 3; radius: 6 }
            }
            IconButton { id: ib; icon: "close"; type: IconButton.Tonal; tooltip: "Close it"; isToggle: true; onClicked: win.clicks++ }
            IconButton { id: ib2; icon: "delete"; type: ButtonBase.Text; danger: true; disabled: true }
            IconButton { icon: "add"; type: IconButton.Filled; isRound: true }
            IconButton { icon: "tune"; type: IconButton.Outlined }
            TextButton { id: tb; text: "Apply"; icon: "check"; type: TextButton.Filled; onClicked: win.clicks++ }
            TextButton { text: "Cancel"; type: TextButton.Outlined }
            TextButton { text: "Delete"; type: TextButton.Tonal; danger: true }
            M3Slider { id: slider; width: 200; from: 0; to: 100; stepSize: 5; value: 50; showTicks: true; unit: "%"; onMoved: win.moves++ }
            M3Slider { id: slider2; width: 200; from: -1; to: 1; value: 0.25; valueAlwaysVisible: true }
            M3Switch { id: sw; onToggled: win.toggles++ }
            M3Switch { checked: true; disabled: true }
            M3TextField {
                id: tf; width: 260; label: "Name"; placeholderText: "type here"; required: true
                validator: t => t.length >= 3 ? true : "too short"
                clearButton: true; suffixText: "px"; leadingIcon: "search"
            }
            M3TextField { id: pw; width: 260; label: "Password"; password: true; supportingText: "secret" }
            M3TextField { id: ml; width: 260; label: "Notes"; multiline: true; text: "a\nb"; validator: /^[a-z\n]*$/ }
            M3TextField { id: ext; width: 260; label: "External"; errorText: "Server said no"; mono: true }
            M3Dropdown {
                id: dd; width: 220; label: "Format"
                options: [{ value: "png", label: "PNG" }, { value: "jpg", label: "JPEG", icon: "image", description: "lossy" }, ["webp", "WebP"], "avif", 5]
                value: "jpg"
                onActivated: (v, i) => win.activations.push(v)
            }
            M3Dropdown { id: dd2; width: 220; options: []; label: "Empty" }
            ColorPicker { id: cp; width: 320; value: "#123456"; alpha: true; extraColors: ["#abc", "nope"]; onPicked: v => win.picks.push(v) }
            Row {
                spacing: 4
                Chip { id: chip; type: Chip.Filter; text: "Filter"; selected: true; onClicked: selected = !selected }
                Chip { type: Chip.Input; text: "input"; removable: true; icon: "image"; tooltip: "remove me" }
                Chip { type: Chip.Assist; text: "assist"; icon: "help"; elevated: true }
                Chip { type: Chip.Suggestion; text: "sugg"; disabled: true }
            }
            Row {
                spacing: 4
                CircularProgress { id: circ; indeterminate: true }
                CircularProgress { id: circ2; value: 0.4 }
                CircularProgress { value: 0; showTrack: false }
            }
            LinearProgress { id: lp; indeterminate: true; width: 200 }
            LinearProgress { id: lp2; value: 0.6; width: 200 }
            Row {
                spacing: 8
                FileIcon { id: fi; category: "pdf"; ext: "pdf"; count: 3 }
                FileIcon { id: fi2; thumb: "/nonexistent/dir/x.png"; category: "image"; ext: "png" }
                FileIcon { category: "weird"; size: 20 }
                FileIcon { icon: "folder"; category: "folder"; size: 96 }
            }
            ScrollArea {
                id: sa; width: 100; height: 50
                Column { width: parent.width; Repeater { model: 20; Rectangle { width: 10; height: 10; color: "red" } } }
            }
            Item {
                id: tipHost; width: 30; height: 30
                Tooltip { id: tip; text: "manual tooltip"; shown: true; position: Tooltip.Bottom }
            }
        }
    }

    Timer {
        interval: 150; running: true; repeat: true
        onTriggered: {
            if (win.phase === 0) {
                win.phase = 1;
                eq(mi.font.family, "Material Symbols Rounded", "icon font");
                check(st.font.family !== "", "text font");
                ib.trigger();
                eq(ib.internalChecked, true, "toggle icon button");
                ib2.trigger();
                tb.trigger();
                eq(win.clicks, 2, "clicks (disabled ignored)");
                sl.press(5, 5);
                slider.increase();
                eq(slider.value, 55, "slider step");
                slider.setValue(1000);
                eq(slider.value, 100, "slider clamp");
                eq(slider.valueText, "100%", "slider text");
                eq(win.moves, 2, "slider moved");
                eq(slider2.valueText, I18n.formatNumber(0.25, 2), "slider auto decimals");
                sw.toggle();
                eq(sw.checked, true, "switch");
                eq(win.toggles, 1, "toggled");
                tf.text = "ab";
                eq(tf.valid, false, "validator short");
                eq(tf.validationMessage, "too short", "validator message");
                eq(tf.hasError, false, "not shown before touch");
                eq(tf.validate(), false, "validate()");
                eq(tf.hasError, true, "shown after validate");
                tf.text = "abcd";
                eq(tf.valid, true, "valid");
                eq(tf.editor.text, "abcd", "editor synced");
                tf.text = "";
                eq(tf.validationMessage, I18n.t("ui.field.required"), "required");
                tf.clear();
                eq(ml.valid, true, "regex ok");
                ml.text = "ABC";
                eq(ml.valid, false, "regex fails");
                eq(ext.hasError, true, "external error");
                eq(pw.editor.echoMode, TextInput.Password, "password echo");
                eq(dd.currentIndex, 1, "dropdown index");
                eq(dd.currentText, "JPEG", "dropdown text");
                eq(dd.model.length, 5, "dropdown options");
                eq(dd.model[2].label, "WebP", "array option");
                eq(dd.model[3].label, "avif", "plain option");
                dd.value = 5;
                eq(dd.currentIndex, 4, "numeric value");
                dd.value = "5";
                eq(dd.currentIndex, 4, "loose compare");
                dd.openPopup();
                eq(dd.open, true, "popup open");
                dd2.openPopup();
                eq(dd2.open, false, "empty dropdown stays closed");
                eq(cp.normalise("#ABC"), "#aabbcc", "short hex");
                eq(cp.normalise("11223380"), "#11223380", "alpha hex");
                eq(cp.normalise("112233ff"), "#112233", "opaque alpha collapses");
                eq(cp.normalise("zz"), "", "invalid hex");
                eq(cp.toQtColor("#ff000080").toString(), "#80ff0000", "css to qt alpha order");
                eq(cp.fromQtColor(Qt.rgba(1, 0, 0, 0.5)), "#ff000080", "qt to css");
                cp.choose("#F00");
                eq(cp.value, "#ff0000", "choose");
                eq(JSON.stringify(win.picks), JSON.stringify(["#ff0000"]), "picked");
                chip.clicked();
                eq(chip.selected, false, "chip toggled by consumer");
                sa.scrollToBottom();
                check(sa.contentY > 0, "scroll area scrolls");
                circ.indeterminate = false;
                circ2.value = 0.9;
                lp2.value = 1;
            } else if (win.phase === 1) {
                win.phase = 2;
                dd.select(2);
                eq(dd.value, "webp", "dropdown select");
                eq(dd.open, false, "dropdown closed after select");
                eq(JSON.stringify(win.activations), JSON.stringify(["webp"]), "activated");
                eq(tip.open, true, "manual tooltip open");
                tip.shown = false;
                lp.indeterminate = false;
                lp.value = 0.3;
            } else if (win.phase === 2) {
                win.phase = 3;
                eq(tip.open, false, "tooltip closed");
            } else if (win.phase >= 3 && win.phase < 14) {
                win.phase++;   // let animations and the scale change (hello) run for a while
            } else if (win.phase === 14) {
                win.finish();
            }
        }
    }
"""


def test_components_instantiate_and_behave(tmp_path):
    _run_qml(tmp_path, "ComponentsTest", _qml(COMPONENTS_QML), env=_backend_env(tmp_path), timeout=25,
             allow_warnings=("non-JSON", "dropwheel-daemon", "fake daemon", "Process failed", "hyprpicker",
                             "/nonexistent/dir/x.png"))


INTERACTION_QML = r"""
    property int clicks: 0
    property int accepted: 0
    property var activated: []

    Column {
        x: 20; y: 20
        spacing: 12
        IconButton { objectName: "btn"; icon: "close"; tooltip: "Close"; onClicked: win.clicks++ }
        M3TextField { objectName: "field"; width: 240; label: "Name"; onAccepted: win.accepted++ }
        M3Dropdown {
            objectName: "dd"; width: 220; label: "Format"
            options: [{ value: "png", label: "PNG" }, { value: "jpg", label: "JPEG" }, { value: "webp", label: "WebP" }, { value: "avif", label: "AVIF" }]
            value: "png"
            onActivated: (v, i) => win.activated.push(v)
        }
        M3Slider { objectName: "slider"; width: 200; from: 0; to: 10; stepSize: 1; value: 5 }
        M3Switch { objectName: "switch" }
        Chip { objectName: "chip"; text: "Chip"; onClicked: selected = !selected }
    }
"""


def test_components_pointer_and_keyboard(tmp_path):
    _harness()  # skips when PySide6 is missing
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtQuick import QQuickItem

    def driver(root, wait):
        from PySide6.QtTest import QTest

        window = root  # Window root object is the QQuickWindow
        wait(300)

        def item(name):
            found = root.findChild(QQuickItem, name)
            assert found is not None, name
            return found

        def centre(it, dx=0.0, dy=0.0):
            p = it.mapToScene(QPointF(it.width() / 2 + dx, it.height() / 2 + dy))
            return QPoint(int(p.x()), int(p.y()))

        btn = item("btn")
        QTest.mouseMove(window, centre(btn, -3, -3))
        wait(50)
        QTest.mouseMove(window, centre(btn))
        wait(800)
        assert btn.property("hovered") is True, "hover not delivered"
        tips = [c for c in root.findChildren(QQuickItem) if c.property("text") == "Close" and c.property("delay") is not None]
        assert tips and tips[0].property("open") is True, "tooltip should open after hovering"
        bubbles = [c for c in root.findChildren(QQuickItem) if c.property("text") == "Close" and c.metaObject().className().startswith("StyledText") and c.isVisible()]
        assert bubbles, "tooltip bubble should be visible"
        # the bubble was re-parented to the window root and placed above the button
        b = bubbles[0].parentItem()
        assert b.parentItem() == root.contentItem(), "bubble not in the window root"
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(btn))
        wait(100)
        assert root.property("clicks") == 1

        field = item("field")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(field, 0, -10))
        wait(100)
        for ch in "Dropwheel":
            QTest.keyClick(window, ch)
        QTest.keyClick(window, Qt.Key.Key_Return)
        wait(100)
        assert field.property("text") == "Dropwheel"
        assert root.property("accepted") == 1

        dd = item("dd")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(dd, 0, -10))
        wait(400)
        assert dd.property("open") is True
        # the popup is re-parented to the window root: click the third row ("WebP")
        def walk(it):
            for ch in it.childItems():
                yield ch
                yield from walk(ch)

        rows = [c for c in walk(root.contentItem()) if c.property("text") == "WebP" and c.isVisible()]
        assert rows, "popup rows not found"
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(rows[0]))
        wait(300)
        assert dd.property("value") == "webp"
        assert dd.property("open") is False
        # keyboard: the dropdown keeps focus → Down opens, Down moves, Enter picks
        QTest.keyClick(window, Qt.Key.Key_Down)
        wait(200)
        assert dd.property("open") is True
        QTest.keyClick(window, Qt.Key.Key_Down)
        QTest.keyClick(window, Qt.Key.Key_Return)
        wait(200)
        assert dd.property("value") == "avif", dd.property("value")
        QTest.keyClick(window, Qt.Key.Key_Space)
        wait(100)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        wait(200)
        assert dd.property("open") is False

        slider = item("slider")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(slider.mapToScene(QPointF(slider.width() - 2, slider.height() / 2)).x()), centre(slider).y()))
        wait(100)
        assert slider.property("value") == 10
        QTest.keyClick(window, Qt.Key.Key_Left)
        wait(100)
        assert slider.property("value") == 9

        sw = item("switch")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(sw))
        wait(100)
        assert sw.property("checked") is True

        chip = item("chip")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(chip))
        wait(500)
        assert chip.property("selected") is True
        root.setProperty("done", True)

    _run_qml(tmp_path, "InteractionTest", _qml(INTERACTION_QML), env={  # noqa: E501
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
    }, timeout=10, driver=driver, allow_warnings=("Backend", "Process failed", "definitely-not", "hyprpicker"))


FAKE_HYPRCTL = r'''
import json, sys, time, pathlib
state = json.loads(pathlib.Path(__file__).with_name("hypr_state.json").read_text())
what = sys.argv[-1]
if state.get("mode") == "hang":
    time.sleep(10)
if state.get("mode") == "garbage":
    print("Hyprland IPC didn't respond in time")
    sys.exit(0)
if what == "monitors":
    print(json.dumps([
        {"id": 0, "name": "DP-1", "x": 0, "y": 0, "width": 2560, "height": 1440, "scale": 1.0, "solitaryBlockedBy": None},
        {"id": 1, "name": "HDMI-A-1", "x": 2560, "y": 200, "width": 1920, "height": 1080, "scale": 1.0,
         "solitaryBlockedBy": ["WINDOWED", "DND"] if state.get("dnd") else ["WINDOWED"]},
    ]))
elif what == "cursorpos":
    print(json.dumps({"x": state["cursor"][0], "y": state["cursor"][1]}))
'''


def test_hypr_with_fake_hyprctl(tmp_path):
    """Runs the real Hypr.qml against a Python stand-in for hyprctl (DND on/off, garbage output, hang)."""
    fake = tmp_path / "hyprctl_fake.py"
    fake.write_text(FAKE_HYPRCTL, encoding="utf-8")
    state = tmp_path / "hypr_state.json"

    def set_state(**kw):
        state.write_text(json.dumps(kw), encoding="utf-8")

    set_state(dnd=True, cursor=[3000, 500], mode="ok")

    script = r"""
        property int phase: 0
        property var handle: null
        property bool ended: false
        Component.onCompleted: {
            Hypr.queryTimeoutMs = 600;
            Hypr.queryDrag((dragging, cursor) => {
                eq(dragging, true, "DND detected");
                eq(cursor && cursor.x, 3000, "cursor x");
                eq(cursor && cursor.y, 500, "cursor y");
                eq(Hypr.dragActive, true, "dragActive");
                eq(Hypr.available, true, "available");
                eq(Hypr.monitors.length, 2, "monitors cached");
                eq(Hypr.screenAt(cursor.x, cursor.y).name, "HDMI-A-1", "screen under cursor");
                // two concurrent cursor queries share one process
                let n = 0;
                Hypr.cursorPos(p => { n++; });
                Hypr.cursorPos(p => { n++; if (n === 2) win.phase = 1; });
            });
        }
        Timer {
            interval: 50; repeat: true; running: true
            onTriggered: {
                if (win.phase === 1) {
                    win.phase = 2;
                    win.handle = Hypr.watchDrag(80, () => { win.ended = true; });
                } else if (win.phase === 2 && win.ended) {
                    win.phase = 3;
                    eq(win.handle.active, false, "watch stopped");
                    eq(Hypr.dragActive, false, "drag ended");
                    win.phase = 4;
                } else if (win.phase === 5) {
                    win.phase = 6;
                    Hypr.queryDrag((dragging, cursor) => {
                        eq(dragging, false, "garbage output → not dragging");
                        eq(cursor, null, "garbage cursor");
                        win.phase = 7;
                    });
                } else if (win.phase === 8) {
                    win.phase = 9;
                    const t0 = Date.now();
                    Hypr.queryDrag((dragging, cursor) => {
                        eq(dragging, false, "hang → not dragging");
                        check(Date.now() - t0 < 3000, "timeout respected");
                        win.finish();
                    });
                }
            }
        }
    """

    _harness()  # skips when PySide6 is missing
    from PySide6.QtCore import QTimer

    stage = {"n": 0}

    def tick():
        # flip the fake hyprctl state as the QML test advances
        try:
            phase = tick.root.property("phase") if hasattr(tick, "root") else None
        except RuntimeError:
            return
        if phase == 2 and stage["n"] == 0:
            stage["n"] = 1
            QTimer.singleShot(300, lambda: set_state(dnd=False, cursor=[3000, 500], mode="ok"))
        elif phase == 4 and stage["n"] == 1:
            stage["n"] = 2
            set_state(dnd=False, cursor=[1, 1], mode="garbage")
            tick.root.setProperty("phase", 5)
        elif phase == 7 and stage["n"] == 2:
            stage["n"] = 3
            set_state(dnd=False, cursor=[1, 1], mode="hang")
            tick.root.setProperty("phase", 8)

    def drive(root, wait):
        tick.root = root
        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(30)
        drive.timer = timer

    _run_qml(tmp_path, "HyprTest", _qml(script), env={
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        "DROPWHEEL_TEST_FAKE_HYPRCTL": str(fake),
    }, timeout=20, driver=drive, allow_warnings=("Backend", "Process failed", "definitely-not", "unexpected `hyprctl"))
    drive.timer.stop()


TYPING_QML = r"""
    property var seen: []
    property var picks: []

    Column {
        x: 20; y: 10
        spacing: 8
        M3TextField { objectName: "field"; width: 240; onTextEdited: win.seen.push(text) }
        ColorPicker { objectName: "cp"; width: 320; value: "#123456"; eyedropper: false; onPicked: v => win.picks.push(v) }
        M3TextField { objectName: "other"; width: 240 }
    }
"""


def test_text_edited_sees_new_text_and_hex_field_commits(tmp_path):
    """textEdited fires with `text` already updated, so typing or pasting a colour applies that colour; a value set
    from outside (swatch, host) replaces the text even while the hex field has focus, and is not undone on focus out."""
    _harness()  # skips when PySide6 is missing
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem

    def driver(root, wait):
        from PySide6.QtTest import QTest

        window = root
        no_mod = Qt.KeyboardModifier.NoModifier
        ctrl = Qt.KeyboardModifier.ControlModifier
        wait(300)

        def var(name):
            v = root.property(name)
            return v.toVariant() if hasattr(v, "toVariant") else v

        def walk(it):
            for ch in it.childItems():
                yield ch
                yield from walk(ch)

        def centre(it, dy=0.0):
            p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2 + dy))
            return QPoint(int(p.x()), int(p.y()))

        def click(it, dy=0.0):
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, no_mod, centre(it, dy))
            wait(60)

        def type_text(text):
            for ch in text:
                QTest.keyClick(window, ch)
            wait(30)

        def retype(text):
            QTest.keyClick(window, Qt.Key.Key_A, ctrl)
            type_text(text)

        field = root.findChild(QQuickItem, "field")
        other = root.findChild(QQuickItem, "other")
        cp = root.findChild(QQuickItem, "cp")
        hexf = next(c for c in walk(cp) if c.metaObject().className().startswith("M3TextField"))

        # consumers read the text of this keystroke, not of the previous one
        click(field)
        type_text("abc")
        assert var("seen") == ["a", "ab", "abc"]

        # typing: the last valid state wins ("ff0" is yellow on the way to "ff0000")
        click(hexf, -10)
        retype("ff0000")
        assert cp.property("value") == "#ff0000"
        assert var("picks") == ["#ffff00", "#ff0000"]
        assert hexf.property("text") == "ff0000", "typed text must not be rewritten while typing"

        # pasting fires textEdited once
        QGuiApplication.clipboard().setText("#00ff00")
        QTest.keyClick(window, Qt.Key.Key_A, ctrl)
        QTest.keyClick(window, Qt.Key.Key_V, ctrl)
        wait(30)
        assert hexf.property("text") == "#00ff00"
        assert cp.property("value") == "#00ff00"

        # Enter normalises the text to the value it stands for
        retype("#abc")
        assert cp.property("value") == "#aabbcc"
        QTest.keyClick(window, Qt.Key.Key_Return)
        wait(30)
        assert hexf.property("text") == "#aabbcc"

        # a swatch click leaves the focus in the field: its text must follow, and leaving must not bring the old colour back
        swatch = next(c for c in walk(cp) if c.property("hex") == "#f44336")
        click(swatch)
        assert hexf.property("text") == "#f44336"
        assert cp.property("value") == "#f44336"
        click(other)
        assert cp.property("value") == "#f44336"

        # same for a value set by the host (Reset) while the field has focus
        click(hexf, -10)
        cp.setProperty("value", "#010203")
        wait(30)
        assert hexf.property("text") == "#010203"
        click(other)
        assert cp.property("value") == "#010203"

        # invalid text changes nothing and stays visible as an error
        picks = len(var("picks"))
        click(hexf, -10)
        retype("zz")
        click(other)
        assert cp.property("value") == "#010203"
        assert hexf.property("text") == "zz"
        assert hexf.property("hasError") is True
        assert len(var("picks")) == picks
        root.setProperty("done", True)

    _run_qml(tmp_path, "TypingTest", _qml(TYPING_QML), env={
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
    }, timeout=10, driver=driver, allow_warnings=("Backend", "Process failed", "definitely-not"))


FAKE_HYPRPICKER = r'''
import json, pathlib, sys
log = pathlib.Path(__file__).with_name("hyprpicker_calls.jsonl")
calls = log.read_text().splitlines() if log.exists() else []
with log.open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
if len(calls) == 0:
    print("[LOG] output at 0x55d3abcdef12")
    print("#AABBCC")
elif len(calls) == 2:
    print("#112233")
# the second call stands for Esc: no output
'''


def test_eyedropper_with_fake_hyprpicker(tmp_path):
    """hyprpicker never gets --autocopy, starts only after the delay that lets the scrim go, runs once at a time."""
    fake = tmp_path / "hyprpicker_fake.py"
    fake.write_text(FAKE_HYPRPICKER, encoding="utf-8")
    fake_sh = tmp_path / "sh_fake.py"
    fake_sh.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    script = r"""
        property var picks: []
        property int phase: 0
        property real t0: 0
        property int refused: 0
        ColorPicker { id: cp; width: 320; value: "#123456"; onPicked: v => win.picks.push(v) }
        ColorPicker { id: cp2; width: 320; y: 200; value: "#654321"; onPicked: v => win.picks.push("cp2:" + v) }
        Loader {
            id: temp
            active: false
            sourceComponent: ColorPicker { value: "#000000"; onPicked: v => win.picks.push("destroyed:" + v) }
        }
        Timer {
            interval: 20; repeat: true; running: true
            onTriggered: {
                if (win.phase === 0 && Hypr.colorPickerAvailable) {
                    win.phase = 1;
                    Hypr.pickColorDelayMs = 250;
                    win.t0 = Date.now();
                    cp.pickFromScreen();
                    eq(Hypr.colorPicking, true, "colorPicking turns true at once, for the scrim");
                    eq(cp.picking, true, "picking");
                    cp2.pickFromScreen();
                    eq(cp2.picking, false, "one pick at a time");
                    eq(Hypr.pickColor(hex => { win.refused++; eq(hex, "", "refused pick answers empty"); }), false, "refused");
                } else if (win.phase === 1 && !Hypr.colorPicking) {
                    win.phase = 2;
                    check(Date.now() - win.t0 >= 250, "hyprpicker started before the delay");
                    eq(cp.value, "#aabbcc", "colour from stdout, not from a log line");
                    eq(cp.picking, false, "picking over");
                    eq(JSON.stringify(win.picks), JSON.stringify(["#aabbcc"]), "picked once");
                    eq(win.refused, 1, "refused callback");
                    Hypr.pickColorDelayMs = 0;
                    cp.pickFromScreen();
                    eq(cp.picking, true, "second pick");
                } else if (win.phase === 2 && !Hypr.colorPicking) {
                    win.phase = 3;
                    eq(cp.picking, false, "Esc ends the pick");
                    eq(cp.value, "#aabbcc", "Esc keeps the colour");
                    eq(win.picks.length, 1, "Esc picks nothing");
                    temp.active = true;
                    temp.item.pickFromScreen();
                    eq(Hypr.colorPicking, true, "third pick");
                    temp.active = false; // the dialog closed during the pick
                } else if (win.phase === 3 && !Hypr.colorPicking) {
                    eq(win.picks.length, 1, "a destroyed picker gets nothing");
                    win.finish();
                }
            }
        }
    """
    _run_qml(tmp_path, "EyedropperTest", _qml(script), env={
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        "DROPWHEEL_TEST_FAKE_SH": str(fake_sh),
        "DROPWHEEL_TEST_FAKE_HYPRPICKER": str(fake),
    }, timeout=20, allow_warnings=("Backend", "Process failed", "definitely-not"))
    calls = [json.loads(line) for line in (tmp_path / "hyprpicker_calls.jsonl").read_text().splitlines()]
    assert calls == [["-f", "hex"]] * 3


def test_eyedropper_binary_gone(tmp_path):
    """hyprpicker removed after the check: the pick ends with no colour instead of leaving the scrim off for good."""
    fake_sh = tmp_path / "sh_fake.py"
    fake_sh.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    script = r"""
        property int phase: 0
        property var answer: null
        Component.onCompleted: {
            eq(Hypr.colorPickerAvailable, false, "unknown before the check");
            eq(Hypr.pickColor(null), false, "no pick before the check");
            Hypr.checkColorPicker();
        }
        Timer {
            interval: 20; repeat: true; running: true
            onTriggered: {
                if (win.phase === 0 && Hypr.colorPickerAvailable) {
                    win.phase = 1;
                    Hypr.pickColorDelayMs = 0;
                    eq(Hypr.pickColor(hex => { win.answer = hex; }), true, "pick accepted");
                } else if (win.phase === 1 && win.answer !== null) {
                    eq(win.answer, "", "no colour");
                    eq(Hypr.colorPicking, false, "colorPicking cleared");
                    eq(Hypr.colorPickerAvailable, false, "known to be missing now");
                    win.finish();
                }
            }
        }
    """
    _run_qml(tmp_path, "EyedropperGoneTest", _qml(script), env={
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        "PATH": str(tmp_path),
        "DROPWHEEL_TEST_FAKE_SH": str(fake_sh),
        "DROPWHEEL_TEST_FAKE_HYPRPICKER": None,
    }, timeout=15, allow_warnings=("Backend", "Process failed", "definitely-not"))


def test_jobs_prune_keeps_rows_meant_to_stay(tmp_path):
    script = r"""
        property int phase: 0
        function config(timeout) {
            Backend.handleLine(JSON.stringify({ event: "config", config: { hud: { timeout_ms: timeout } } }));
        }
        function succeeded(id, result) {
            Jobs.handleEvent({ job: id, state: "done", action: "a", label: "L", files: ["x"], result: result });
        }
        function ended(id, state) {
            Jobs.handleEvent({ job: id, state: state, action: "a", label: "L", files: ["x"], error: { message: "e" } });
        }
        function rows() {
            return Jobs.jobs.map(j => j.id).sort().join(" ");
        }
        Component.onCompleted: {
            config(1);
            Jobs.pruneAfterMs = 0;
            Jobs.maxFailedKept = 1;
            const now = Date.now() / 1000;
            succeeded("plain", { kind: "files", outputs: [] });
            succeeded("share-live", { kind: "share", url: "http://x", expires: now + 600 });
            succeeded("share-unknown", { kind: "share", url: "http://x", expires: null });
            succeeded("share-over", { kind: "share", url: "http://x", expires: now - 60 });
            succeeded("held", { kind: "files", outputs: [] });
            Jobs.hold("held", true);
            succeeded("undoing", { kind: "files", outputs: [{ path: "/tmp/o" }], undoable: true });
            eq(Jobs.undo("undoing", null), true, "undo started");
            ended("cancelled", "cancelled");
            ended("failed-old", "failed");
            ended("failed-new", "failed");
            Jobs.handleEvent({ job: "active", state: "running", action: "a", label: "L", files: ["x"] });
        }
        Timer {
            interval: 40; repeat: true; running: true
            onTriggered: {
                if (win.phase === 0) {
                    win.phase = 1;
                    Jobs.prune();
                    eq(win.rows(), "active failed-new held share-live share-unknown undoing", "after the first prune");
                    Jobs.hold("held", false);
                    Jobs.prune();
                    eq(win.rows(), "active failed-new share-live share-unknown undoing", "released hold");
                    win.config(0); // "never"
                    win.succeeded("forever", { kind: "files", outputs: [] });
                    win.succeeded("share-over-2", { kind: "share", url: "http://x", expires: Date.now() / 1000 - 60 });
                    win.ended("cancelled-2", "cancelled");
                } else if (win.phase === 1) {
                    Jobs.prune();
                    eq(win.rows(), "active failed-new forever share-live share-over-2 share-unknown undoing", "timeout 0");
                    win.finish();
                }
            }
        }
    """
    _run_qml(tmp_path, "JobsPruneTest", _qml(script), env={
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
        "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
    }, timeout=15, allow_warnings=("Backend", "Process failed", "definitely-not"))


def test_jobs_daemon_crash_ends_a_pending_cancel(tmp_path):
    """A failed card must show the failure, not "Stopping…", when the daemon dies before confirming the cancel."""
    script = r"""
        property int step: 0
        Connections {
            target: Backend
            function onReady() {
                if (win.step !== 0)
                    return;
                win.step = 1;
                Jobs.handleEvent({ job: "jx", state: "running", action: "a", label: "L", files: ["f"], progress: 0.1 });
                Jobs.cancel("jx");
                eq(Jobs.get("jx").cancelling, true, "cancel pending");
                Backend.call("crash", {}, null);
            }
        }
        Timer {
            interval: 20; repeat: true; running: true
            onTriggered: {
                if (win.step === 1 && Jobs.get("jx").state === "failed") {
                    eq(Jobs.get("jx").cancelling, false, "cancelling cleared");
                    eq(Jobs.get("jx").error.code, "backend_exited", "error");
                    check(Jobs.get("jx").finishedAt > 0, "finishedAt");
                    win.finish();
                }
            }
        }
    """
    _run_qml(tmp_path, "JobsCrashTest", _qml(script), env=_backend_env(tmp_path), timeout=15,
             allow_warnings=("non-JSON", "dropwheel-daemon", "exited with code", "fake daemon", "cancel failed"))


def test_backend_non_utf8_paths_round_trip(tmp_path):
    """Same representation as the daemon (Python surrogateescape): URL → path → JSON line → daemon → JSON → shell."""
    from urllib.parse import quote_from_bytes

    raws = [b"/a/caf\xe9.jpg", b"/a/\xc3\xa9.png", b"/a/\xe9\xc3\xa9 b", b"/a/\xe2\x82", b"/a/\xe2\x82\xac",
            b"/a/\xf0\x9f\x98\x80", b"/a/\xf0\x9f\x98", b"/a/\xed\xa0\x80", b"/a/\xc0\xaf", b"/a/\xf4\x90\x80\x80",
            b"/a/\xff\xfe/xy"]
    cases = [["file://" + quote_from_bytes(r, safe="/"), r.decode("utf-8", "surrogateescape")] for r in raws]
    script = r"""
        Component.onCompleted: {
            const cases = @CASES@;
            for (const c of cases) {
                eq(Backend.pathFromUrl(c[0]), c[1], "pathFromUrl " + c[0]);
                eq(Backend.fileUrl(c[1]), c[0], "fileUrl " + c[0]);
            }
            eq(Backend.pathFromUrl("file:///a/100%25%E9%zz"), "/a/100%\uDCE9%zz", "literal percent");
            eq(Backend.pathFromUrl("file:///a/100%.jpg"), "/a/100%.jpg", "bare percent");
            eq(Backend.pathFromUrl("file://host/a/%E9"), "/a/\uDCE9", "host");
            eq(Backend.toPaths(["file:///a/caf%E9.jpg"])[0], "/a/caf\uDCE9.jpg", "toPaths");
        }
        Connections {
            target: Backend
            function onReady() {
                const p = "/a/caf\uDCE9 é 😀.jpg";
                Backend.call("echo", { p: p }, (r, e) => {
                    eq(e, null, "echo error");
                    eq(r && r.echo.p, p, "path sent to the daemon and back unchanged");
                    win.finish();
                });
            }
        }
    """.replace("@CASES@", json.dumps(cases))
    _run_qml(tmp_path, "NonUtf8Test", _qml(script), env=_backend_env(tmp_path), timeout=15,
             allow_warnings=("non-JSON", "dropwheel-daemon", "fake daemon"))


def test_theme_picks_installed_font_families(tmp_path):
    """Qt.fontFamilies() is a sequence, not a JS Array: the candidates must still be found in it."""
    _harness()  # skips when PySide6 is missing
    from PySide6.QtGui import QFontDatabase

    # The offscreen platform lists no system font: load a candidate of the mono list, if this machine has one.
    font_id = -1
    for path in ("C:/Windows/Fonts/CascadiaCode.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/dejavu/DejaVuSansMono.ttf"):
        if os.path.isfile(path):
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id != -1:
                break
    script = r"""
        Component.onCompleted: {
            const installed = {};
            const list = Qt.fontFamilies();
            win.report.fonts = list.length;
            win.report.mono = Theme.font.family.mono;
            for (let i = 0; i < list.length; i++)
                installed[String(list[i]).toLowerCase()] = true;
            const candidates = ["CaskaydiaCove NF", "CaskaydiaCove Nerd Font", "CaskaydiaCove Nerd Font Mono",
                "JetBrainsMono Nerd Font", "JetBrains Mono", "Cascadia Code", "DejaVu Sans Mono"];
            let mono = "monospace";
            for (const c of candidates) {
                if (installed[c.toLowerCase()] === true) {
                    mono = c;
                    break;
                }
            }
            eq(Theme.font.family.mono, mono, "mono family");
            eq(Theme.font.family.clock, installed["rubik"] === true ? "Rubik" : Theme.font.family.sans, "clock family");
            win.finish();
        }
    """
    try:
        report = _run_qml(tmp_path, "ThemeFontsTest", _qml(script), env={
            "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
            "XDG_STATE_HOME": _posix(tmp_path / "nostate"),
            "XDG_CONFIG_HOME": _posix(tmp_path / "noconfig"),
        }, timeout=10, allow_warnings=("Backend", "Process failed", "definitely-not"))
    finally:
        if font_id != -1:
            QFontDatabase.removeApplicationFont(font_id)
    if font_id != -1:
        assert report["fonts"] > 0
        assert report["mono"] != "monospace", "an installed candidate family was not found"
