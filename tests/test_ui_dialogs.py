"""Tests for shell/dialogs (DialogWindow, DialogSurface, the editors and result views) and the ui_dialogs locale fragment.

Two layers:

* static checks that always run: locale parity and placeholders, every ``I18n.t``/``I18n.tn`` key used by the dialogs
  exists (including the dynamically built ones), qmldir lists every file, balanced braces, no override of QQuickItem
  properties (``states``, ``data``…), the SPEC file list exists;
* runtime checks (skipped without PySide6 ≥ 6.9): DialogUtils.js through QJSEngine, and every editor / result view
  loaded inside the real DialogSurface in an offscreen engine, talking to a fake daemon (previews, run, util,
  share_stop).  The Quickshell stand-ins are the ones of tests/test_ui_base.py (``_harness``).  Each scenario drives
  the editor through its public API, presses Apply and checks the params sent to the job (SPEC §5.6).
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
DIALOGS = SHELL / "dialogs"
LOCALES = ROOT / "src" / "dropwheel" / "locales"

SPEC_FILES = ["DialogWindow.qml", "ParamForm.qml", "CropEditor.qml", "RedactEditor.qml", "TrimEditor.qml",
              "PageOrganizer.qml", "MetadataEditor.qml", "AdjustEditor.qml", "BackgroundEditor.qml",
              "TextResult.qml", "InfoResult.qml", "ShareResult.qml", "qmldir"]
HELPER_FILES = ["DialogSurface.qml", "EditorBase.qml", "Filmstrip.qml", "PreviewCanvas.qml", "PreviewState.qml",
                "PasswordPrompt.qml", "DialogUtils.js"]

# Keys built at runtime from a prefix + an id (see the QML sources).
DYNAMIC_KEYS = {
    "dialog.redact.style.": ["solid", "blur", "pixelate"],
    "dialog.adjust.": ["exposure", "brightness", "contrast", "temperature", "tint", "saturation", "sharpen",
                       "vignette"],
    "dialog.adjust.section.": ["light", "color", "detail"],
    "dialog.background.preset.": ["theme-primary", "theme-container", "theme-tertiary", "theme-surface", "ocean",
                                  "sunset", "grape", "mint", "peach", "candy", "sky", "aurora", "midnight"],
    "dialog.background.": ["padding", "radius", "shadow"],
}

# QQuickItem / QObject members that must not be redeclared (qmllint "property-override").
ITEM_MEMBERS = {"states", "state", "data", "children", "resources", "transitions", "transform", "visible", "enabled",
                "opacity", "parent", "x", "y", "z", "width", "height", "anchors", "layer", "focus", "clip", "scale",
                "rotation", "smooth", "antialiasing", "baselineOffset", "implicitWidth", "implicitHeight",
                "activeFocus", "objectName", "containmentMask", "visibleChildren"}


# --------------------------------------------------------------------------------------------- static helpers
def _locale(lang: str, name: str = "ui_dialogs") -> dict[str, str]:
    data = json.loads((LOCALES / lang / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _qml_files() -> list[Path]:
    return sorted(DIALOGS.glob("*.qml"))


def _strip(text: str) -> str:
    """Remove comments, strings and regex literals (roughly) so braces can be counted."""
    out = []
    i, n = 0, len(text)
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
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "/":
            # regex literal after an operator / opening token
            k = len(out) - 1
            while k >= 0 and out[k] in " \t\n":
                k -= 1
            prev = out[k] if k >= 0 else ""
            if prev in "(,=:[!&|?{};" or prev == "":
                j = i + 1
                while j < n and text[j] != "/" and text[j] != "\n":
                    if text[j] == "\\":
                        j += 1
                    elif text[j] == "[":
                        while j < n and text[j] != "]":
                            j += 2 if text[j] == "\\" else 1
                    j += 1
                if j < n and text[j] == "/":
                    j += 1
                    while j < n and text[j].isalpha():
                        j += 1
                    i = j
                    continue
        out.append(c)
        i += 1
    return "".join(out)


def _used_keys() -> tuple[set[str], set[str]]:
    plain, plural = set(), set()
    for f in list(_qml_files()) + [DIALOGS / "DialogUtils.js"]:
        text = f.read_text(encoding="utf-8")
        plain |= set(re.findall(r'I18n\.t\(\s*"([A-Za-z0-9_.\-]+)"\s*[,)]', text))
        plural |= set(re.findall(r'I18n\.tn\(\s*"([A-Za-z0-9_.\-]+)"', text))
    return plain, plural


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z0-9_]+)\}", text))


# --------------------------------------------------------------------------------------------- static tests
def test_expected_files_exist():
    for name in SPEC_FILES + HELPER_FILES:
        assert (DIALOGS / name).is_file(), name
    for lang in ("it", "en"):
        assert (LOCALES / lang / "ui_dialogs.json").is_file()


def test_locales_parity_and_placeholders():
    it, en = _locale("it"), _locale("en")
    assert set(it) == set(en), sorted(set(it) ^ set(en))
    for key in it:
        assert key.startswith("dialog."), key
        assert isinstance(it[key], str) and it[key].strip(), key
        assert isinstance(en[key], str) and en[key].strip(), key
        assert _placeholders(it[key]) == _placeholders(en[key]), key


def test_plural_keys_complete():
    for lang in ("it", "en"):
        data = _locale(lang)
        for key in data:
            if key.endswith(".one"):
                assert key[:-4] + ".other" in data, key
            if key.endswith(".other"):
                assert key[:-6] + ".one" in data, key


def test_every_used_key_is_defined():
    plain, plural = _used_keys()
    assert plain and plural
    for lang in ("it", "en"):
        table = {**_locale(lang, "ui_base"), **_locale(lang)}
        missing = [k for k in plain if k not in table and not k.endswith(".")]
        missing += [k + s for k in plural for s in (".one", ".other") if k + s not in table]
        for prefix, ids in DYNAMIC_KEYS.items():
            missing += [prefix + i for i in ids if prefix + i not in table]
        assert not missing, (lang, sorted(missing))


def test_dynamic_prefixes_are_really_used():
    text = "".join(f.read_text(encoding="utf-8") for f in _qml_files())
    for prefix in DYNAMIC_KEYS:
        assert f'I18n.t("{prefix}" +' in text, prefix


def test_placeholders_match_usage():
    """Placeholders passed from QML must exist in the translation (catches {w}/{width} typos)."""
    it = {**_locale("it", "ui_base"), **_locale("it")}
    for f in _qml_files():
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'I18n\.tn?\(\s*"([a-z_.0-9\-]+)"\s*,\s*(?:[^,{()]+,\s*)?\{([^{}]*)\}', text):
            key, body = m.group(1), m.group(2)
            args = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", body))
            candidates = [key] if key in it else [key + ".one", key + ".other"]
            for k in candidates:
                if k in it:
                    used = _placeholders(it[k]) - {"count"}
                    assert used <= args, (f.name, k, used, args)


def test_qmldir_lists_all_files():
    entries = {}
    for line in (DIALOGS / "qmldir").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3:
            entries[parts[0]] = parts[2]
    for name, file in entries.items():
        assert (DIALOGS / file).is_file(), file
        assert file == name + ".qml"
    for f in _qml_files():
        assert f.stem in entries, f.name


@pytest.mark.parametrize("path", _qml_files() + [DIALOGS / "DialogUtils.js"], ids=lambda p: p.name)
def test_sources_balanced(path: Path):
    s = _strip(path.read_text(encoding="utf-8"))
    for o, c in ("{}", "()", "[]"):
        assert s.count(o) == s.count(c), (path.name, o, s.count(o), s.count(c))


@pytest.mark.parametrize("path", _qml_files(), ids=lambda p: p.name)
def test_no_item_property_override(path: Path):
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:readonly\s+|required\s+|default\s+)*property\s+[\w<>.]+\s+(\w+)", text, re.M):
        assert m.group(1) not in ITEM_MEMBERS, (path.name, m.group(1))
    assert "focusReason" not in text, path.name  # not a QQuickItem property in Qt 6.12


@pytest.mark.parametrize("path", _qml_files(), ids=lambda p: p.name)
def test_qml_header_and_pragmas(path: Path):
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^// " + re.escape(path.stem) + r" — ", text, re.M), "missing header comment"
    if "delegate:" in text or "Repeater {" in text:
        assert text.startswith("pragma ComponentBehavior: Bound"), path.name


def test_dialog_window_contract():
    text = (DIALOGS / "DialogWindow.qml").read_text(encoding="utf-8")
    for needle in ('WlrLayershell.namespace: "dropwheel-dialog"', "WlrLayer.Overlay", "ExclusionMode.Ignore",
                   "WlrKeyboardFocus.Exclusive", 'color: "transparent"', "function open(node", "function showResult(",
                   "signal closed", "anchors.top: true", "anchors.bottom: true", "anchors.left: true",
                   "anchors.right: true", "Hypr.cursorPos", "Hypr.screenAt"):
        assert needle in text, needle
    for bad in ("implicitWidth:", "width:", "height:"):
        assert not re.search(r"^    " + bad, text, re.M), bad  # no window width/height


def test_surface_routes_every_dialog_kind():
    text = (DIALOGS / "DialogSurface.qml").read_text(encoding="utf-8")
    for kind, comp in (("crop", "CropEditor"), ("redact", "RedactEditor"), ("trim", "TrimEditor"),
                       ("pages", "PageOrganizer"), ("metadata", "MetadataEditor"), ("adjust", "AdjustEditor"),
                       ("background", "BackgroundEditor"), ("text", "TextResult"), ("info", "InfoResult"),
                       ("share", "ShareResult"), ("form", "ParamForm")):
        assert f'case "{kind}":' in text, kind
        assert f"{comp} {{" in text, comp
    assert "Jobs.run(" in text


# --------------------------------------------------------------------------------------------- runtime helpers
def _harness():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import test_ui_base  # noqa: WPS433 - shared Quickshell stand-ins
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"test_ui_base harness unavailable: {exc}")
    return test_ui_base._harness()


def _url_path(p: Path) -> str:
    """A local path that Backend.fileUrl() turns into a loadable file:// URL on Linux and Windows."""
    s = str(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", s):
        s = s[2:]  # drive-relative ("/Users/…") – same drive as the test run
    return s


FAKE_DAEMON = r'''
import json, sys, time
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
log = open(sys.argv[2], "a", encoding="utf-8")
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
emit({"event": "ready", "version": 1})
jobs = 0
png = cfg["png"]
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    rid, method, params = req["id"], req["method"], req.get("params", {})
    log.write(json.dumps({"method": method, "params": params}) + "\n"); log.flush()
    res = None
    err = None
    if method == "hello":
        res = {"version": "1", "lang": "en", "strings": cfg["strings"], "config": cfg.get("config") or {"language": "en"},
               "warnings": []}
    elif method == "preview":
        kind, path = params.get("kind"), params.get("path", "")
        if kind == "thumb":
            res = {"png": png, "width": 80, "height": 60}
        elif kind == "image":
            # "portrait" in the name: a 600 × 800 photo (mixed-shape batches)
            w, h = (600, 800) if "portrait" in path else (800, 600)
            res = {"png": png, "width": w, "height": h, "scale": 1}
        elif kind == "pdf_info":
            if "locked" in path and params.get("password") != "secret":
                res = {"pages": None, "sizes": [], "encrypted": True, "needs_password": True,
                       "wrong_password": bool(params.get("password"))}
            else:
                res = {"pages": 3, "sizes": [[595, 842], [595, 842], [842, 595]], "encrypted": "locked" in path,
                       "needs_password": False}
        elif kind == "pdf_page":
            res = {"png": png, "width_pt": 595, "height_pt": 842, "width": 400, "height": 566}
        elif kind == "pdf_search":
            res = {"matches": [{"page": 0, "rects": [[100, 100, 200, 120]]},
                               {"page": 2, "rects": [[50, 60, 90, 70], [10, 10, 20, 20]]}], "count": 3}
        elif kind == "video_info":
            res = {"duration": 12.5, "width": 1920, "height": 1080, "fps": 25, "has_audio": True, "codec": "h264"}
        elif kind == "video_frames":
            n = int(params.get("count", 12))
            res = {"frames": [{"t": round((i + 0.5) * 12.5 / n, 3), "png": png} for i in range(n)], "duration": 12.5}
        elif kind == "audio_waveform":
            res = {"png": png, "duration": 30.0}
        elif kind == "metadata":
            res = {"groups": [
                {"name": "File", "items": [{"key": "FileName", "value": "a.png", "editable": False, "id": "File:FileName"}]},
                {"name": "IFD0", "items": [{"key": "Artist", "value": "Ale", "editable": True, "id": "IFD0:Artist"},
                                           {"key": "Make", "value": "Canon", "editable": False, "id": "IFD0:Make"}]},
                {"name": "GPS", "items": [{"key": "GPSLatitude", "value": "45 deg", "editable": True, "id": "GPS:GPSLatitude"}]}],
                "editable": True, "source": "exiftool"}
        else:
            err = {"message": "unknown kind " + str(kind)}
    elif method == "run":
        jobs += 1
        jid = "j%d" % jobs
        emit({"id": rid, "result": {"job": jid}})
        emit({"event": "job", "job": jid, "state": "done", "action": params["action"], "label": "x",
              "files": [p.rsplit("/", 1)[-1] for p in params["files"]], "progress": None,
              "result": {"kind": "files", "outputs": [], "undoable": False}})
        continue
    elif method == "util":
        res = {"ok": True, "path": "/tmp/saved text.txt"}
    elif method == "share_stop":
        res = {"ok": True}
    else:
        res = {"ok": True}
    if err is not None:
        emit({"id": rid, "error": err})
    else:
        emit({"id": rid, "result": res})
'''


def _run_dialog_qml(tmp_path: Path, body: str, *, timeout: float = 60.0, driver=None,
                    config: dict | None = None) -> tuple[dict, list[dict]]:
    """Copy shell/{services,components,dialogs} to tmp, run tests/DialogTest.qml until `done`; return
    (report, daemon request log). `driver(root, wait)` (optional) runs right after loading; `config` is the
    configuration the fake daemon reports (e.g. ``{"wheel": {"scale": 1.5}}``)."""
    state = _harness()
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtQml import QQmlApplicationEngine

    shell = tmp_path / "shell"
    for name in ("services", "components", "dialogs"):
        shutil.copytree(SHELL / name, shell / name)
    stub = tmp_path / "stubs" / "Quickshell" / "Widgets"
    stub.mkdir(parents=True)
    (stub / "qmldir").write_text("module Quickshell.Widgets\nClippingRectangle 1.0 ClippingRectangle.qml\n")
    (stub / "ClippingRectangle.qml").write_text("import QtQuick\nRectangle { clip: true }\n")
    (shell / "tests").mkdir()
    qml = shell / "tests" / "DialogTest.qml"
    qml.write_text(textwrap.dedent(body), encoding="utf-8")

    img = QImage(800, 600, QImage.Format.Format_RGB32)
    img.fill(QColor("#3a6ea5"))
    png = tmp_path / "preview.png"
    assert img.save(str(png))
    strings = {}
    for name in ("ui_base", "ui_dialogs"):
        strings.update(_locale("en", name))
    cfg = tmp_path / "daemon.json"
    cfg.write_text(json.dumps({"png": _url_path(png), "strings": strings,
                               "config": dict({"language": "en"}, **(config or {}))}), encoding="utf-8")
    daemon = tmp_path / "fake_daemon.py"
    daemon.write_text(FAKE_DAEMON, encoding="utf-8")
    reqlog = tmp_path / "requests.jsonl"
    reqlog.write_text("", encoding="utf-8")

    env = {
        "DROPWHEEL_BACKEND": "'%s' -u '%s' '%s' '%s'" % (sys.executable, daemon, cfg, reqlog),
        "XDG_STATE_HOME": "/nonexistent-state",
        "XDG_CONFIG_HOME": "/nonexistent-config",
        "LANG": "en_US.UTF-8",
        "LC_ALL": None,
        "LC_MESSAGES": None,
        "XDG_CURRENT_DESKTOP": "Hyprland",
    }
    saved = {}
    for k, v in env.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    messages: list[str] = []
    old = qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
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
             "propagateSizeHints", "dropwheel-daemon", "Material Symbols")
    bad = [m for m in errors + messages
           if not any(n in m for n in noise)
           and ("Error" in m or "error" in m or "Warning" in m or "warning" in m or "TypeError" in m
                or "ReferenceError" in m or "Unable to assign" in m or "Cannot" in m or "undefined" in m
                or "Binding loop" in m or "Unknown" in m or "not a type" in m or "is not" in m)]
    requests = [json.loads(line) for line in reqlog.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert done, "QML test did not finish. Report: %r\n%s" % (report, "\n".join(errors + messages))
    assert not bad, "unexpected QML messages:\n" + "\n".join(bad)
    assert isinstance(report, dict), report
    failures = report.get("failures") or []
    assert not failures, "\n".join(str(f) for f in failures) + "\nlog:\n" + "\n".join(messages)
    assert state  # keep the harness alive
    return report, requests


# --------------------------------------------------------------------------------------------- runtime tests
def test_dialog_utils_js():
    _harness()
    from PySide6.QtQml import QJSEngine

    src = (DIALOGS / "DialogUtils.js").read_text(encoding="utf-8").replace(".pragma library", "")
    eng = QJSEngine()
    res = eng.evaluate(src)
    assert not res.isError(), res.toString()

    def js(expr: str):
        v = eng.evaluate(expr)
        assert not v.isError(), (expr, v.toString())
        return v.toVariant()

    # A name that is not UTF-8 stays a URL: the daemon decodes it to the same bytes, "%E9" would be a wrong literal name.
    assert js('decodePath("file:///a/caf%E9.jpg")') == "file:///a/caf%E9.jpg"
    assert js('jobTarget(normaliseFile("file:///a/caf%E9.jpg"))') == "file:///a/caf%E9.jpg"
    assert js('decodePath("file:///a/una%20foto.jpg")') == "/a/una foto.jpg"
    assert js('formatTime(3723.4567)') == "01:02:03.457"
    assert js('formatTime(59.9999, false)') == "00:01:00"
    assert js('parseTime("1:02:03.5")') == pytest.approx(3723.5)
    assert js('parseTime("2:05")') == 125
    assert js('parseTime("12,5 s")') == 12.5
    assert js('isNaN(parseTime("1:75"))') is True
    assert js('validatePages("1-3,5,8-z", 10)') == ""
    assert js('validatePages("r1,x4", 10)') == ""
    assert js('validatePages("1-30", 10)') == ""  # clamped by the backend
    assert js('validatePages("30", 10)') == "range"
    assert js('validatePages("1--3", 10)') == "syntax"
    assert js('validatePages("1-9:odd", 10)') == "syntax"  # no qpdf suffix in the backend
    assert js('validatePages("x4", 10)') == ""  # only exclusions: all the other pages
    assert js('validatePages("x1-10", 10)') == "none"
    assert js('validatePages("", 10)') == "empty"
    assert js('stripEllipsis("Rinomina\\u2026")') == "Rinomina"
    assert js('stripEllipsis("Size...")') == "Size"
    assert js('stripEllipsis("JPG")') == "JPG"
    assert js('isUrl("https://example.com/a?b=1")') is True
    assert js('isUrl("www.example.com")') is True
    assert js('isUrl("hello world")') is False
    assert js('urlFor("www.example.com")') == "https://www.example.com"
    assert js('ratioOf("16:9")') == pytest.approx(16 / 9)
    assert js('ratioOf("free")') is None
    assert js('parseNumber("1,5", false)') == 1.5
    assert js('isNaN(parseNumber("1.5", true))') is True
    files = js('JSON.stringify(normaliseFiles(["file:///tmp/a%20b.png", {path: "/x/y.pdf", category: "pdf"}, null, ""]))')
    data = json.loads(files)
    assert [f["path"] for f in data] == ["/tmp/a b.png", "/x/y.pdf"]
    assert data[0]["name"] == "a b.png" and data[0]["ext"] == "png"
    assert data[1]["category"] == "pdf"
    assert js('jobTarget(normaliseFile("https://e.x/v"))') == "https://e.x/v"


def test_page_validator_matches_the_backend():
    """validatePages accepts exactly what pdf.parse_pages accepts, so the form never blocks a valid selection nor lets
    through one the job would refuse."""
    _harness()
    from PySide6.QtQml import QJSEngine

    from dropwheel.actions.pdf import parse_pages

    src = (DIALOGS / "DialogUtils.js").read_text(encoding="utf-8").replace(".pragma library", "")
    eng = QJSEngine()
    res = eng.evaluate(src)
    assert not res.isError(), res.toString()
    atoms = ["1", "3", "5", "10", "11", "0", "z", "end", "fine", "r1", "r3", "r10", "r11", "r0", "odd", "dispari",
             "even", "pari", "all", "tutte", "*", "x4", "!3-4", "x1-z", "X2", "!-3", "x-", "7-", "-3", "-", "5-1",
             "1-100", "11-12", "1-9:odd", "1--3", "abc", "x", "!", "1-2-3", "r1-", "z-1", "1 - 3", "1..3", "1–3",
             "n", "xz", "xr1"]
    specs = set(atoms) | {f"{a},{b}" for a in atoms[:30] for b in atoms[:30]} | {f"{a} {b}" for a in atoms[:12] for b in atoms[:12]}
    for total in (1, 2, 10):
        for spec in specs:
            try:
                parse_pages(spec, total)
                backend_ok = True
            except ValueError:
                backend_ok = False
            js = eng.evaluate(f"validatePages({json.dumps(spec)}, {total})").toString()
            assert (js == "") == backend_ok, (spec, total, js)
    # Without a known page count only the syntax is checked.
    for spec, want in (("1-3", ""), ("x4", ""), ("500", ""), ("dispari", ""), ("1-9:odd", "syntax"), ("abc", "syntax")):
        assert eng.evaluate(f"validatePages({json.dumps(spec)}, null)").toString() == want, spec


DIALOG_TEST = r"""
import QtQuick
import "../services"
import "../components"
import "../dialogs"

Window {
    id: win
    width: 1400
    height: 900
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [] })
    property var applied: null
    property int stepIndex: -1
    property int actionIndex: 0
    property string phase: "boot"
    property double phaseStarted: Date.now()
    property int jobsStarted: 0

    readonly property string img: "__IMG__"
    readonly property var imageFile: ({ path: "/data/foto uno.png", name: "foto uno.png", category: "image", ext: "png", icon: "image" })
    readonly property var imageFile2: ({ path: "/data/foto due.jpg", name: "foto due.jpg", category: "image", ext: "jpg", icon: "image" })
    readonly property var pdfFile: ({ path: "/data/doc.pdf", name: "doc.pdf", category: "pdf", ext: "pdf", pages: 3 })
    readonly property var lockedPdf: ({ path: "/data/locked.pdf", name: "locked.pdf", category: "pdf", ext: "pdf" })
    readonly property var videoFile: ({ path: "/data/clip.mp4", name: "clip.mp4", category: "video", ext: "mp4" })
    readonly property var audioFile: ({ path: "/data/song.mp3", name: "song.mp3", category: "audio", ext: "mp3" })
    readonly property var landFile: ({ path: "/data/land.jpg", name: "land.jpg", category: "image", ext: "jpg" })
    readonly property var portraitFile: ({ path: "/data/portrait.jpg", name: "portrait.jpg", category: "image", ext: "jpg" })
    readonly property var photoA: ({ path: "/data/a.jpg", name: "a.jpg", category: "image", ext: "jpg" })
    readonly property var photoB: ({ path: "/data/b.jpg", name: "b.jpg", category: "image", ext: "jpg" })
    readonly property var photoC: ({ path: "/data/c.jpg", name: "c.jpg", category: "image", ext: "jpg" })

    function fail(what) { report.failures.push("[" + (steps[stepIndex] ? steps[stepIndex].name : "boot") + "] " + what); }
    function check(cond, what) { if (!cond) fail(what); }
    function find(item, pred) {
        if (!item)
            return null;
        if (pred(item))
            return item;
        const kids = item.children ?? [];
        for (let i = 0; i < kids.length; i++) {
            const r = find(kids[i], pred);
            if (r)
                return r;
        }
        return null;
    }
    function findText(t) { return find(ed(), it => it.text === t && it.visible === true && it.font !== undefined); }
    // Params cross a `var` signal (QVariantMap: keys sorted), so compare with sorted keys.
    function canon(v) {
        if (Array.isArray(v))
            return v.map(x => canon(x));
        if (v !== null && typeof v === "object") {
            const out = {};
            for (const k of Object.keys(v).sort())
                out[k] = canon(v[k]);
            return out;
        }
        return v;
    }
    function eq(a, b, what) {
        const sa = JSON.stringify(canon(a));
        const sb = JSON.stringify(canon(b));
        if (sa !== sb)
            fail(what + ": got " + sa + ", expected " + sb);
    }
    function ed() { return surface.editorItem; }
    function view() { return surface.editor; }

    readonly property var steps: [
        {
            name: "form",
            open: () => surface.open({ id: "test.form", label: "Compress", dialog: "form", params: [
                { name: "quality", type: "int", label: "Quality", default: 80, min: 1, max: 100 },
                { name: "mode", type: "select", label: "Mode", default: "b", options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] },
                { name: "pages", type: "pages", label: "Pages", default: "1-2" },
                { name: "hidden", type: "text", label: "Hidden", default: "x", visible_if: { mode: "a" } },
                { name: "flag", type: "bool", label: "Flag", default: true },
                { name: "when", type: "time", label: "When", default: 61.5 },
                { name: "level", type: "slider", label: "Level", default: 3, min: 0, max: 10, step: 1 },
                { name: "tint", type: "color", label: "Tint", default: "#112233" },
                { name: "span", type: "timerange", label: "Span", default: [1, 4.5] }
            ] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().generation >= 0, then: () => {
                    check(ed().valid, "form valid with defaults");
                    ed().setField("quality", 500, "out of range");
                    check(!ed().valid, "range error makes the form invalid");
                    ed().setField("quality", 90, "");
                } }
            ],
            verify: p => eq(p, { quality: 90, mode: "b", pages: "1-2", flag: true, when: 61.5, level: 3, tint: "#112233", span: { start: 1, end: 4.5 } }, "form params")
        },
        {
            name: "crop",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [{ name: "rect", type: "rect" }] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    eq(ed().pxRect, { x: 0, y: 0, w: 800, h: 600 }, "full frame");
                    ed().selectRatio("1:1");
                    ed().nudge(-500, 0);
                } }
            ],
            verify: p => { eq(p.rect, { x: 0, y: 0, w: 600, h: 600 }, "crop rect"); eq(p.ratio, "1:1", "crop ratio"); }
        },
        {
            name: "crop-undo",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.imageFile, win.imageFile2]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().selectRatio("16:9");
                    ed().selectRatio("4:3");
                    check(ed().undo(), "undo works");
                    eq(ed().cur.ratio, "16:9", "undo restores the ratio");
                } }
            ],
            verify: p => { eq(p.ratio, "16:9", "ratio"); eq(p.rect, { x: 0, y: 75, w: 800, h: 450 }, "16:9 rect"); check(p.per_file === undefined, "same for all: no per_file"); }
        },
        {
            name: "redact-images",
            open: () => surface.open({ id: "image.redact", label: "Redact", dialog: "redact", params: [{ name: "boxes", type: "boxes" }] }, [win.imageFile, win.imageFile2]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    check(!ed().canApply, "nothing to apply yet");
                    ed().setBoxes([{ page: null, x: 0.1, y: 0.1, w: 0.2, h: 0.25, style: "blur", color: "#000000", term: "" }]);
                    check(ed().lossyStyles, "blur warning");
                    ed().selectBox(0);
                    ed().nudge(8, 0);
                } }
            ],
            verify: p => {
                eq(p.boxes.length, 1, "one box");
                eq(p.boxes[0].style, "blur", "style");
                eq(p.boxes[0].page, null, "image page null");
                eq(Math.round(p.boxes[0].x * 800), 88, "nudged 8 px");
                check(p.per_file !== undefined && p.per_file["/data/foto due.jpg"].boxes.length === 0, "per_file for the second file");
            }
        },
        {
            name: "redact-pdf-search",
            open: () => surface.open({ id: "pdf.privacy.redact", label: "Redact", dialog: "redact", params: [{ name: "boxes", type: "boxes" }, { name: "password", type: "password" }] }, [win.pdfFile]),
            actions: [
                { until: () => ed() !== null && ed().ready && ed().pageCount === 3, then: () => {
                    ed().searchText = "secret word";
                    ed().search();
                } },
                { until: () => ed().boxes.length === 3, then: () => {
                    eq(ed().terms, ["secret word"], "term added");
                    eq(ed().searchText, "", "search field cleared");
                    eq(ed().page, 0, "first match page visible");
                    ed().goToPage(2);
                    eq(ed().pageBoxes.length, 2, "two boxes on page 3");
                } }
            ],
            verify: p => {
                eq(p.boxes.map(b => b.page), [0, 2, 2], "pages");
                check(p.boxes.every(b => b.style === "solid" && b.color === "#000000"), "pdf boxes are solid black");
                eq(p.terms, ["secret word"], "terms");
                check(Math.abs(p.boxes[0].x - 99 / 595) < 1e-5, "normalised x");
                check(p.password === undefined, "no password");
            }
        },
        {
            name: "redact-locked-pdf",
            open: () => surface.open({ id: "pdf.privacy.redact", label: "Redact", dialog: "redact", params: [] }, [win.lockedPdf]),
            actions: [
                { until: () => ed() !== null && ed().locked, then: () => ed().unlock("wrong") },
                { until: () => ed().locked, then: () => ed().unlock("secret") },
                { until: () => ed().ready, then: () => ed().setBoxes([{ page: 1, x: 0, y: 0, w: 0.5, h: 0.5, style: "solid", color: "#000000", term: "" }]) }
            ],
            verify: p => { eq(p.password, "secret", "password forwarded"); eq(p.boxes[0].page, 1, "page"); }
        },
        {
            name: "trim-video",
            open: () => surface.open({ id: "video.trim", label: "Trim", dialog: "trim", params: [] }, [win.videoFile]),
            actions: [
                { until: () => ed() !== null && ed().ready && ed().frames.length > 0, then: () => {
                    eq(ed().duration, 12.5, "duration");
                    check(!ed().canApply, "full range in keep mode is not a trim");
                    check(findText(I18n.t("dialog.trim.keys")) !== null, "video: the arrows move a frame");
                    ed().setRange(2, 5.25);
                    ed().activeHandle = "end";
                    ed().nudge(0.04);
                } }
            ],
            verify: p => eq(p, { start: 2, end: 5.29, mode: "keep", fast: false }, "trim params")
        },
        {
            name: "trim-audio-cut",
            open: () => surface.open({ id: "audio.trim", label: "Trim", dialog: "trim", params: [] }, [win.audioFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().setMode("cut");
                    check(!ed().canApply, "cannot cut everything");
                    check(findText(I18n.t("dialog.trim.keys_audio")) !== null, "audio: the arrows move 0.1 s");
                    check(findText(I18n.t("dialog.trim.keys")) === null, "audio: no frame wording");
                    ed().setRange(10, 20);
                } }
            ],
            verify: p => eq(p, { start: 10, end: 20, mode: "cut" }, "audio cut params")
        },
        {
            name: "pages",
            open: () => surface.open({ id: "pdf.pages.organize", label: "Organize", dialog: "pages", params: [{ name: "pageops", type: "pageops" }] }, [win.pdfFile]),
            actions: [
                { until: () => ed() !== null && ed().ready && ed().count === 3, then: () => {
                    check(!ed().canApply, "unchanged");
                    ed().selectAll();
                    const uids = ed().selection.slice();
                    ed().rotatePages(uids, 90);
                    ed().deletePages([uids[2]]);
                    ed().duplicatePages([uids[0]]);
                    eq(ed().count, 3, "count after duplicate");
                    check(ed().undo(), "undo duplicate");
                    eq(ed().count, 2, "count after undo");
                    ed().selection = [uids[0]];
                    ed().insertBlank();
                } }
            ],
            verify: p => eq(p.pageops, [{ src: 0, rotate: 90 }, { blank: true, rotate: 0, width: 842, height: 595 }, { src: 1, rotate: 90 }], "pageops")
        },
        {
            name: "metadata",
            open: () => surface.open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: [{ name: "metadata", type: "metadata" }] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    eq(ed().itemCount, 4, "items");
                    ed().query = "gps";
                    eq(ed().rows.length, 2, "filtered rows (header + item)");
                    ed().query = "";
                    ed().setValue("IFD0:Artist", "Bob");
                    ed().toggleRemove("GPS:GPSLatitude");
                    ed().toggleRemove("IFD0:Make");
                    eq(ed().changes.length, 2, "summary");
                } }
            ],
            verify: p => eq(p.metadata, { set: { "IFD0:Artist": "Bob" }, remove: ["GPS:GPSLatitude"], remove_all: false }, "metadata params")
        },
        {
            name: "metadata-remove-all",
            open: () => surface.open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().setRemoveAll(true);
                    check(ed().isRemoved("IFD0:Artist"), "artist removed by remove all");
                    ed().toggleRemove("IFD0:Artist");
                } }
            ],
            verify: p => eq(p.metadata, { set: { "IFD0:Artist": "Ale" }, remove: [], remove_all: true }, "keep one while removing all")
        },
        {
            name: "adjust",
            open: () => surface.open({ id: "image.adjust", label: "Adjust", dialog: "adjust", params: [{ name: "adjust", type: "adjust" }] }, [win.imageFile, win.imageFile2]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().setValue("contrast", 25);
                    ed().setValue("temperature", -40.4);
                    ed().setValue("vignette", 250);
                    ed().resetKey("vignette");
                    ed().showOriginal = true;
                    ed().showOriginal = false;
                } }
            ],
            verify: p => eq(p.adjust, { exposure: 0, brightness: 0, contrast: 25, temperature: -40, tint: 0, saturation: 0, sharpen: 0, vignette: 0 }, "adjust params")
        },
        {
            name: "background",
            open: () => surface.open({ id: "image.background.color", label: "Add BG", dialog: "background", params: [{ name: "background", type: "background" }] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    eq(ed().canvasSize, { w: 1120, h: 920 }, "auto canvas");
                    ed().chooseColor("#ff0000");
                    ed().setBg({ ratio: "16:9" }, null);
                    eq(ed().canvasSize, { w: 1636, h: 920 }, "16:9 canvas");
                    ed().setBgLive("padding", 40);
                } }
            ],
            verify: p => eq(p.background, { padding: 40, radius: 24, shadow: 24, ratio: "16:9", fill: { type: "color", colors: ["#ff0000"], angle: 135, image: "" } }, "background params")
        },
        {
            name: "background-gradient",
            open: () => surface.open({ id: "image.background.color", label: "Add BG", dialog: "background", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().choosePreset({ id: "aurora", colors: ["#00c6ff", "#0072ff", "#7f00ff"] });
                    ed().setAngleLive(90);
                    ed().chooseBlur();
                    check(ed().undo(), "undo blur");
                } }
            ],
            verify: p => eq(p.background.fill, { type: "gradient", colors: ["#00c6ff", "#0072ff", "#7f00ff"], angle: 90, image: "" }, "gradient fill")
        },
        {
            // A fixed ratio with "Same for all" holds on files of another shape.
            name: "crop-mixed-shapes",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().selectRatio("1:1");
                    eq(ed().pxRect, { x: 100, y: 0, w: 600, h: 600 }, "square on the landscape photo");
                } }
            ],
            verify: p => {
                eq(p.per_file["/data/land.jpg"], { rect: { x: 100, y: 0, w: 600, h: 600 }, ratio: "1:1" }, "landscape");
                eq(p.per_file["/data/portrait.jpg"], { rect: { x: 0, y: 100, w: 600, h: 600 }, ratio: "1:1" }, "portrait");
            }
        },
        {
            // A smaller frame: same relative size and centre on the other shape.
            name: "crop-mixed-scaled",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().selectRatio("1:1");
                    ed().commitField("w", 300);
                    eq(ed().pxRect, { x: 100, y: 0, w: 300, h: 300 }, "half of the largest square");
                } }
            ],
            verify: p => eq(p.per_file["/data/portrait.jpg"].rect, { x: 38, y: 50, w: 300, h: 300 }, "half of the portrait's largest square, same centre")
        },
        {
            // "Same for all" off: a file never opened keeps the ratio of the frame it was copied from.
            name: "crop-per-file-unopened",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().selectRatio("16:9");
                    ed().setSameForAll(false);
                } }
            ],
            verify: p => {
                eq(p.per_file["/data/land.jpg"], { rect: { x: 0, y: 75, w: 800, h: 450 }, ratio: "16:9" }, "landscape");
                eq(p.per_file["/data/portrait.jpg"], { rect: { x: 0, y: 231, w: 600, h: 338 }, ratio: "16:9" }, "portrait never opened");
            }
        },
        {
            // ... and the frame shown for that file is the one it gets.
            name: "crop-per-file-opened",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().selectRatio("1:1");
                    ed().setSameForAll(false);
                    ed().currentIndex = 1;
                } },
                { until: () => ed().oh === 800 && ed().ow === 600, then: () => {
                    eq(ed().pxRect, { x: 0, y: 100, w: 600, h: 600 }, "fitted on the portrait");
                    ed().nudge(0, 10);
                    eq(ed().pxRect, { x: 0, y: 110, w: 600, h: 600 }, "nudged");
                } }
            ],
            verify: p => {
                eq(p.per_file["/data/land.jpg"].rect, { x: 100, y: 0, w: 600, h: 600 }, "landscape untouched");
                eq(p.per_file["/data/portrait.jpg"].rect, { x: 0, y: 110, w: 600, h: 600 }, "portrait");
            }
        },
        {
            // Back to "Same for all": the frame on screen is the one for every file.
            name: "crop-same-for-all-again",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => {
                    ed().setSameForAll(false);
                    ed().commitField("w", 400);
                    ed().setSameForAll(true);
                    eq(ed().pxRect, { x: 0, y: 0, w: 400, h: 600 }, "same frame");
                    check(ed().undo(), "undo the switch");
                    check(ed().perFile, "per file again");
                } }
            ]
        },
        {
            // "Original" is each file's own shape; free frames stay normalised.
            name: "crop-original-ratio",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => ed().selectRatio("original") }
            ],
            verify: p => {
                eq(p.per_file["/data/land.jpg"], { rect: { x: 0, y: 0, w: 800, h: 600 }, ratio: "original" }, "landscape");
                eq(p.per_file["/data/portrait.jpg"], { rect: { x: 0, y: 0, w: 600, h: 800 }, ratio: "original" }, "portrait");
            }
        },
        {
            name: "crop-free-normalised",
            open: () => surface.open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [win.landFile, win.portraitFile]),
            actions: [
                { until: () => ed() !== null && ed().canApply, then: () => ed().commitField("w", 400) }
            ],
            verify: p => eq(p.per_file["/data/portrait.jpg"], { rect: { x: 0, y: 0, w: 300, h: 800 }, ratio: "free" }, "half the width of the portrait")
        },
        {
            // Files without edits are not part of the job: for the backend they would be failures.
            name: "metadata-one-of-three",
            open: () => surface.open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: [] }, [win.photoA, win.photoB, win.photoC]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    eq(ed().targetFiles().length, 0, "nothing edited yet");
                    ed().setValue("IFD0:Artist", "Bob");
                    eq(ed().targetFiles().map(f => f.name), ["a.jpg"], "only the edited file");
                    eq(ed().footerHint, I18n.tn("dialog.metadata.changes", 1) + " · " + I18n.t("dialog.metadata.edited_files", { edited: 1, total: 3 }), "hint");
                } }
            ],
            verify: p => {
                eq(p.metadata, { set: { "IFD0:Artist": "Bob" }, remove: [], remove_all: false }, "the edit");
                check(p.per_file === undefined, "one file edited: no per_file");
            }
        },
        {
            name: "metadata-two-of-three",
            open: () => surface.open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: [] }, [win.photoA, win.photoB, win.photoC]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().setValue("IFD0:Artist", "Bob");
                    ed().currentIndex = 2;
                } },
                { until: () => ed().currentFile.name === "c.jpg" && ed().ready, then: () => {
                    ed().toggleRemove("GPS:GPSLatitude");
                    eq(ed().targetFiles().map(f => f.name), ["a.jpg", "c.jpg"], "the edited files");
                    eq(ed().editedFiles, 2, "edited files");
                } }
            ],
            verify: p => {
                eq(Object.keys(p.per_file).sort(), ["/data/a.jpg", "/data/c.jpg"], "per_file only for the edited files");
                eq(p.per_file["/data/c.jpg"].metadata.remove, ["GPS:GPSLatitude"], "second edit");
            }
        },
        {
            // While everything is removed, an entry in `set` is what keeps a tag: going back to the original value keeps it.
            name: "metadata-keep-after-typing",
            open: () => surface.open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null && ed().ready, then: () => {
                    ed().setRemoveAll(true);
                    ed().toggleRemove("IFD0:Artist");
                    ed().typeValue("IFD0:Artist", "Alex");
                    ed().typeValue("IFD0:Artist", "Ale");
                    check(!ed().isRemoved("IFD0:Artist"), "still kept");
                    ed().undo();
                    eq(ed().edit.set["IFD0:Artist"], "Ale", "one undo step for the burst of typing");
                    ed().typeValue("IFD0:Artist", "Ale");
                } }
            ],
            verify: p => eq(p.metadata, { set: { "IFD0:Artist": "Ale" }, remove: [], remove_all: true }, "kept at the original value")
        },
        {
            // Blank pages alone do not make a PDF (pdf.err.no_pages_left).
            name: "pages-only-blanks",
            open: () => surface.open({ id: "pdf.pages.organize", label: "Organize", dialog: "pages", params: [] }, [win.pdfFile]),
            actions: [
                { until: () => ed() !== null && ed().ready && ed().count === 3, then: () => {
                    ed().selectAll();
                    ed().deletePages(ed().selection.slice());
                    eq(ed().count, 0, "no pages");
                    check(!ed().canApply, "no pages: nothing to save");
                    check(ed().footerHintError, "error hint");
                    ed().insertBlank();
                    eq(ed().count, 1, "one blank page");
                    check(!ed().canApply, "only blank pages: nothing to save");
                    check(ed().footerHintError, "error hint");
                    eq(ed().footerHint, I18n.t("dialog.pages.none_left"), "hint");
                    check(ed().undo() && ed().undo(), "undo");
                    eq(ed().count, 3, "pages back");
                    check(!ed().footerHintError, "no error");
                    ed().selection = [];
                    ed().insertBlank();
                    check(ed().canApply, "a blank page next to real ones is fine");
                } }
            ]
        },
        {
            name: "form-group-leaf-labels",
            open: () => surface.open({ id: "image.convert.jpg", label: "JPG", title: "Convert · JPG", dialog: "form", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null, then: () => {
                    eq(surface.title, "Convert · JPG", "title with the group");
                    eq(ed().applyLabel, I18n.t("ui.apply"), "a noun does not start a job");
                } }
            ]
        },
        {
            name: "form-group-leaf-ellipsis",
            open: () => surface.open({ id: "image.compress.size", label: "File size…", title: "Compress · File size…", dialog: "form", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null, then: () => {
                    eq(surface.title, "Compress · File size", "no ellipsis in the title");
                    eq(ed().applyLabel, I18n.t("ui.apply"), "generic verb");
                } }
            ]
        },
        {
            name: "form-standalone-label",
            open: () => surface.open({ id: "archive.extract_to", label: "Extract to…", title: "Extract to…", dialog: "form", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null, then: () => {
                    eq(surface.title, "Extract to", "no ellipsis in the title");
                    eq(ed().applyLabel, "Extract to", "the action's own verb");
                } }
            ]
        },
        {
            name: "form-without-title",
            open: () => surface.open({ id: "pdf.protect", label: "Protect…", dialog: "form", params: [] }, [win.imageFile]),
            actions: [
                { until: () => ed() !== null, then: () => {
                    eq(surface.title, "Protect", "no ellipsis in the title");
                    eq(ed().applyLabel, "Protect", "no ellipsis on the button");
                } }
            ]
        },
        {
            name: "form-pages-syntax",
            open: () => surface.open({ id: "pdf.pages.extract", label: "Extract", dialog: "form", params: [{ name: "pages", type: "pages", label: "Pages", default: "1" }] }, [win.pdfFile]),
            actions: [
                { until: () => ed() !== null && ed().generation >= 0, then: () => {
                    const p = ed().param("pages");
                    for (const ok of ["1-3", "odd", "dispari", "even", "x2", "2-", "-2", "!1-2", "r1", "1-99"])
                        eq(ed().validateText(p, ok), "", ok);
                    eq(ed().validateText(p, "1-3:odd"), I18n.t("dialog.form.pages_syntax"), "no qpdf suffix");
                    eq(ed().validateText(p, "9"), I18n.t("dialog.form.pages_range", { count: 3 }), "beyond the last page");
                    eq(ed().validateText(p, "x1-3"), I18n.t("dialog.form.pages_none"), "nothing left");
                } }
            ]
        },
        {
            name: "text-result",
            result: { id: "j90", label: "Read QR", files: ["code.png"], finishedAt: Date.now(), result: { kind: "text", text: "https://example.com/x", message: "1 code" } },
            actions: [
                { until: () => view() !== null && surface.kind === "text", then: () => {
                    check(view().isUrl, "url detected");
                    eq(view().lineCount, 1, "line count");
                    view().save();
                } },
                { until: () => view().savedPath !== "", then: () => { eq(view().savedPath, "/tmp/saved text.txt", "saved path"); view().copy(); } }
            ]
        },
        {
            name: "info-result",
            result: { id: "j91", label: "Properties", files: ["doc.pdf"], result: { kind: "info", info: [
                { group: "File", key: "Size", value: "1 MB" }, { group: "File", key: "Pages", value: 3 },
                { group: "PDF", key: "Producer", value: null }, { group: "PDF", key: "Obj", value: { a: 1 } }] } },
            actions: [
                { until: () => view() !== null && surface.kind === "info", then: () => {
                    eq(view().rows.length, 6, "rows with group headers");
                    check(view().plainText.indexOf("  Pages: 3") !== -1, "plain text");
                    check(view().plainText.indexOf('Obj: {"a":1}') !== -1, "object value");
                    view().query = "producer";
                    eq(view().rows.length, 2, "filtered");
                } }
            ]
        },
        {
            name: "share-result",
            result: { id: "j92", label: "Share", files: ["a.png"], finishedAt: Date.now() - 1000, result: { kind: "share", url: "http://192.168.1.2:4000/tok/", qr: "__IMG__", share_id: "abcd1234", expires: Date.now() / 1000 + 3600 } },
            actions: [
                { until: () => view() !== null && surface.kind === "share", then: () => {
                    check(view().active, "share active");
                    check(view().remainingMs > 3000000 && view().remainingMs <= 3600000, "countdown");
                    view().stop();
                } },
                { until: () => view().active === false, then: () => {} }
            ]
        },
        {
            name: "qr-result",
            result: { id: "j93", label: "QR", files: ["note.txt"], result: { kind: "qr", text: "hello", qr: "__IMG__" } },
            actions: [
                { until: () => view() !== null && surface.kind === "share", then: () => { check(!view().isShare, "plain qr"); eq(view().url, "hello", "qr text"); } }
            ]
        }
    ]

    DialogSurface {
        id: surface

        anchors.fill: parent
        onJobStarted: jobId => win.jobsStarted++
    }

    Connections {
        target: surface.editorItem
        ignoreUnknownSignals: true

        function onApply(params) {
            win.applied = JSON.parse(JSON.stringify(params));
        }
    }

    function startStep(i) {
        stepIndex = i;
        actionIndex = 0;
        applied = null;
        phaseStarted = Date.now();
        if (i >= steps.length) {
            eq(win.jobsStarted, steps.filter(s => s.verify !== undefined).length, "one job per editor step");
            done = true;
            return;
        }
        const s = steps[i];
        report.log.push("step " + s.name);
        phase = "actions";
        if (s.result)
            surface.showResult(s.result);
        else
            s.open();
        surface.reveal();
    }

    Timer {
        interval: 25
        repeat: true
        running: !win.done
        onTriggered: {
            try {
                win.tick();
            } catch (e) {
                win.fail("exception: " + e + "\n" + (e.stack ?? ""));
                win.startStep(win.stepIndex + 1);
            }
        }
    }

    function tick() {
        if (phase === "boot") {
            if (Backend.helloDone)
                startStep(0);
            return;
        }
        const s = steps[stepIndex];
        if (Date.now() - phaseStarted > 15000) {
            fail("timeout in phase " + phase + " (action " + actionIndex + ")");
            surface.close();
            phase = "closing";
            phaseStarted = Date.now();
            return;
        }
        if (phase === "actions") {
            if (actionIndex < s.actions.length) {
                const a = s.actions[actionIndex];
                if (a.until()) {
                    a.then();
                    actionIndex++;
                    phaseStarted = Date.now();
                }
                return;
            }
            if (s.verify === undefined) {
                surface.close();
                phase = "closing";
            } else {
                check(surface.editorItem.canApply, "canApply before Apply");
                surface.applyNow();
                phase = "applied";
            }
            phaseStarted = Date.now();
            return;
        }
        if (phase === "applied") {
            if (applied === null)
                return;
            s.verify(applied);
            phase = "closing";
            phaseStarted = Date.now();
            return;
        }
        if (phase === "closing" && !surface.active)
            startStep(stepIndex + 1);
    }
}
"""


def test_editors_and_results_in_dialog_surface(tmp_path):
    _harness()  # skips when PySide6 is missing
    png = _url_path(tmp_path / "preview.png")
    body = DIALOG_TEST.replace("__IMG__", png)
    report, requests = _run_dialog_qml(tmp_path, body, timeout=120)
    kinds = [r["params"].get("kind") for r in requests if r["method"] == "preview"]
    for kind in ("image", "thumb", "pdf_info", "pdf_page", "pdf_search", "video_info", "video_frames",
                 "audio_waveform", "metadata"):
        assert kind in kinds, kind
    runs = [r["params"] for r in requests if r["method"] == "run"]
    assert [r["action"] for r in runs] == [
        "test.form", "image.crop", "image.crop", "image.redact", "pdf.privacy.redact", "pdf.privacy.redact",
        "video.trim", "audio.trim", "pdf.pages.organize", "image.metadata", "image.metadata", "image.adjust",
        "image.background.color", "image.background.color",
        "image.crop", "image.crop", "image.crop", "image.crop", "image.crop", "image.crop",
        "image.metadata", "image.metadata", "image.metadata"]
    assert runs[0]["files"] == ["/data/foto uno.png"]
    assert runs[3]["files"] == ["/data/foto uno.png", "/data/foto due.jpg"]
    assert runs[20]["files"] == ["/data/a.jpg"]  # metadata-one-of-three: the untouched files stay out of the job
    assert runs[21]["files"] == ["/data/a.jpg", "/data/c.jpg"]
    locked = [r["params"] for r in requests if r["method"] == "preview" and r["params"].get("kind") == "pdf_info"
              and "locked" in r["params"].get("path", "")]
    assert [p.get("password") for p in locked] == [None, "wrong", "secret"]
    pages = [r["params"] for r in requests if r["method"] == "preview" and r["params"].get("kind") == "pdf_page"
             and "locked" in r["params"].get("path", "")]
    assert pages and all(p.get("password") == "secret" for p in pages)
    utils = [r["params"] for r in requests if r["method"] == "util"]
    assert {"op": "save_text", "text": "https://example.com/x", "ext": "txt", "name": "code (Text)"} in utils
    assert any(u["op"] == "copy_text" for u in utils)
    assert {"id": "abcd1234"} in [r["params"] for r in requests if r["method"] == "share_stop"]
    assert not report["failures"]


POINTER_TEST = r"""
import QtQuick
import "../services"
import "../components"
import "../dialogs"

Window {
    id: win
    width: 1400
    height: 900
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [] })
    // Gesture requests for the Python driver: {id, points: [[x, y]…], modifiers}; it sets `handled` = id when done.
    property var plan: ({ id: 0, points: [] })
    property int handled: 0
    property int stage: 0
    property double stageStarted: Date.now()

    readonly property var imageFile: ({ path: "/data/foto uno.png", name: "foto uno.png", category: "image", ext: "png" })
    readonly property var pdfFile: ({ path: "/data/doc.pdf", name: "doc.pdf", category: "pdf", ext: "pdf", pages: 3 })
    readonly property var videoFile: ({ path: "/data/clip.mp4", name: "clip.mp4", category: "video", ext: "mp4" })

    function fail(what) { report.failures.push("[stage " + stage + "] " + what); }
    function check(cond, what) { if (!cond) fail(what); }
    function find(item, pred) {
        if (!item)
            return null;
        if (pred(item))
            return item;
        const kids = item.children ?? [];
        for (let i = 0; i < kids.length; i++) {
            const r = find(kids[i], pred);
            if (r)
                return r;
        }
        return null;
    }
    function pt(item, x, y) {
        const p = item.mapToItem(null, x, y);
        return [Math.round(p.x), Math.round(p.y)];
    }
    function canvasOf(ed) {
        return find(ed, it => it.fitScale !== undefined && it.imageWidth !== undefined);
    }
    function gridOf(ed) {
        return find(ed, it => it.cellWidth !== undefined && it.cellHeight !== undefined && it.columns !== undefined);
    }
    function gesture(points, modifiers) {
        plan = { id: plan.id + 1, points: points, modifiers: modifiers ?? "" };
    }
    function settled() {
        return surface.shown && Date.now() - stageStarted > 900;
    }
    function next() {
        stage++;
        stageStarted = Date.now();
    }

    DialogSurface {
        id: surface

        anchors.fill: parent
    }

    Timer {
        interval: 30
        repeat: true
        running: !win.done
        onTriggered: {
            try {
                win.tick();
            } catch (e) {
                win.fail("exception: " + e + "\n" + (e.stack ?? ""));
                win.done = true;
            }
            if (Date.now() - win.stageStarted > 20000) {
                win.fail("timeout");
                win.done = true;
            }
        }
    }

    function tick() {
        const ed = surface.editorItem;
        if (plan.id !== handled)
            return;
        switch (stage) {
        case 0:
            if (!Backend.helloDone)
                return;
            surface.open({ id: "image.redact", label: "Redact", dialog: "redact", params: [] }, [imageFile]);
            surface.reveal();
            next();
            return;
        case 1: {
            if (!ed || !ed.ready || !settled())
                return;
            const c = canvasOf(ed);
            check(c !== null, "preview canvas found");
            gesture([pt(c, c.imageX + c.imageWidth * 0.2, c.imageY + c.imageHeight * 0.2), pt(c, c.imageX + c.imageWidth * 0.5, c.imageY + c.imageHeight * 0.6)]);
            next();
            return;
        }
        case 2: {
            check(ed.boxes.length === 1, "one box drawn, got " + ed.boxes.length);
            const b = ed.boxes[0] ?? {};
            check(Math.abs(b.x - 0.2) < 0.02 && Math.abs(b.y - 0.2) < 0.02 && Math.abs(b.w - 0.3) < 0.02 && Math.abs(b.h - 0.4) < 0.02, "box geometry " + JSON.stringify(b));
            check(ed.selected === 0, "new box selected");
            const c = canvasOf(ed);
            gesture([pt(c, c.imageX + c.imageWidth * 0.35, c.imageY + c.imageHeight * 0.4), pt(c, c.imageX + c.imageWidth * 0.45, c.imageY + c.imageHeight * 0.5)]);
            next();
            return;
        }
        case 3: {
            const b = ed.boxes[0] ?? {};
            check(Math.abs(b.x - 0.3) < 0.02 && Math.abs(b.y - 0.3) < 0.02 && Math.abs(b.w - 0.3) < 0.02, "moved box " + JSON.stringify(b));
            const c = canvasOf(ed);
            gesture([pt(c, c.imageX + c.imageWidth * (b.x + b.w), c.imageY + c.imageHeight * (b.y + b.h)), pt(c, c.imageX + c.imageWidth * 0.9, c.imageY + c.imageHeight * 0.9)]);
            next();
            return;
        }
        case 4: {
            const b = ed.boxes[0] ?? {};
            check(Math.abs(b.x + b.w - 0.9) < 0.02 && Math.abs(b.y + b.h - 0.9) < 0.02 && Math.abs(b.x - 0.3) < 0.02, "resized box " + JSON.stringify(b));
            // A click on empty space neither creates a box nor keeps the selection.
            const c = canvasOf(ed);
            const p = pt(c, c.imageX + c.imageWidth * 0.05, c.imageY + c.imageHeight * 0.95);
            gesture([p, p]);
            next();
            return;
        }
        case 5:
            check(ed.boxes.length === 1, "click did not add a box");
            check(ed.selected === -1, "click deselects");
            check(ed.undo() && ed.undo() && ed.undo(), "three undo steps");
            check(ed.boxes.length === 0, "undo back to nothing, got " + ed.boxes.length);
            surface.close();
            next();
            return;
        case 6:
            if (surface.active)
                return;
            surface.open({ id: "pdf.pages.organize", label: "Organize", dialog: "pages", params: [] }, [pdfFile]);
            surface.reveal();
            next();
            return;
        case 7: {
            if (!ed || !ed.ready || ed.count !== 3 || !settled())
                return;
            const grid = gridOf(ed);
            check(grid !== null, "grid found");
            const c0 = grid.itemAtIndex(0);
            const c2 = grid.itemAtIndex(2);
            gesture([pt(c0, c0.width / 2, c0.height * 0.25), pt(c2, c2.width * 0.75, c2.height * 0.25)]);
            next();
            return;
        }
        case 8: {
            const ops = ed.pageOps().map(o => o.src);
            check(JSON.stringify(ops) === "[1,2,0]", "page dragged to the end: " + JSON.stringify(ops));
            check(ed.dirty && ed.canApply, "dirty after drag");
            const c1 = gridOf(ed).itemAtIndex(1);
            const p = pt(c1, c1.width / 2, c1.height * 0.25);
            gesture([p, p], "ctrl");
            next();
            return;
        }
        case 9:
            check(ed.selection.length === 2, "ctrl+click adds to the selection: " + JSON.stringify(ed.selection));
            surface.close();
            next();
            return;
        case 10:
            if (surface.active)
                return;
            surface.open({ id: "video.trim", label: "Trim", dialog: "trim", params: [] }, [videoFile]);
            surface.reveal();
            next();
            return;
        case 11: {
            if (!ed || !ed.ready || ed.frames.length === 0 || !settled())
                return;
            const tl = find(ed, it => it.radius !== undefined && it.clip === true && it.height > 60 && it.height < 100 && it.width > 400);
            check(tl !== null, "timeline found");
            gesture([pt(tl, 1, tl.height / 2), pt(tl, tl.width * 0.4, tl.height / 2)]);
            next();
            return;
        }
        case 12:
            check(Math.abs(ed.start - 5) < 0.2, "start dragged to ~5 s: " + ed.start);
            check(Math.abs(ed.end - 12.5) < 0.001, "end untouched: " + ed.end);
            check(ed.canApply, "trim can apply");
            surface.close();
            next();
            return;
        case 13:
            if (!surface.active)
                done = true;
            return;
        }
    }
}
"""


def test_pointer_interactions(tmp_path):
    _harness()  # skips when PySide6 is missing
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    def driver(root, wait):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not root.property("done"):
            plan = root.property("plan")
            if hasattr(plan, "toVariant"):
                plan = plan.toVariant()
            pid = int(plan.get("id", 0)) if isinstance(plan, dict) else 0
            if pid and pid != int(root.property("handled") or 0):
                pts = [(int(x), int(y)) for x, y in plan["points"]]
                mods = (Qt.KeyboardModifier.ControlModifier if plan.get("modifiers") == "ctrl"
                        else Qt.KeyboardModifier.NoModifier)
                (x0, y0), (x1, y1) = pts[0], pts[-1]
                QTest.mouseMove(root, QPoint(x0, y0))
                wait(40)
                QTest.mousePress(root, Qt.MouseButton.LeftButton, mods, QPoint(x0, y0))
                wait(40)
                if (x0, y0) != (x1, y1):
                    for i in range(1, 13):
                        QTest.mouseMove(root, QPoint(round(x0 + (x1 - x0) * i / 12), round(y0 + (y1 - y0) * i / 12)))
                        wait(25)
                QTest.mouseRelease(root, Qt.MouseButton.LeftButton, mods, QPoint(x1, y1))
                wait(400)
                root.setProperty("handled", pid)
            wait(30)

    report, _requests = _run_dialog_qml(tmp_path, POINTER_TEST, timeout=30, driver=driver)
    assert not report["failures"]


PICKER_TEST = r"""
import QtQuick
import "../services"
import "../dialogs"

Window {
    id: win
    width: 900
    height: 700
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [] })
    property var suspends: []
    property var picked: []
    property int stage: 0
    property double stageStarted: Date.now()
    readonly property bool expectCancel: __EXPECT_CANCEL__

    function fail(what) { report.failures.push("[stage " + stage + "] " + what); }
    function check(cond, what) { if (!cond) fail(what); }
    function next() { stage++; stageStarted = Date.now(); }

    DialogSurface {
        id: surface

        anchors.fill: parent
        onSuspendRequested: s => win.suspends = win.suspends.concat([s])
    }

    Timer {
        interval: 25
        repeat: true
        running: !win.done
        onTriggered: {
            try {
                win.tick();
            } catch (e) {
                win.fail("exception: " + e);
                win.done = true;
            }
        }
    }

    function tick() {
        if (Date.now() - stageStarted > 15000) {
            fail("timeout");
            done = true;
            return;
        }
        if (stage === 0) {
            if (!surface.pickerAvailable)
                return;
            surface.open({ id: "x.form", label: "X", dialog: "form", params: [{ name: "out", type: "dir", label: "Out" }] }, ["/data/a b.png"]);
            surface.reveal();
            surface.pickFile({ directory: true, title: "Choose" }, p => win.picked = win.picked.concat([p]));
            check(JSON.stringify(win.suspends) === "[true]", "window suspended while picking: " + JSON.stringify(win.suspends));
            next();
        } else if (stage === 1) {
            if (win.picked.length === 0)
                return;
            check(win.picked[0] === (win.expectCancel ? "" : "/home/test/Scelta cartella"), "first pick: " + JSON.stringify(win.picked));
            check(JSON.stringify(win.suspends) === "[true,false]", "window restored: " + JSON.stringify(win.suspends));
            check(!surface.pickerRunning, "picker finished");
            if (win.expectCancel) {
                done = true;
                return;
            }
            surface.pickFile({ title: "Again", filters: ["*.png", "*.jpg"], filterName: "Images" }, p => win.picked = win.picked.concat([p]));
            next();
        } else if (stage === 2) {
            if (win.picked.length < 2)
                return;
            check(win.picked[1] === "", "cancelled pick gives an empty path: " + JSON.stringify(win.picked));
            check(JSON.stringify(win.suspends) === "[true,false,true,false]", "suspend sequence: " + JSON.stringify(win.suspends));
            done = true;
        }
    }
}
"""

FAKE_SH = r'''
import os
print(os.environ.get("DWTEST_PROBE_OUT", "zenity"))
'''

FAKE_ZENITY = r'''
import json, os, sys
log = os.environ["DWTEST_PICK_LOG"]
calls = []
if os.path.exists(log):
    with open(log, encoding="utf-8") as fh:
        calls = [json.loads(line) for line in fh if line.strip()]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
if not calls:
    print("/home/test/Scelta cartella")
    sys.exit(0)
sys.exit(1)  # the user cancelled the second dialog
'''


def test_file_picker_flow(tmp_path, monkeypatch):
    """pickFile(): zenity found by the probe, window suspended / restored, path delivered, cancel → ""."""
    _harness()  # skips when PySide6 is missing
    fake_sh = tmp_path / "fake_sh.py"
    fake_sh.write_text(FAKE_SH, encoding="utf-8")
    fake_zenity = tmp_path / "fake_zenity.py"
    fake_zenity.write_text(FAKE_ZENITY, encoding="utf-8")
    log = tmp_path / "zenity.jsonl"
    monkeypatch.setenv("DROPWHEEL_TEST_FAKE_SH", str(fake_sh))
    monkeypatch.setenv("DROPWHEEL_TEST_FAKE_ZENITY", str(fake_zenity))
    monkeypatch.setenv("DWTEST_PROBE_OUT", "zenity")
    monkeypatch.setenv("DWTEST_PICK_LOG", str(log))
    report, _requests = _run_dialog_qml(tmp_path, PICKER_TEST.replace("__EXPECT_CANCEL__", "false"), timeout=40)
    assert not report["failures"]
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(calls) == 2
    assert calls[0][0] == "--file-selection"
    assert "--directory" in calls[0] and "--title=Choose" in calls[0] and "--filename=/data/" in calls[0]
    assert "--directory" not in calls[1]
    assert "--file-filter=Images | *.png *.jpg" in calls[1] and "--title=Again" in calls[1]


def test_file_picker_that_cannot_start(tmp_path, monkeypatch):
    """A picker binary that fails to start (no exit, no output) still brings the dialog window back."""
    _harness()  # skips when PySide6 is missing
    fake_sh = tmp_path / "fake_sh.py"
    fake_sh.write_text(FAKE_SH, encoding="utf-8")
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("DROPWHEEL_TEST_FAKE_SH", str(fake_sh))
    monkeypatch.delenv("DROPWHEEL_TEST_FAKE_KDIALOG", raising=False)
    monkeypatch.setenv("DWTEST_PROBE_OUT", "kdialog")
    monkeypatch.setenv("PATH", str(empty))  # "kdialog" cannot be found → QProcess FailedToStart
    report, _requests = _run_dialog_qml(tmp_path, PICKER_TEST.replace("__EXPECT_CANCEL__", "true"), timeout=40)
    assert not report["failures"]


# Real keyboard and mouse input: what a field typed in does when Apply is clicked or Enter is pressed.
INPUT_TEST = r"""
import QtQuick
import "../services"
import "../components"
import "../dialogs"

Window {
    id: win
    width: 1400
    height: 900
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [] })
    // Input for the Python driver: {id, ops: [["type", "400"], ["key", "Return", "ctrl"], ["click", x, y]]}.
    property var plan: ({ id: 0, ops: [] })
    property int handled: 0
    property int stepIndex: 0
    property double stepStarted: Date.now()
    property var applied: null
    property int started: 0

    readonly property var imageFile: ({ path: "/data/foto.png", name: "foto.png", category: "image", ext: "png" })
    readonly property var videoFile: ({ path: "/data/clip.mp4", name: "clip.mp4", category: "video", ext: "mp4" })

    function fail(what) { report.failures.push("[step " + stepIndex + "] " + what); }
    function check(cond, what) { if (!cond) fail(what); }
    function canon(v) {
        if (Array.isArray(v))
            return v.map(x => canon(x));
        if (v !== null && typeof v === "object") {
            const out = {};
            for (const k of Object.keys(v).sort())
                out[k] = canon(v[k]);
            return out;
        }
        return v;
    }
    function eq(a, b, what) {
        const sa = JSON.stringify(canon(a));
        const sb = JSON.stringify(canon(b));
        if (sa !== sb)
            fail(what + ": got " + sa + ", expected " + sb);
    }
    function find(item, pred) {
        if (!item)
            return null;
        if (pred(item))
            return item;
        const kids = item.children ?? [];
        for (let i = 0; i < kids.length; i++) {
            const r = find(kids[i], pred);
            if (r)
                return r;
        }
        return null;
    }
    function ed() { return surface.editorItem; }
    function field(label) { return find(surface.editor, it => it.label === label && it.focusInput !== undefined); }
    function inline(text) { return find(ed(), it => it.entryValue !== undefined && it.text === text); }
    function input(ops) { plan = { id: plan.id + 1, ops: ops }; }
    function typeInto(f, text) { f.focusInput(); f.selectAll(); input([["type", text]]); }
    function clickApply() {
        const b = find(surface, it => it.text === ed().applyLabel && it.disabled !== undefined);
        const p = b.mapToItem(null, b.width / 2, b.height / 2);
        check(!b.disabled, "Apply is enabled");
        input([["click", Math.round(p.x), Math.round(p.y)]]);
    }
    function settled() { return surface.shown && ed() !== null && Date.now() - stepStarted > 900; }
    function opened() { return surface.active && !surface.closing; }
    function open(node, files) {
        applied = null;
        surface.open(node, files);
        surface.reveal();
    }
    function openCrop() { open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [imageFile]); }
    function openTrim() { open({ id: "video.trim", label: "Trim", dialog: "trim", params: [] }, [videoFile]); }
    function openMeta(extra) { open({ id: "image.metadata", label: "Metadata", dialog: "metadata", params: extra ?? [] }, [imageFile]); }

    readonly property var steps: [
        { until: () => Backend.helloDone, then: () => openCrop() },
        // Crop: typed width + mouse click on Apply.
        { until: () => settled() && ed().canApply, then: () => typeInto(field(I18n.t("dialog.crop.width")), "400") },
        { until: () => true, then: () => {
            eq(ed().pxRect.w, 800, "still only typed");
            clickApply();
        } },
        { until: () => applied !== null, then: () => {
            eq(applied.rect, { x: 0, y: 0, w: 400, h: 600 }, "typed width applied");
            check(!opened(), "closed");
        } },
        // Crop: Enter commits and stays open; Ctrl+Enter applies the field being typed.
        { until: () => !surface.active, then: () => openCrop() },
        { until: () => settled() && ed().canApply, then: () => {
            typeInto(field(I18n.t("dialog.crop.width")), "400");
            win.plan = { id: win.plan.id, ops: [["type", "400"], ["key", "Return"]] };
        } },
        { until: () => true, then: () => {
            eq(ed().pxRect.w, 400, "Enter commits");
            check(opened() && applied === null, "Enter does not apply");
            eq(field(I18n.t("dialog.crop.width")).text, "400", "field shows the value");
            typeInto(field(I18n.t("dialog.crop.height")), "300");
            win.plan = { id: win.plan.id, ops: [["type", "300"], ["key", "Return", "ctrl"]] };
        } },
        { until: () => applied !== null, then: () => eq(applied.rect, { x: 0, y: 0, w: 400, h: 300 }, "Ctrl+Enter applies the typed height") },
        // Crop: text the field cannot accept stops Apply with its error.
        { until: () => !surface.active, then: () => openCrop() },
        { until: () => settled() && ed().canApply, then: () => typeInto(field(I18n.t("dialog.crop.width")), "abc") },
        { until: () => true, then: () => clickApply() },
        { until: () => true, then: () => {
            check(opened() && applied === null, "invalid text does not apply");
            check(field(I18n.t("dialog.crop.width")).hasError, "the field shows its error");
            typeInto(field(I18n.t("dialog.crop.width")), "500");
        } },
        { until: () => true, then: () => clickApply() },
        { until: () => applied !== null, then: () => eq(applied.rect.w, 500, "fixed value applied") },
        // Crop: Reset also drops what is being typed.
        { until: () => !surface.active, then: () => openCrop() },
        { until: () => settled() && ed().canApply, then: () => typeInto(field(I18n.t("dialog.crop.width")), "400") },
        { until: () => true, then: () => {
            const b = find(surface, it => it.text === I18n.t("ui.reset") && it.disabled !== undefined);
            const p = b.mapToItem(null, b.width / 2, b.height / 2);
            input([["click", Math.round(p.x), Math.round(p.y)]]);
        } },
        { until: () => true, then: () => {
            eq(ed().pxRect.w, 800, "reset");
            eq(field(I18n.t("dialog.crop.width")).text, "800", "field follows the reset");
            field(I18n.t("dialog.crop.height")).focusInput();
            eq(ed().pxRect.w, 800, "nothing commits after the reset");
            // A field with the focus and nothing typed follows the value and commits nothing later.
            const w = field(I18n.t("dialog.crop.width"));
            w.focusInput();
            ed().selectRatio("1:1");
            eq(ed().pxRect.w, 600, "ratio applied");
            eq(w.text, "600", "focused field follows the value");
            field(I18n.t("dialog.crop.height")).focusInput();
            eq(ed().pxRect, { x: 100, y: 0, w: 600, h: 600 }, "nothing stale committed");
            surface.close();
        } },
        // Crop: switching file commits what is being typed to the file it was typed for.
        { until: () => !surface.active, then: () => open({ id: "image.crop", label: "Crop", dialog: "crop", params: [] }, [imageFile, { path: "/data/portrait.jpg", name: "portrait.jpg", category: "image", ext: "jpg" }]) },
        { until: () => settled() && ed().canApply, then: () => {
            ed().setSameForAll(false);
            typeInto(field(I18n.t("dialog.crop.width")), "400");
        } },
        { until: () => true, then: () => {
            eq(ed().currentIndex, 0, "first file");
            find(ed(), it => it.sameForAllToggled !== undefined).selected(1);
            eq(ed().currentIndex, 1, "second file");
            eq(ed().fileStates[0].w, 0.5, "typed width went to the first file");
            eq(ed().fileStates[1].touched, false, "second file untouched");
        } },
        { until: () => ed().ow === 600, then: () => {
            eq(field(I18n.t("dialog.crop.width")).text, "600", "field shows the second file");
            surface.close();
        } },
        // Trim: a time typed on the untouched range is what enables Apply.
        { until: () => !surface.active, then: () => openTrim() },
        { until: () => settled() && ed().ready, then: () => {
            check(!ed().canApply, "full range: nothing to apply");
            typeInto(field(I18n.t("dialog.trim.start")), "5");
        } },
        { until: () => ed().canApply, then: () => {
            eq(ed().start, 0, "still only typed");
            clickApply();
        } },
        { until: () => applied !== null, then: () => eq(applied, { start: 5, end: 12.5, mode: "keep", fast: false }, "typed start applied") },
        // Trim: Enter commits and stays open.
        { until: () => !surface.active, then: () => openTrim() },
        { until: () => settled() && ed().ready, then: () => {
            typeInto(field(I18n.t("dialog.trim.end")), "10");
            win.plan = { id: win.plan.id, ops: [["type", "10"], ["key", "Return"]] };
        } },
        { until: () => true, then: () => {
            eq(ed().end, 10, "Enter commits");
            check(opened() && applied === null, "Enter does not apply");
            eq(field(I18n.t("dialog.trim.end")).text, "00:00:10.000", "field shows the value");
            typeInto(field(I18n.t("dialog.trim.start")), "7");
            win.plan = { id: win.plan.id, ops: [["type", "7"], ["key", "Return", "ctrl"]] };
        } },
        { until: () => applied !== null, then: () => eq(applied, { start: 7, end: 10, mode: "keep", fast: false }, "Ctrl+Enter applies the typed start") },
        // Metadata: typing counts at once, and Apply saves it.
        { until: () => !surface.active, then: () => openMeta() },
        { until: () => settled() && ed().ready, then: () => {
            check(!ed().canApply, "nothing to save");
            const f = inline("Ale");
            f.forceActiveFocus();
            f.selectAll();
            input([["type", "mario rossi"]]);
        } },
        { until: () => true, then: () => {
            eq(ed().changeCount, 1, "counted while typing");
            check(ed().canApply && ed().dirty, "Apply enabled while typing");
            eq(ed().edit.set["IFD0:Artist"], "mario rossi", "the typed value");
            input([["key", "Escape"]]);
        } },
        { until: () => true, then: () => {
            check(opened(), "first Esc only reverts");
            eq(ed().changeCount, 0, "Esc goes back to the value the field started from");
            check(!ed().canApply, "nothing to save again");
            check(inline("Ale") !== null, "field shows the original");
            const f = inline("Ale");
            f.forceActiveFocus();
            f.selectAll();
            input([["type", "mario rossi"]]);
        } },
        { until: () => true, then: () => clickApply() },
        { until: () => applied !== null, then: () => eq(applied.metadata, { set: { "IFD0:Artist": "mario rossi" }, remove: [], remove_all: false }, "typed value saved") },
        // Metadata: with another change pending the typed value is not dropped; Enter in the field only confirms it.
        { until: () => !surface.active, then: () => openMeta() },
        { until: () => settled() && ed().ready, then: () => {
            ed().toggleRemove("GPS:GPSLatitude");
            const f = inline("Ale");
            f.forceActiveFocus();
            f.selectAll();
            input([["type", "bob"], ["key", "Return"]]);
        } },
        { until: () => true, then: () => {
            check(opened() && applied === null, "Enter in the value field does not apply");
            eq(ed().changeCount, 2, "both changes");
            const f = inline("bob");
            f.forceActiveFocus();
            f.selectAll();
            input([["type", "carlo"], ["key", "Return", "ctrl"]]);
        } },
        { until: () => applied !== null, then: () => eq(applied.metadata, { set: { "IFD0:Artist": "carlo" }, remove: ["GPS:GPSLatitude"], remove_all: false }, "typed value and removal") },
        // Metadata: Enter in the search box and in an embedded option does not save and close.
        { until: () => !surface.active, then: () => openMeta([{ name: "cover", type: "path", label: "Cover" }]) },
        { until: () => settled() && ed().ready, then: () => {
            ed().toggleRemove("GPS:GPSLatitude");
            const f = field(I18n.t("dialog.metadata.search"));
            f.focusInput();
            input([["type", "gps"], ["key", "Return"]]);
        } },
        { until: () => true, then: () => {
            check(opened() && applied === null, "Enter in the search box does not apply");
            eq(ed().query, "gps", "filtered");
            const f = field("Cover");
            f.focusInput();
            input([["type", "x"], ["key", "Return"]]);
        } },
        { until: () => true, then: () => {
            check(opened() && applied === null, "Enter in an embedded option does not apply");
            input([["key", "Return", "ctrl"]]);
        } },
        { until: () => applied !== null, then: () => {
            eq(applied.metadata.remove, ["GPS:GPSLatitude"], "Ctrl+Enter applies from the option");
            eq(applied.cover, "x", "option sent");
        } },
        // Standalone form: Enter applies.
        { until: () => !surface.active, then: () => open({ id: "x.form", label: "Do", dialog: "form", params: [{ name: "n", type: "int", label: "N", default: 1 }] }, [imageFile]) },
        { until: () => settled(), then: () => {
            typeInto(field("N"), "7");
            win.plan = { id: win.plan.id, ops: [["type", "7"], ["key", "Return"]] };
        } },
        { until: () => applied !== null, then: () => eq(applied.n, 7, "Enter applies the form") },
        // Info result: Enter in the filter does not close it.
        { until: () => !surface.active, then: () => {
            const info = [];
            for (let i = 0; i < 12; i++)
                info.push({ group: "G", key: "k" + i, value: i });
            surface.showResult({ id: "j1", label: "Info", files: ["a"], result: { kind: "info", info: info } });
            surface.reveal();
        } },
        { until: () => surface.shown && surface.kind === "info" && Date.now() - stepStarted > 900, then: () => {
            field(I18n.t("dialog.info.filter")).focusInput();
            input([["type", "k1"], ["key", "Return"]]);
        } },
        { until: () => true, then: () => {
            check(opened(), "Enter in the filter does not close the result");
            eq(surface.editor.query, "k1", "filtered");
            input([["key", "Escape"]]);
        } },
        { until: () => !surface.active, then: () => done = true }
    ]

    DialogSurface {
        id: surface

        anchors.fill: parent
        onJobStarted: jobId => win.started++
    }

    Connections {
        target: surface.editorItem
        ignoreUnknownSignals: true

        function onApply(params) {
            win.applied = JSON.parse(JSON.stringify(params));
        }
    }

    Timer {
        interval: 30
        repeat: true
        running: !win.done
        onTriggered: {
            if (win.plan.id !== win.handled)
                return;
            if (Date.now() - win.stepStarted > 15000) {
                win.fail("timeout");
                win.done = true;
                return;
            }
            const s = win.steps[win.stepIndex];
            try {
                if (s.until()) {
                    win.stepStarted = Date.now();
                    s.then();
                    win.stepIndex++;
                }
            } catch (e) {
                win.fail("exception: " + e + "\n" + (e.stack ?? ""));
                win.done = true;
            }
        }
    }
}
"""


def _input_driver(root, wait):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    keys = {"Return": Qt.Key.Key_Return, "Escape": Qt.Key.Key_Escape, " ": Qt.Key.Key_Space}
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline and not root.property("done"):
        plan = root.property("plan")
        if hasattr(plan, "toVariant"):
            plan = plan.toVariant()
        pid = int(plan.get("id", 0)) if isinstance(plan, dict) else 0
        if pid and pid != int(root.property("handled") or 0):
            for op in plan["ops"]:
                if op[0] == "type":
                    for ch in op[1]:
                        QTest.keyClick(root, keys.get(ch) or Qt.Key(ord(ch.upper())))
                        wait(10)
                elif op[0] == "key":
                    mods = (Qt.KeyboardModifier.ControlModifier if len(op) > 2 and op[2] == "ctrl"
                            else Qt.KeyboardModifier.NoModifier)
                    QTest.keyClick(root, keys[op[1]], mods)
                elif op[0] == "click":
                    point = QPoint(int(op[1]), int(op[2]))
                    QTest.mouseMove(root, point)
                    wait(40)
                    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                wait(60)
            wait(60)
            root.setProperty("handled", pid)
        wait(20)


def test_typed_values_and_enter_key(tmp_path):
    """Fields typed in: Apply and Ctrl+Enter commit them first, Enter only commits (F9, F10, F37)."""
    _harness()  # skips when PySide6 is missing
    report, requests = _run_dialog_qml(tmp_path, INPUT_TEST, timeout=20, driver=_input_driver)
    assert not report["failures"]
    runs = [r["params"] for r in requests if r["method"] == "run"]
    assert [r["params"]["rect"]["w"] for r in runs[:3]] == [400, 400, 500]
    assert runs[3]["params"]["start"] == 5
    assert runs[-1]["action"] == "x.form"


# The stills of the video trim keep a usable height at every wheel.scale (SPEC §7).
TRIM_LAYOUT_TEST = r"""
import QtQuick
import "../services"
import "../dialogs"

Window {
    id: win
    width: __W__
    height: __H__
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [], side: false, stills: 0, controls: 0, top: 0 })
    property double openedAt: 0

    function find(item, pred) {
        if (!item)
            return null;
        if (pred(item))
            return item;
        const kids = item.children ?? [];
        for (let i = 0; i < kids.length; i++) {
            const r = find(kids[i], pred);
            if (r)
                return r;
        }
        return null;
    }

    DialogSurface {
        id: surface

        anchors.fill: parent
    }

    Timer {
        interval: 30
        repeat: true
        running: !win.done
        onTriggered: {
            if (win.openedAt === 0) {
                if (!Backend.helloDone)
                    return;
                const files = [];
                for (let i = 0; i < __FILES__; i++)
                    files.push({ path: "/data/clip" + i + ".mp4", name: "clip" + i + ".mp4", category: "video", ext: "mp4" });
                surface.open({ id: "video.trim", label: "Trim", dialog: "trim", params: [] }, files);
                surface.reveal();
                win.openedAt = Date.now();
                return;
            }
            const ed = surface.editorItem;
            if (!ed || !ed.ready || ed.frames.length === 0 || Date.now() - win.openedAt < 900)
                return;
            const still = win.find(ed, it => it.time !== undefined && it.frame !== undefined && it.active !== undefined);
            const start = win.find(ed, it => it.focusInput !== undefined && it.value !== undefined);
            const p = start.mapToItem(ed, 0, 0);
            win.report = {
                failures: [],
                log: [],
                side: ed.sideControls,
                stills: still.height / Theme.scale,
                fieldBottom: p.y + start.height,
                editorHeight: ed.height
            };
            win.done = true;
        }
    }
}
"""


@pytest.mark.parametrize("w,h,scale,files,side", [
    (1366, 768, 1.5, 1, True),    # the size of the report: the stills collapsed to a line
    (1366, 768, 1.0, 1, False),
    (1366, 768, 1.0, 3, False),
    (1093, 614, 1.0, 3, True),    # 1366 × 768 at output scale 1.25, multi-file
    (1920, 1080, 1.5, 3, False),
    (1920, 1080, 0.8, 1, False),
])
def test_trim_stills_keep_their_height(tmp_path, w, h, scale, files, side):
    _harness()  # skips when PySide6 is missing
    body = (TRIM_LAYOUT_TEST.replace("__W__", str(w)).replace("__H__", str(h)).replace("__FILES__", str(files)))
    report, _requests = _run_dialog_qml(tmp_path, body, timeout=20, config={"wheel": {"scale": scale}})
    assert report["side"] is side
    assert report["stills"] >= 100, report  # in scale-independent units
    assert report["fieldBottom"] <= report["editorHeight"], report  # the time fields stay on screen


SCRIM_TEST = r"""
import QtQuick
import "../services"
import "../dialogs"

Window {
    id: win
    width: 640
    height: 480
    visible: true

    property bool done: false
    property var report: ({ failures: [], log: [] })
    property int phase: 0
    property var scrim: null

    function fail(message) {
        report.failures.push(message);
    }

    DialogSurface {
        id: surface

        anchors.fill: parent
    }

    Component.onCompleted: Hypr.checkColorPicker()

    Timer {
        interval: 20
        repeat: true
        running: !win.done
        onTriggered: {
            if (win.phase === 0 && Hypr.colorPickerAvailable) {
                win.phase = 1;
                for (let i = 0; i < surface.children.length; i++) {
                    if (surface.children[i].color !== undefined)
                        win.scrim = surface.children[i];
                    if (win.scrim)
                        break;
                }
                if (!win.scrim || Math.abs(win.scrim.color.a - 0.45) > 0.01)
                    win.fail("scrim at rest: " + (win.scrim ? win.scrim.color.a : "not found"));
                Hypr.pickColorDelayMs = 200;
                if (!Hypr.pickColor(null))
                    win.fail("pick refused");
                if (win.scrim.color.a !== 0)
                    win.fail("the scrim must go before hyprpicker starts: " + win.scrim.color.a);
            } else if (win.phase === 1 && !Hypr.colorPicking) {
                if (Math.abs(win.scrim.color.a - 0.45) > 0.01)
                    win.fail("scrim after the pick: " + win.scrim.color.a);
                win.done = true;
            }
        }
    }
}
"""


def test_scrim_clears_while_the_eyedropper_samples_the_screen(tmp_path, monkeypatch):
    fake_sh = tmp_path / "sh_fake.py"
    fake_sh.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    fake = tmp_path / "hyprpicker_fake.py"
    fake.write_text("print('#AABBCC')\n", encoding="utf-8")
    monkeypatch.setenv("DROPWHEEL_TEST_FAKE_SH", str(fake_sh))
    monkeypatch.setenv("DROPWHEEL_TEST_FAKE_HYPRPICKER", str(fake))
    report, _requests = _run_dialog_qml(tmp_path, SCRIM_TEST, timeout=20)
    assert not report["failures"]
