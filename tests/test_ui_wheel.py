"""Tests for the wheel / HUD QML (shell/shell.qml, shell/wheel, shell/hud) and the ui_wheel locale fragment.

* static checks that always run: locale parity, every I18n key used by these files exists, qmldir files, balanced
  braces, the verified Hyprland/Quickshell facts of SPEC §2.2 that are easy to break (pragmas, namespaces, layer,
  keyboard focus, mask shape, DropArea keys, "read drop.urls before accept", no synchronous hide in onDropped,
  IPC functions, GlobalShortcut names, draggable result chip);
* runtime checks of Wheel.qml (hit testing, navigation, drop decisions, spring loading, keyboard, label layout),
  Segment.qml and JobCard.qml in an offscreen Qt engine, reusing the Quickshell stand-ins of tests/test_ui_base.py.
  They are skipped when PySide6 (Qt >= 6.9) is not installed. WheelWindow.qml runs on stand-ins for PanelWindow and
  HyprlandFocusGrab (see _window_under_test); HotZone, HudWindow and shell.qml need the real Quickshell runtime and
  are checked statically here (and with qmllint by the developers), apart from single functions lifted from shell.qml.
"""

from __future__ import annotations

import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
WHEEL = SHELL / "wheel"
HUD = SHELL / "hud"
LOCALES = ROOT / "src" / "dropwheel" / "locales"

WHEEL_FILES = ["WheelWindow.qml", "Wheel.qml", "Segment.qml", "CenterHub.qml", "HotZone.qml"]
HUD_FILES = ["HudWindow.qml", "JobCard.qml", "ResultChip.qml"]


def _owned_qml() -> list[Path]:
    return [SHELL / "shell.qml"] + [WHEEL / f for f in WHEEL_FILES] + [HUD / f for f in HUD_FILES]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _locale(lang: str, name: str) -> dict[str, str]:
    path = LOCALES / lang / name
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _strip(text: str) -> str:
    """Remove comments, strings and regex literals so brackets can be counted."""
    out: list[str] = []
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
            q = c
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append('""')
            continue
        if c == "/":
            prev = "".join(out).rstrip()[-1:]
            if prev and prev in "(,=:[!&|?{};":
                j = i + 1
                while j < n and text[j] not in "/\n":
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


def _block(text: str, start: str) -> str:
    """The brace-balanced block that starts at the first occurrence of `start` (which must contain '{')."""
    i = text.index(start)
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError("unbalanced block " + start)


# --------------------------------------------------------------------------------------------- static tests
def test_files_exist_and_qmldirs():
    for path in _owned_qml():
        assert path.is_file(), path
    for folder, names in ((WHEEL, WHEEL_FILES), (HUD, HUD_FILES)):
        lines = [ln.split() for ln in _read(folder / "qmldir").splitlines() if ln.strip() and not ln.startswith("#")]
        listed = {parts[-1] for parts in lines}
        assert listed == set(names), (folder.name, listed)
        for parts in lines:
            assert len(parts) == 3 and parts[0] + ".qml" == parts[2] and parts[1] == "1.0", parts


def test_locale_parity_and_placeholders():
    it = _locale("it", "ui_wheel.json")
    en = _locale("en", "ui_wheel.json")
    assert en, "missing en/ui_wheel.json"
    assert set(it) == set(en), set(it) ^ set(en)
    for key, value in en.items():
        assert key.startswith(("wheel.", "hud.")), key
        assert isinstance(value, str) and value.strip(), key
        assert isinstance(it[key], str) and it[key].strip(), key
        ph_en = set(re.findall(r"\{(\w+)\}", value))
        ph_it = set(re.findall(r"\{(\w+)\}", it[key]))
        if key.endswith(".one"):
            # "e un altro" may spell out the number; it must not invent placeholders.
            assert ph_it <= ph_en and ph_en - {"count"} <= ph_it, key
        else:
            assert ph_it == ph_en, key
    # Plural pairs are complete.
    for key in en:
        if key.endswith(".one"):
            assert key[:-4] + ".other" in en, key
        if key.endswith(".other"):
            assert key[:-6] + ".one" in en, key


def _js_table(text: str, name: str) -> dict[str, str]:
    m = re.search(r"const %s = \{\n(.*?)\n\};" % name, text, re.S)
    assert m, name
    rows = re.findall(r'^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)",?$', m.group(1), re.M)
    return {k: json.loads('"%s"' % v) for k, v in rows}


def test_wheeltext_fallback_matches_locales():
    text = _read(WHEEL / "WheelText.js")
    assert ".pragma library" in text
    for lang, name in (("en", "EN"), ("it", "IT")):
        assert _js_table(text, name) == _locale(lang, "ui_wheel.json"), lang


def test_no_key_collision_with_other_fragments():
    mine = set(_locale("en", "ui_wheel.json"))
    for frag in sorted((LOCALES / "en").glob("*.json")):
        if frag.name == "ui_wheel.json":
            continue
        other = set(json.loads(frag.read_text(encoding="utf-8")))
        assert not (mine & other), (frag.name, sorted(mine & other))


def test_every_used_key_is_defined():
    known: dict[str, str] = {}
    for frag in ("ui_base.json", "ui_wheel.json"):
        known.update(_locale("en", frag))
    plain: set[str] = set()
    plural: set[str] = set()
    for path in _owned_qml():
        text = _read(path)
        plain.update(re.findall(r'\bI18n\.t\(\s*"([A-Za-z0-9_.]+)"', text))
        plural.update(re.findall(r'\bI18n\.tn\(\s*"([A-Za-z0-9_.]+)"', text))
        plain.update(re.findall(r'\bWT\.tr\(\s*I18n\s*,\s*"([A-Za-z0-9_.]+)"', text))
        plural.update(re.findall(r'\bWT\.trn\(\s*I18n\s*,\s*"([A-Za-z0-9_.]+)"', text))
        # wheel.* / hud.* strings go through WheelText.js (it keeps working while the daemon is down)
        assert not re.search(r'\bI18n\.tn?\(\s*"(wheel|hud)\.', text), path.name
    assert plain, "regex broken?"
    missing = sorted(k for k in plain if k not in known)
    missing += sorted(k + s for k in plural for s in (".one", ".other") if k + s not in known)
    assert not missing, missing


@pytest.mark.parametrize("path", _owned_qml(), ids=lambda p: p.name)
def test_qml_balanced_and_documented(path: Path):
    raw = _read(path)
    text = _strip(raw)
    for o, c in ("{}", "()", "[]"):
        assert text.count(o) == text.count(c), (path.name, o, text.count(o), text.count(c))
    assert "\t" not in raw, "tabs in " + path.name
    head = "\n".join(ln for ln in raw.splitlines() if not ln.startswith(("pragma", "//@ pragma"))).lstrip()
    assert head.startswith("//"), path.name + " lacks the API comment header"
    if "delegate:" in raw or "Repeater" in raw:
        assert "pragma ComponentBehavior: Bound" in raw, path.name


def test_shell_pragmas_and_settings():
    text = _read(SHELL / "shell.qml")
    lines = text.splitlines()
    first_import = next(i for i, ln in enumerate(lines) if ln.startswith("import"))
    pragmas = [ln.strip() for ln in lines[:first_import] if ln.startswith("//@ pragma")]
    assert "//@ pragma ShellId dropwheel" in pragmas
    assert "//@ pragma Env QS_NO_RELOAD_POPUP=1" in pragmas
    assert "//@ pragma DefaultEnv QSG_RENDER_LOOP=threaded" in pragmas
    assert re.search(r'settings\.watchFiles:\s*Quickshell\.env\("DROPWHEEL_DEV"\)\s*===\s*"1"', text)
    assert "ShellRoot {" in text
    for needed in ("LazyLoader", "WheelWindow", "DialogWindow", "HudWindow", "HotZone", "Variants"):
        assert needed in text, needed


def test_ipc_handler_and_shortcuts():
    text = _read(SHELL / "shell.qml")
    ipc = _block(text, "IpcHandler {")
    assert 'target: "dropwheel"' in ipc
    for sig in ("function open(payload: string): void", "function openAtCursor(): void", "function close(): void",
                "function setHotzone(enabled: bool): void", "function reload(): void", "function ping(): string"):
        assert sig in ipc, sig
    assert 'return "pong"' in ipc
    shortcuts = re.findall(r'GlobalShortcut \{\s*appid: "([^"]+)"\s*name: "([^"]+)"', text)
    assert ("dropwheel", "shift") in shortcuts and ("dropwheel", "open") in shortcuts, shortcuts
    # Both signals are handled (Hyprland may defer a bind to the key release) with the 250 ms debounce.
    assert text.count("onPressed:") >= 2 and text.count("onReleased:") >= 2
    assert "< 250" in text


def test_wheel_window_verified_facts():
    text = _read(WHEEL / "WheelWindow.qml")
    assert "WlrLayershell.layer: WlrLayer.Overlay" in text
    assert 'WlrLayershell.namespace: "dropwheel-wheel"' in text
    assert "exclusionMode: ExclusionMode.Ignore" in text
    assert 'color: "transparent"' in text
    # None in drag mode; click mode is Exclusive only for a surface created in click mode (see test_wheel_window_*).
    focus = _block(text, "WlrLayershell.keyboardFocus: {")
    assert 'root.mode !== "click" || wheelItem.closing' in focus and "return WlrKeyboardFocus.None;" in focus
    assert "priv.exclusiveSurface ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand" in focus
    assert "shape: RegionShape.Ellipse" in text
    assert 'keys: ["text/uri-list"]' in text
    dropped = _block(text, "onDropped: drop => {")
    assert dropped.index("drop.urls") < dropped.index("drop.accept(Qt.CopyAction)")
    assert "Qt.callLater" in dropped
    assert "visible = false" not in dropped and "dismiss(" not in dropped and "close(" not in dropped
    entered = _block(text, "onEntered: drag => {")
    assert "drag.accept(Qt.CopyAction)" in entered
    # Enter coordinates are only remembered (to skip the synthetic move), never hit-tested.
    assert "pointerMoved(drag.x" not in entered
    assert "anchors.top: true" in text and "anchors.left: true" in text


def test_hotzone_and_hud_windows():
    hz = _read(WHEEL / "HotZone.qml")
    assert 'WlrLayershell.namespace: "dropwheel-hotzone"' in hz
    assert "WlrLayershell.keyboardFocus: WlrKeyboardFocus.None" in hz
    assert "WlrLayer.Overlay" in hz and 'keys: ["text/uri-list"]' in hz
    dropped = _block(hz, "onDropped: drop => {")
    assert dropped.index("drop.urls") < dropped.index("drop.accept(Qt.CopyAction)") and "Qt.callLater" in dropped
    hud = _read(HUD / "HudWindow.qml")
    assert 'WlrLayershell.namespace: "dropwheel-hud"' in hud
    assert "WlrLayershell.keyboardFocus: WlrKeyboardFocus.None" in hud
    assert "WlrLayer.Overlay" in hud
    assert re.search(r"mask: Region \{\s*item: column\s*\}", hud)
    assert "maxVisible: 5" in hud


def test_result_chip_drag_contract():
    text = _read(HUD / "ResultChip.qml")
    assert "Drag.dragType: Drag.Automatic" in text
    assert "Drag.supportedActions: Qt.CopyAction" in text
    assert '"text/uri-list": root.uri + "\\r\\n"' in text
    assert '"text/plain"' in text
    assert "grabToImage" in text and "Drag.imageSource" in text


def test_wheel_geometry_constants():
    text = _read(WHEEL / "Wheel.qml")
    for expr in ("innerRadius: 52 * k", "outerRadius: 132 * k", "hubRadius: 48 * k", "outerRadius + 8 * k",
                 "segmentGap: 4 * k", "growBy: 4 * root.k", "cornerRadius: 10 * root.k"):
        assert expr in text, expr
    assert "from: 0.45" in text and "Anim.FastSpatial" in text
    assert "-12 * root.morphDir" in text and "Anim.SlowEffects" in text


# --------------------------------------------------------------------------------------------- runtime harness
def _ui_base():
    try:
        import test_ui_base  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("tests/test_ui_base.py (Quickshell stand-ins) not available")
    return test_ui_base


def _prepare_tree(tmp_path: Path) -> None:
    shell = tmp_path / "shell"
    if shell.exists():
        return
    shutil.copytree(SHELL / "services", shell / "services")
    shutil.copytree(SHELL / "components", shell / "components")
    (shell / "wheel").mkdir()
    for name in ("Wheel.qml", "Segment.qml", "CenterHub.qml", "WheelText.js"):
        shutil.copy(WHEEL / name, shell / "wheel" / name)
    (shell / "wheel" / "qmldir").write_text("Wheel 1.0 Wheel.qml\nSegment 1.0 Segment.qml\nCenterHub 1.0 CenterHub.qml\n"
                                            "WindowUnderTest 1.0 WindowUnderTest.qml\nStubPanelWindow 1.0 StubPanelWindow.qml\n"
                                            "StubFocusGrab 1.0 StubFocusGrab.qml\nStubRegion 1.0 StubRegion.qml\n")
    _window_under_test(shell / "wheel")
    (shell / "hud").mkdir()
    for name in ("JobCard.qml", "ResultChip.qml"):
        shutil.copy(HUD / name, shell / "hud" / name)
    (shell / "hud" / "qmldir").write_text("JobCard 1.0 JobCard.qml\nResultChip 1.0 ResultChip.qml\n")
    stub = tmp_path / "stubs" / "Quickshell" / "Widgets"
    stub.mkdir(parents=True)
    (stub / "qmldir").write_text("module Quickshell.Widgets\nClippingRectangle 1.0 ClippingRectangle.qml\n")
    (stub / "ClippingRectangle.qml").write_text("import QtQuick\nRectangle { clip: true }\n")


# Stand-ins for the Quickshell window types. They model the compositor rule behind the worst wheel bug so far: when a
# MAPPED surface turns Exclusive, Hyprland ends the active seat grab (CLayerSurface::onCommit → setGrab(nullptr)) and the
# grab reports `cleared`; the interactivity reaches the compositor after the grab, because it only goes out with the
# next surface commit (callLater here).
STUB_PANEL = """
import QtQuick

Item {
    id: panel
    property var stubScreen: null
    property color color: "transparent"
    property var mask: null
    property int marginLeft: 0
    property int marginTop: 0
    property string keyboardFocus: "none"
    property string committedFocus: "none"
    // A surface created Exclusive has that state from its first commit: only a later change counts.
    property bool mapped: false
    signal turnedExclusiveWhileMapped
    width: implicitWidth
    height: implicitHeight
    function commitFocus() {
        const was = panel.committedFocus;
        panel.committedFocus = panel.keyboardFocus;
        if (panel.mapped && was !== "exclusive" && panel.committedFocus === "exclusive")
            panel.turnedExclusiveWhileMapped();
    }
    onKeyboardFocusChanged: Qt.callLater(panel.commitFocus)
    onVisibleChanged: {
        panel.mapped = panel.visible;
        panel.committedFocus = panel.visible ? panel.keyboardFocus : "none";
    }
}
"""

STUB_GRAB = """
import QtQuick

Item {
    id: grab
    property var windows: []
    property bool active: false
    signal cleared
    Connections {
        target: grab.windows.length > 0 ? grab.windows[0] : null
        function onTurnedExclusiveWhileMapped() {
            if (grab.active)
                grab.cleared();
        }
    }
}
"""

STUB_REGION = """
import QtQml

QtObject {
    property var item: null
    property string shape: ""
}
"""


def _window_under_test(folder: Path) -> None:
    """Write the real WheelWindow.qml with its Quickshell types swapped for the stand-ins above."""
    text = _read(WHEEL / "WheelWindow.qml")
    swaps = [("PanelWindow {", "StubPanelWindow {"), ("HyprlandFocusGrab {", "StubFocusGrab {"), ("Region {", "StubRegion {"),
             ("shape: RegionShape.Ellipse", 'shape: "ellipse"'), ("margins.left:", "marginLeft:"), ("margins.top:", "marginTop:"),
             ("    screen: priv.screenObj", "    stubScreen: priv.screenObj"), ("WlrLayershell.keyboardFocus:", "keyboardFocus:"),
             ("WlrKeyboardFocus.None", '"none"'), ("WlrKeyboardFocus.Exclusive", '"exclusive"'), ("WlrKeyboardFocus.OnDemand", '"ondemand"'),
             ("import Quickshell.Wayland\n", ""), ("import Quickshell.Hyprland\n", ""), ("import Quickshell\n", "")]
    for old, new in swaps:
        assert old in text, old
        text = text.replace(old, new)
    for line in ("    anchors.top: true\n", "    anchors.left: true\n", "    exclusionMode: ExclusionMode.Ignore\n",
                 "    WlrLayershell.layer: WlrLayer.Overlay\n", '    WlrLayershell.namespace: "dropwheel-wheel"\n'):
        assert line in text, line
        text = text.replace(line, "")
    (folder / "WindowUnderTest.qml").write_text(text, encoding="utf-8")
    (folder / "StubPanelWindow.qml").write_text(STUB_PANEL, encoding="utf-8")
    (folder / "StubFocusGrab.qml").write_text(STUB_GRAB, encoding="utf-8")
    (folder / "StubRegion.qml").write_text(STUB_REGION, encoding="utf-8")


PRELUDE = """
import QtQuick
import "../services"
import "../components"
import "../wheel"
import "../hud"
import "../wheel/WheelText.js" as WT

Window {
    id: win
    width: 700
    height: 600
    visible: true
    property bool done: false
    property var report: ({ failures: [] })
    function check(cond, what) { if (!cond) report.failures.push(String(what)); }
    function eq(a, b, what) { if (a !== b) report.failures.push(what + ": got " + JSON.stringify(a) + ", expected " + JSON.stringify(b)); }
    function finish() { done = true; }
    function act(id, extra) {
        return Object.assign({ type: "action", id: id, label: id, icon: "bolt", text: null, available: true, hint: "", install: "",
                               danger: false, mode: "instant", dialog: null, params: [], description: "", default: false }, extra || {});
    }
    function group(id, items, def) {
        return { type: "group", id: id, label: id, icon: "folder", text: null, items: items, default: def || null, available: true };
    }
    function point(w, index, count, radius) {
        const a = (-90 + index * 360 / count) * Math.PI / 180;
        return { x: w.centerX + Math.cos(a) * radius, y: w.centerY + Math.sin(a) * radius };
    }
"""


def _run(tmp_path: Path, name: str, body: str, env: dict | None = None, allow: tuple[str, ...] = ()) -> dict:
    ub = _ui_base()
    ub._harness()
    _prepare_tree(tmp_path)
    full_env = {
        "DROPWHEEL_BACKEND": "definitely-not-a-binary-dropwheel",
        "XDG_STATE_HOME": "/nonexistent-state",
        "XDG_CONFIG_HOME": "/nonexistent-config",
    }
    full_env.update(env or {})
    return ub._run_qml(tmp_path, name, PRELUDE + textwrap.dedent(body) + "\n}\n", env=full_env,
                       allow_warnings=("Backend", "Process failed", "definitely-not") + allow)


MENU = """
    readonly property var convert: group("img.convert", [act("img.convert.png", { text: "PNG" }), act("img.convert.jpg", { text: "JPG" }),
                                                        act("img.convert.heic", { available: false, hint: "Requires heif", install: "sudo pacman -S libheif" })],
                                         "img.convert.jpg")
    readonly property var menu: ({
        files: [{ path: "/tmp/a b.png", uri: "file:///tmp/a%20b.png", name: "a b.png", ext: "png", category: "image", size: 1234 }],
        category: "image", count: 1, title: "a b.png", subtitle: "1.2 kB", thumb: null, icon: "image",
        pages: [
            { id: "main", label: "Tools", items: [convert, act("img.compress", { mode: "quick", params: [{ name: "q", type: "int" }] }),
                                                 act("img.crop", { mode: "dialog", dialog: "crop" }), act("img.bg", { available: false, install: "pipx install rembg" }),
                                                 act("img.info")] },
            { id: "file", label: "File", items: [act("file.open"), act("file.trash", { danger: true })] },
            { id: "empty", label: "Empty", items: [] }
        ]
    })
"""


def test_wheel_hit_testing_and_navigation(tmp_path):
    body = MENU + """
    property var activations: []
    property int cancels: 0
    property int clicks: 0
    Wheel {
        id: w
        width: implicitWidth; height: implicitHeight
        menu: win.menu
        mode: "click"
        onActivated: (node, options) => win.activations.push({ id: node.id, options: options, dialog: node.dialog })
        onCancelled: win.cancels++
        onClickModeRequested: win.clicks++
    }
    Timer {
        interval: 50; running: true
        onTriggered: {
            // pages with no items are skipped
            eq(w.pages.length, 2, "non-empty pages");
            eq(w.count, 5, "main ring size");
            // hit testing: segment centres for 1..8 segments, centre, outside
            eq(w.hitTest(w.centerX, w.centerY).zone, "centre", "centre zone");
            eq(w.hitTest(w.centerX, w.centerY - w.innerRadius + 2).zone, "centre", "inside inner radius");
            eq(w.hitTest(w.centerX + w.discRadius + 20, w.centerY).zone, "outside", "outside");
            const r = (w.innerRadius + w.outerRadius) / 2;
            for (let i = 0; i < 5; i++) {
                const p = point(w, i, 5, r);
                const h = w.hitTest(p.x, p.y);
                eq(h.zone + ":" + h.index, "segment:" + i, "segment " + i + " of 5");
            }
            // 12 o'clock is segment 0, a little clockwise still 0, half a step clockwise is segment 1
            const top = w.hitTest(w.centerX + 5, w.centerY - r);
            eq(top.index, 0, "12 o'clock");
            const p1 = point(w, 1, 5, r);
            eq(w.hitTest(p1.x, p1.y).index, 1, "segment 1 clockwise from the top");
            // sizes are scale-aware and leave room for the pill
            check(w.implicitHeight > w.discRadius * 2 + 100, "room for pill and breadcrumb");
            // pill with nothing hovered: file name + size
            eq(w.pillTitle, "a b.png · 1.2 kB", "pill file summary");
            // hover a segment → pill shows its label
            const pc = point(w, 4, 5, r);
            w.pointerMoved(pc.x, pc.y);
            eq(w.highlight, 4, "hover highlight");
            eq(w.pillTitle, "img.info", "pill label");
            // hover the unavailable one → hint + install command in the pill
            const pb = point(w, 3, 5, r);
            w.pointerMoved(pb.x, pb.y);
            eq(w.pillCode, "pipx install rembg", "install command in pill");
            check(w.pillDetail !== "", "hint in pill");
            // centre at the top level = cancel
            w.pointerMoved(w.centerX, w.centerY);
            eq(w.centreHot, true, "centre hot");
            eq(w.pillTitle, WT.tr(I18n, "wheel.cancel"), "cancel label");
            w.pointerLeft();
            eq(w.highlight, -1, "left clears highlight");
            // click on a group → sub-ring with the default highlighted; Back via the centre
            const pg = point(w, 0, 5, r);
            w.clickAt(pg.x, pg.y, Qt.LeftButton);
            eq(w.stack.length, 1, "entered group");
            eq(w.count, 3, "sub-ring size");
            eq(w.items[w.highlight].id, "img.convert.jpg", "sub-ring default highlighted");
            eq(w.breadcrumb, "img.convert", "breadcrumb");
            // click an unavailable action → no activation
            const ph = point(w, 2, 3, r);
            w.clickAt(ph.x, ph.y, Qt.LeftButton);
            eq(win.activations.length, 0, "unavailable does not run");
            eq(w.flashIndex, 2, "unavailable flashes");
            // click the centre = back, the group we came from is highlighted
            w.clickAt(w.centerX, w.centerY, Qt.LeftButton);
            eq(w.stack.length, 0, "back");
            eq(w.items[w.highlight].id, "img.convert", "back highlights the group");
            // keyboard: arrows move, Enter runs, numbers run, Alt+Enter / right-click open options
            w.handleKeyPress({ key: Qt.Key_Right, modifiers: 0, isAutoRepeat: false });
            eq(w.highlight, 1, "right arrow");
            w.handleKeyPress({ key: Qt.Key_Return, modifiers: Qt.AltModifier, isAutoRepeat: false });
            const last = win.activations[win.activations.length - 1];
            eq(JSON.stringify(last), JSON.stringify({ id: "img.compress", options: true, dialog: "form" }), "Alt+Enter → form");
            w.handleKeyPress({ key: Qt.Key_3, modifiers: 0, isAutoRepeat: false });
            eq(win.activations[win.activations.length - 1].id, "img.crop", "number key 3");
            eq(win.activations[win.activations.length - 1].options, false, "dialog action is not 'options'");
            const pi = point(w, 4, 5, r);
            w.clickAt(pi.x, pi.y, Qt.RightButton);
            eq(win.activations.length, 2, "right-click on an instant action without params runs nothing");
            eq(w.transientText, I18n.t("ui.no_options"), "no options feedback");
            w.clickAt(pi.x, pi.y, Qt.LeftButton);
            eq(win.activations[win.activations.length - 1].id, "img.info", "left click runs");
            // Shift tap switches the page (Shift+Tab does not)
            w.handleKeyPress({ key: Qt.Key_Shift, modifiers: Qt.ShiftModifier, isAutoRepeat: false });
            w.handleKeyPress({ key: Qt.Key_Backtab, modifiers: Qt.ShiftModifier, isAutoRepeat: false });
            w.handleKeyRelease({ key: Qt.Key_Shift, modifiers: 0, isAutoRepeat: false });
            eq(w.pageIndex, 0, "Shift+Tab does not switch page");
            w.handleKeyPress({ key: Qt.Key_Shift, modifiers: Qt.ShiftModifier, isAutoRepeat: false });
            w.handleKeyRelease({ key: Qt.Key_Shift, modifiers: 0, isAutoRepeat: false });
            eq(w.pageIndex, 1, "Shift tap switches page");
            eq(w.count, 2, "file page ring");
            w.handleKeyPress({ key: Qt.Key_Tab, modifiers: Qt.ControlModifier, isAutoRepeat: false });
            eq(w.pageIndex, 0, "Ctrl+Tab switches page back");
            // Esc at the top level cancels; Backspace at the top level does nothing
            w.handleKeyPress({ key: Qt.Key_Backspace, modifiers: 0, isAutoRepeat: false });
            eq(win.cancels, 0, "backspace at top level");
            w.handleKeyPress({ key: Qt.Key_Escape, modifiers: 0, isAutoRepeat: false });
            eq(win.cancels, 1, "Esc cancels");
            // a new menu resets the navigation
            w.switchPage(1);
            w.menu = JSON.parse(JSON.stringify(win.menu));
            eq(w.pageIndex, 0, "reset page");
            eq(w.stack.length, 0, "reset stack");
            finish();
        }
    }
    """
    _run(tmp_path, "WheelNav", body)


def test_wheel_drop_decisions_and_spring(tmp_path):
    body = MENU + """
    property var activations: []
    property int cancels: 0
    property int clicks: 0
    property int phase: 0
    Wheel {
        id: w
        width: implicitWidth; height: implicitHeight
        menu: win.menu
        mode: "drag"
        springMs: 150
        onActivated: (node, options) => win.activations.push(node.id)
        onCancelled: win.cancels++
        onClickModeRequested: { win.clicks++; w.mode = "click"; }
    }
    Wheel {
        id: loadingWheel
        width: implicitWidth; height: implicitHeight
        menu: null
        loading: true
        onClickModeRequested: win.clicks += 100
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            const r = (w.innerRadius + w.outerRadius) / 2;
            if (win.phase === 0) {
                // drop on an action → activated
                const p = point(w, 4, 5, r);
                w.dropAt(p.x, p.y);
                eq(JSON.stringify(win.activations), JSON.stringify(["img.info"]), "drop runs the action");
                // drop on the centre → cancel
                w.dropAt(w.centerX, w.centerY);
                eq(win.cancels, 1, "drop on centre cancels");
                // drop on an unavailable action → flash + click mode
                const pb = point(w, 3, 5, r);
                w.dropAt(pb.x, pb.y);
                eq(win.clicks, 1, "unavailable keeps the wheel in click mode");
                eq(w.flashIndex, 3, "unavailable flashes");
                eq(win.activations.length, 1, "unavailable does not run");
                // still loading → click mode
                loadingWheel.dropAt(loadingWheel.centerX, loadingWheel.centerY - 90);
                eq(win.clicks, 101, "drop while loading keeps the wheel");
                // spring loading in drag mode: hover a group
                w.mode = "drag";
                const pg = point(w, 0, 5, r);
                w.pointerMoved(pg.x, pg.y);
                check(w.springProgress >= 0, "spring started");
                win.phase = 1;
            } else if (win.phase === 1 && w.stack.length === 1) {
                eq(w.items.length, 3, "spring opened the sub-ring");
                // the unmoved pointer must not spring again; moving into the centre springs Back
                w.pointerMoved(w.centerX + 1, w.centerY + 1);
                eq(w.pillTitle, I18n.t("ui.back"), "back label");
                win.phase = 2;
            } else if (win.phase === 2 && w.stack.length === 0) {
                // drop on a group → enter it and switch to click mode
                const pg = point(w, 0, 5, r);
                w.mode = "drag";
                w.dropAt(pg.x, pg.y);
                eq(w.stack.length, 1, "drop on group enters it");
                eq(win.clicks, 102, "drop on group requests click mode");
                eq(w.items[w.highlight].id, "img.convert.jpg", "default highlighted after drop on group");
                // click mode: no spring loading
                w.pointerMoved(w.centerX, w.centerY);
                eq(w.springProgress, 0, "no spring in click mode");
                // close animation emits closeFinished
                w.close(true, 300, 200);
                check(w.closing, "closing");
                win.phase = 3;
            } else if (win.phase === 3 && !w.closing) {
                finish();
            }
        }
    }
    Connections {
        target: w
        function onCloseFinished() { w.open(); }
    }
    """
    _run(tmp_path, "WheelDrop", body)


def test_wheel_open_resets_session_and_arms_cancel(tmp_path):
    body = MENU + """
    property int cancels: 0
    Wheel {
        id: w
        width: implicitWidth; height: implicitHeight
        menu: win.menu
        mode: "click"
        onCancelled: win.cancels++
    }
    Timer {
        interval: 50; running: true
        onTriggered: {
            const r = (w.innerRadius + w.outerRadius) / 2;
            w.open();
            // the wheel opens centred on the pointer: the hub keeps showing the file, Enter selects segment 1
            w.pointerMoved(w.centerX, w.centerY);
            eq(w.centreHot, true, "centre hot");
            eq(w.centreShown, false, "cancel not armed yet");
            eq(w.pillTitle, "a b.png · 1.2 kB", "file summary while the hub is not armed");
            w.handleKeyPress({ key: Qt.Key_Return, modifiers: 0, isAutoRepeat: false });
            eq(win.cancels, 0, "Enter does not cancel through an unarmed hub");
            eq(w.highlight, 0, "Enter selects the first segment");
            // leaving the hub and coming back arms Cancel
            const p = point(w, 2, 5, r);
            w.pointerMoved(p.x, p.y);
            w.pointerMoved(w.centerX, w.centerY);
            eq(w.centreShown, true, "armed after leaving the hub");
            eq(w.pillTitle, WT.tr(I18n, "wheel.cancel"), "cancel label once armed");
            // a new session with the same (cached) menu object starts from the top level of page 1
            const g = point(w, 0, 5, r);
            w.clickAt(g.x, g.y, Qt.LeftButton);
            eq(w.stack.length, 1, "entered the group");
            w.open();
            eq(w.stack.length, 0, "open() leaves the sub-ring");
            eq(w.highlight, -1, "open() clears the highlight");
            eq(w.centreShown, false, "open() disarms the hub");
            eq(w.morphT, 1, "no morph on open");
            w.switchPage(1);
            eq(w.pageIndex, 1, "file page");
            w.open();
            eq(w.pageIndex, 0, "open() returns to the first page");
            eq(w.count, 5, "main ring shown");
            finish();
        }
    }
    """
    _run(tmp_path, "WheelSession", body)


def test_wheel_window_keeps_its_grab_when_a_drag_wheel_turns_click(tmp_path):
    body = MENU + """
    property int cancels: 0
    property int closes: 0
    property int phase: 0
    readonly property var screen: ({ x: 0, y: 0, width: 1920, height: 1080 })
    WindowUnderTest {
        id: w
        menu: win.menu
        onCancelled: win.cancels++
        onClosed: win.closes++
    }
    StubFocusGrab { id: unused }
    readonly property var grab: {
        for (const c of w.data) {
            if (c && c.hasOwnProperty("cleared"))
                return c;
        }
        return null;
    }
    Timer {
        interval: 60; running: true; repeat: true
        onTriggered: {
            if (win.phase === 0) {
                // A drag-mode wheel never has the keyboard.
                w.present(win.screen, 900, 500, true);
                eq(w.keyboardFocus, "none", "drag mode has no keyboard focus");
                eq(win.grab.active, false, "no grab in drag mode");
                win.phase = 1;
            } else if (win.phase === 1) {
                // Drop on a group / unavailable action / while loading, hot-zone drop: the mapped surface turns click.
                w.mode = "click";
                eq(win.grab.active, true, "click mode has the grab");
                eq(w.keyboardFocus, "ondemand", "a mapped surface must not turn Exclusive");
                win.phase = 2;
            } else if (win.phase === 2) {
                eq(win.cancels, 0, "the wheel survives the switch to click mode");
                check(w.visible && !w.closing, "the wheel stays open");
                // Another open on the same surface (IPC) does not turn it Exclusive either.
                w.present(win.screen, 900, 500, true);
                eq(w.keyboardFocus, "ondemand", "a later present() on the mapped surface");
                w.dismiss(false, 0, 0);
                eq(w.keyboardFocus, "none", "closing gives the keyboard back");
                win.phase = 3;
            } else if (win.phase === 3 && !w.visible) {
                eq(win.closes, 1, "closed once");
                // A surface created in click mode is Exclusive from the start, and that does not end its grab.
                w.present(win.screen, 900, 500, true);
                eq(w.keyboardFocus, "exclusive", "created in click mode");
                eq(win.grab.active, true, "grab of a click-mode surface");
                win.phase = 4;
            } else if (win.phase === 4) {
                eq(win.cancels, 0, "a surface mapped Exclusive keeps its grab");
                w.present(win.screen, 900, 500, true);
                eq(w.keyboardFocus, "exclusive", "stays Exclusive on another present()");
                // Reopened during the close animation: the surface is still mapped, it must not go back to Exclusive.
                w.dismiss(false, 0, 0);
                eq(w.keyboardFocus, "none", "closing");
                w.present(win.screen, 900, 500, true);
                eq(w.closing, false, "reopened");
                eq(w.keyboardFocus, "ondemand", "reopened while still mapped");
                win.phase = 5;
            } else if (win.phase === 5) {
                eq(win.cancels, 0, "reopening during the close animation keeps the grab");
                // A click outside still closes the wheel.
                win.grab.cleared();
                eq(win.cancels, 1, "a real clear cancels");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "WheelWindowGrab", body)


def test_empty_target_never_locks_the_keyboard(tmp_path):
    body = MENU + """
    property int cancels: 0
    property int phase: 0
    readonly property var screen: ({ x: 0, y: 0, width: 1920, height: 1080 })
    WindowUnderTest {
        id: w
        mode: "click"
        emptyTarget: true
        onCancelled: win.cancels++
    }
    readonly property var grab: {
        for (const c of w.data) {
            if (c && c.hasOwnProperty("cleared"))
                return c;
        }
        return null;
    }
    Timer {
        interval: 60; running: true; repeat: true
        onTriggered: {
            if (win.phase === 0) {
                w.present(win.screen, 900, 500, true);
                // OnDemand and no grab: a click on another window moves the keyboard there and leaves the target open.
                eq(w.keyboardFocus, "ondemand", "the empty target is not Exclusive");
                eq(win.grab.active, false, "the empty target has no grab");
                check(w.wheel.pillDetail !== "", "the pill says how to close it");
                win.phase = 1;
            } else if (win.phase === 1) {
                // A click on the ring does nothing, one on the hub closes it (there is no outside-click close).
                const r = (w.wheel.innerRadius + w.wheel.outerRadius) / 2;
                w.wheel.clickAt(w.wheel.centerX, w.wheel.centerY - r, Qt.LeftButton);
                eq(win.cancels, 0, "click on the ring");
                w.wheel.pointerMoved(w.wheel.centerX, w.wheel.centerY - r);
                w.wheel.pointerMoved(w.wheel.centerX, w.wheel.centerY);
                eq(w.wheel.centreShown, true, "the hub is armed");
                eq(w.wheel.pillTitle, WT.tr(I18n, "wheel.cancel"), "the hovered hub says cancel");
                w.wheel.clickAt(w.wheel.centerX, w.wheel.centerY, Qt.LeftButton);
                eq(win.cancels, 1, "click on the hub");
                // Files arrive: the grab starts on the OnDemand surface and must survive.
                w.menu = win.menu;
                w.emptyTarget = false;
                eq(win.grab.active, true, "grab once the files are in");
                eq(w.keyboardFocus, "ondemand", "still not Exclusive");
                win.phase = 2;
            } else if (win.phase === 2) {
                eq(win.cancels, 1, "the drop on the empty target does not close it");
                check(w.visible, "still open");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "WheelEmptyTarget", body)


def test_disc_is_centred_on_the_cursor_and_clamped_on_the_disc(tmp_path):
    body = MENU + """
    property int phase: 0
    WindowUnderTest {
        id: w
        menu: win.menu
    }
    function placeAt(screen, x, y) {
        w.present(screen, x, y, true);
        const dw = w.wheel;
        return { x: screen.x + w.marginLeft + dw.centerX, y: screen.y + w.marginTop + dw.centerY };
    }
    Timer {
        interval: 60; running: true; repeat: true
        onTriggered: {
            if (win.phase === 0) {
                const dw = w.wheel;
                // 1920x1080 at monitor scale 1.5: logical 1280x720, the case the old clamp got wrong for 2/3 of the height.
                const s = { x: 0, y: 0, width: 1280, height: 720 };
                const reach = dw.discRadius + dw.edgeMargin;
                for (const y of [reach, 150, 200, 360, 500, 720 - reach]) {
                    const c = placeAt(s, 640, y);
                    eq(c.x, 640, "disc centred on x");
                    eq(c.y, y, "disc centred on the cursor at y=" + y);
                }
                check(w.marginTop < 0 || w.height <= 720, "the surface may hang off the screen");
                // Never past the disc: at the edges only the disc is clamped, and the cursor is still over it.
                let c = placeAt(s, 640, 0);
                eq(c.y, reach, "clamped at the top");
                c = placeAt(s, 640, 719);
                eq(c.y, 720 - reach, "clamped at the bottom");
                c = placeAt(s, 0, 300);
                eq(c.x, reach, "clamped on the left");
                c = placeAt(s, 1279, 300);
                eq(c.x, 1280 - reach, "clamped on the right");
                // Second monitor: margins are relative to its origin.
                const s2 = { x: 2560, y: 200, width: 1920, height: 1080 };
                c = placeAt(s2, 3000, 800);
                eq(c.x, 3000, "other screen x");
                eq(c.y, 800, "other screen y");
                const local = w.cursorLocal();
                eq(local.x, dw.centerX, "cursorLocal x at the disc centre");
                eq(local.y, dw.centerY, "cursorLocal y at the disc centre");
                // A screen too small for the disc caps the scale instead of pushing the disc off the screen.
                const tiny = { x: 0, y: 0, width: 200, height: 200 };
                c = placeAt(tiny, 10, 10);
                check(dw.k < Theme.scale, "scale capped");
                check(dw.fit < 1, "fit factor below 1");
                check(2 * (dw.discRadius + dw.edgeMargin) <= 200.5, "disc fits: " + dw.discRadius);
                eq(c.x, 100, "centred when it just fits");
                win.phase = 1;
            } else if (win.phase === 1) {
                const s = { x: 0, y: 0, width: 1280, height: 720 };
                placeAt(s, 640, 360);
                check(w.wheel.k === Theme.scale, "scale back to normal on a normal screen");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "WheelPlacement", body)


def test_pill_and_breadcrumb_stay_on_screen(tmp_path):
    body = MENU + """
    readonly property var longNode: act("img.ocr", { label: "Copia testo", available: false,
        hint: "Richiede tesseract, che legge il testo delle immagini e che su questo sistema non risulta installato: serve per questa azione e anche per le altre di questo gruppo, senza eccezioni",
        install: "sudo pacman -S --needed tesseract tesseract-data-ita tesseract-data-eng tesseract-data-fra tesseract-data-deu tesseract-data-spa" })
    readonly property var longMenu: ({
        files: [{ path: "/tmp/a.png", name: "a.png", ext: "png", category: "image", size: 1 }], category: "image", count: 1, title: "a.png", subtitle: "1 B",
        pages: [{ id: "main", label: "Tools", items: [group("very.long", [win.longNode, act("x")]), win.longNode] }, { id: "file", label: "File", items: [act("y")] }]
    })
    Wheel {
        id: w
        width: implicitWidth; height: implicitHeight
        mode: "click"
        menu: win.longMenu
    }
    function inside(item, what) {
        check(item.y >= w.areaTop - 0.5, what + " top " + item.y + " < " + w.areaTop);
        check(item.y + item.height <= w.areaBottom + 0.5, what + " bottom " + (item.y + item.height) + " > " + w.areaBottom);
        check(item.x >= w.areaLeft - 0.5, what + " left " + item.x);
        check(item.x + item.width <= w.areaRight + 0.5, what + " right " + (item.x + item.width));
    }
    property int phase: 0
    // Long enough for the pill's width animation to settle (it overshoots while running).
    Timer {
        interval: 500; running: true; repeat: true
        onTriggered: {
            const pill = w.pillItem;
            const crumb = w.crumbItem;
            const discTop = w.centerY - w.discRadius;
            const discBottom = w.centerY + w.discRadius;
            if (win.phase === 0) {
                // The whole item is on screen: pill below, inside the surface even at its tallest.
                w.selectIndex(1);
                eq(w.pillCode, win.longNode.install, "command in the pill");
                win.phase = 1;
            } else if (win.phase === 1) {
                eq(w.labelsBelow, true, "labels below");
                inside(pill, "tallest pill");
                check(pill.y >= discBottom, "pill below the disc");
                check(pill.height <= w.pillMaxHeight + 0.5, "the reserved room is the real maximum: " + pill.height + " > " + w.pillMaxHeight);
                check(w.implicitHeight >= 2 * w.discRadius + 2 * pill.height, "symmetric room for it");
                // Error pill, in click mode, with details.
                w.errorText = "Impossibile caricare le azioni perché il servizio non risponde più da parecchio tempo";
                w.errorDetails = "Una riga di dettaglio decisamente lunga che occupa più di una riga nella pillola";
                win.phase = 2;
            } else if (win.phase === 2) {
                inside(pill, "error pill");
                w.errorText = "";
                w.errorDetails = "";
                // Bottom edge of the screen: only the disc fits, the labels flip above it.
                w.visibleArea = Qt.rect(0, -50, w.width, discBottom + 50 + 4);
                win.phase = 3;
            } else if (win.phase === 3) {
                eq(w.labelsBelow, false, "labels flip above");
                inside(pill, "flipped pill");
                check(pill.y + pill.height <= discTop, "flipped pill above the disc");
                check(w.dotsItem.y + w.dotsItem.height <= discTop, "flipped dots above the disc");
                // A sub-ring there: the breadcrumb takes the free side, or goes past the pill when that side is off screen.
                w.enterGroup(w.items[0], false);
                w.selectIndex(0);
                win.phase = 4;
            } else if (win.phase === 4) {
                check(crumb.opacity > 0, "breadcrumb shown");
                inside(crumb, "breadcrumb with the pill above");
                check(crumb.y + crumb.height <= pill.y, "breadcrumb past the flipped pill");
                // Top edge: labels stay below, the breadcrumb has no room above and goes past the pill.
                w.visibleArea = Qt.rect(0, discTop - 4, w.width, w.height);
                win.phase = 5;
            } else if (win.phase === 5) {
                eq(w.labelsBelow, true, "labels below at the top edge");
                inside(pill, "pill at the top edge");
                inside(crumb, "breadcrumb at the top edge");
                check(crumb.y >= pill.y + pill.height, "breadcrumb past the pill");
                // Left edge: the disc is flush with the screen, the labels slide back inside.
                w.visibleArea = Qt.rect(w.centerX - w.discRadius - 4, 0, 1280, w.height);
                win.phase = 6;
            } else if (win.phase === 6) {
                inside(pill, "pill at the left edge");
                inside(crumb, "breadcrumb at the left edge");
                check(pill.x + pill.width / 2 > w.centerX, "the pill slid towards the screen");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "WheelLabels", body)


SEGMENT_BODY = """
    function node(label, extra) {
        return Object.assign({ type: "action", id: "x", label: label, icon: "bolt", text: null, available: true }, extra || {});
    }
    component Seg: Segment {
        width: 400; height: 400
        index: 0
        count: 8
    }
"""


def test_segment_labels_wrap_before_they_shrink(tmp_path):
    ub = _ui_base()
    ub._harness()
    from PySide6.QtGui import QFontDatabase

    # The offscreen platform may list no font at all, and then text has no real metrics: load one if there is any.
    font_id = -1
    for path in ("C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/noto/NotoSans-Regular.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id != -1:
                break
    if font_id == -1:
        pytest.skip("no font file to measure the labels with")
    body = SEGMENT_BODY + """
    Item {
        width: 400; height: 400
        Seg { id: shortLabel; node: win.node("Info") }
        Seg { id: twoWords; node: win.node("Copia percorso") }
        Seg { id: oneLongWord; node: win.node("Dimensioni") }
        Seg { id: endless; node: win.node("Rimuovi tutti i metadati personali e la posizione dal file prima di condividerlo") }
    }
    Timer {
        interval: 200; running: true
        onTriggered: {
            const nominal = Theme.font.size.small * (9.5 / 11);
            const lowest = Theme.font.size.small * (7.5 / 11);
            eq(shortLabel.labelItem.lineCount, 1, "a short label stays on one line");
            check(shortLabel.labelItem.fontInfo.pointSize >= nominal - 1, "a short label keeps the nominal size");
            check(!shortLabel.labelItem.truncated, "short label not elided");
            // The reported bug: "COPIA PERC…" on an 8-segment ring.
            eq(twoWords.labelItem.lineCount, 2, "two words wrap");
            check(!twoWords.labelItem.truncated, "two words not elided");
            check(twoWords.labelItem.contentHeight <= twoWords.labelItem.height, "two lines fit the box");
            check(twoWords.labelItem.fontInfo.pointSize >= lowest - 1, "two words keep a readable size");
            // Wrapping does not split a word: it shrinks instead.
            eq(oneLongWord.labelItem.lineCount, 1, "one word stays on one line");
            check(oneLongWord.labelItem.contentWidth <= oneLongWord.labelItem.width + 0.5, "the word fits the box");
            check(oneLongWord.labelItem.fontInfo.pointSize >= lowest - 1, "the word keeps a readable size");
            // Far too long: elided, and never past two lines or below the minimum size (quantised by the font engine).
            check(endless.labelItem.truncated, "elided as a last resort");
            check(endless.labelItem.contentHeight <= endless.labelItem.height, "still inside the box");
            eq(endless.labelItem.lineCount, 2, "still two lines");
            check(endless.labelItem.fontInfo.pointSize >= lowest - 1, "not below the minimum: " + endless.labelItem.fontInfo.pointSize);
            finish();
        }
    }
    """
    try:
        _run(tmp_path, "SegmentLabels", body)
    finally:
        QFontDatabase.removeApplicationFont(font_id)


def test_segment_install_badge(tmp_path):
    body = SEGMENT_BODY + """
    Item {
        width: 400; height: 400
        Seg { id: bigMissing; node: win.node("TXT", { text: "TXT", available: false, install: "x" }) }
        Seg { id: iconMissing; node: win.node("OCR", { available: false, install: "x" }) }
        Seg { id: bigOk; node: win.node("PNG", { text: "PNG" }) }
        Seg { id: groupMissing; node: win.node("Altro", { type: "group", items: [], available: false }) }
    }
    Timer {
        interval: 200; running: true
        onTriggered: {
            // There on big text as well as on icons, and not dimmed with the content.
            eq(bigMissing.badge.visible, true, "badge on an unavailable format target");
            eq(iconMissing.badge.visible, true, "badge on an unavailable icon action");
            eq(bigOk.badge.visible, false, "no badge when available");
            eq(groupMissing.badge.visible, false, "no badge on groups");
            check(bigMissing.bigTextItem.parent.opacity < 0.5, "content dimmed");
            eq(bigMissing.badge.opacity, 1, "badge opacity");
            eq(bigMissing.badge.parent.opacity, 1, "badge outside the dimmed content");
            // At the top right of the text.
            const big = bigMissing.bigTextItem;
            const box = bigMissing.badge.parent;
            const textRight = (box.width + Math.min(big.contentWidth, box.width)) / 2;
            check(bigMissing.badge.x > box.width / 2, "badge on the right");
            check(bigMissing.badge.x <= textRight && bigMissing.badge.x + bigMissing.badge.width >= textRight, "badge at the text edge");
            check(bigMissing.badge.y < (big.height - big.contentHeight) / 2 + big.contentHeight / 2, "badge at the top");
            finish();
        }
    }
    """
    _run(tmp_path, "SegmentBadge", body)


def test_shortcut_release_is_paired_with_its_press(tmp_path):
    fn = _block(_read(SHELL / "shell.qml"), "function shortcutShouldFire(")
    body = """
    QtObject {
        id: root
        property var shortcutState: ({ shift: { lastAt: 0, pressAt: 0 } })
        %s
    }
    Timer {
        interval: 10; running: true
        onTriggered: {
            const st = root.shortcutState.shift;
            eq(root.shortcutShouldFire("shift", true), true, "press acts");
            eq(root.shortcutShouldFire("shift", false), false, "its release does not");
            st.lastAt = 0;
            // Shift held for a long time while hovering sub-rings: the release must not switch the page.
            eq(root.shortcutShouldFire("shift", true), true, "second press");
            st.pressAt = Date.now() - 60000;
            st.lastAt = 0;
            eq(root.shortcutShouldFire("shift", false), false, "release after a long hold");
            eq(st.pressAt, 0, "the pair is consumed");
            // A bind deferred to the key release: only the release arrives, and it acts.
            st.lastAt = 0;
            eq(root.shortcutShouldFire("shift", false), true, "release without a press");
            // Debounce.
            eq(root.shortcutShouldFire("shift", true), false, "press within 250 ms");
            finish();
        }
    }
    """ % textwrap.indent(fn, "        ").lstrip()
    _run(tmp_path, "ShortcutPairs", body)


def test_open_wheel_supersedes_pending_open_requests():
    text = _read(SHELL / "shell.qml")
    # A cursor / clipboard reply that arrives after another trigger opened the wheel must be dropped.
    assert "root.openTicket++" in _block(text, "function openWheel(")
    for name in ("function openClipboardAtCursor(", "function openFromPayload("):
        block = _block(text, name)
        assert "++root.openTicket" in block and "ticket !== root.openTicket" in block, name


def test_install_hint_points_at_the_segment():
    # The pill is outside the input mask: a click on it goes to the window below, so the hint must not suggest it.
    assert "azione" in _locale("it", "ui_wheel.json")["wheel.click_to_copy"]
    assert "action" in _locale("en", "ui_wheel.json")["wheel.click_to_copy"]


def test_job_cards_render_every_state(tmp_path):
    body = """
    function job(fields) {
        return Object.assign({ id: "j1", localId: "", local: false, state: "running", action: "a", label: "Compress",
            files: ["a.jpg"], progress: null, message: "", warnings: [], result: null, error: null, createdAt: 0, updatedAt: 0,
            finishedAt: 0, cancelling: false, undoing: false, undone: false, undoMessage: "", undoError: null }, fields);
    }
    Column {
        JobCard { id: c1; jobId: "x1"; job: win.job({ state: "queued" }) }
        JobCard { id: c2; jobId: "x2"; job: win.job({ progress: 0.5, files: ["a.jpg", "b.jpg", "c.jpg"] }) }
        JobCard { id: c3; jobId: "x3"; timeoutMs: 0; job: win.job({ state: "done", warnings: ["w1", "w2"], result: { kind: "files", undoable: true,
                  size_before: 1000000, size_after: 220000, outputs: [{ path: "/t/a b.jpg", name: "a b.jpg", size: 220000 }, { path: "/t/2.jpg" }, {}, { path: "/t/4.jpg" }] } }) }
        JobCard { id: c4; jobId: "x4"; job: win.job({ state: "failed", error: { message: "Boom", details: "trace", install: "sudo pacman -S x" } }) }
        JobCard { id: c5; jobId: "x5"; job: win.job({ state: "done", result: { kind: "text", text: "hello" } }) }
        JobCard { id: c6; jobId: "x6"; job: win.job({ state: "done", result: { kind: "info", info: [{ group: "g", key: "k", value: "v" }] } }) }
        JobCard { id: c7; jobId: "x7"; job: win.job({ state: "done", result: { kind: "share", url: "http://x/y", share_id: "s1" } }) }
        JobCard { id: c8; jobId: "x8"; job: win.job({ state: "done", result: null }) }
        JobCard { id: c9; jobId: "x9"; job: win.job({ state: "cancelled" }) }
        JobCard { id: c10; jobId: "x10"; job: ({}) }
    }
    property var expanded: null
    Connections { target: c5; function onExpandRequested(job) { win.expanded = job; } }
    Timer {
        interval: 400; running: true
        onTriggered: {
            eq(c1.running, true, "queued is running");
            eq(c1.dismissAfter, 0, "running never auto-dismisses");
            eq(c2.subtitle, WT.trn(I18n, "hud.files_more", 2, { name: "a.jpg" }), "files summary");
            eq(c2.subtitle, "a.jpg and 2 others", "WheelText fallback without the daemon");
            eq(c3.outputPaths.length, 3, "outputs without path are skipped");
            check(c3.sizeDelta.indexOf("−78%") !== -1, "size delta: " + c3.sizeDelta);
            eq(c3.undoable, true, "undoable");
            eq(c3.dismissAfter, 0, "timeoutMs 0 = never");
            eq(c4.dismissAfter, 0, "failed stays");
            eq(c4.failedState, true, "failed");
            eq(c5.kind, "text", "text kind");
            check(c5.dismissAfter > 0, "text result auto-dismisses");
            eq(c7.dismissAfter, 0, "an active share stays");
            eq(c8.kind, "files", "missing result");
            eq(c9.dismissAfter, 3000, "cancelled short timeout");
            eq(c10.jobState, "queued", "empty job object");
            check(c3.implicitHeight > c1.implicitHeight, "result card is taller");
            c5.expandRequested(c5.job);
            check(win.expanded !== null, "expand signal");
            finish();
        }
    }
    """
    _run(tmp_path, "JobCards", body)


def test_job_card_from_jobs_model_and_dismiss(tmp_path):
    ub = _ui_base()
    env = ub._backend_env(tmp_path) if hasattr(ub, "_backend_env") else None
    if env is None:
        pytest.skip("fake daemon helper not available")
    body = """
    property int phase: 0
    property string started: ""
    Column {
        // away from the window origin: the offscreen platform reports the pointer at (0, 0) = "hovered"
        x: 300
        y: 300
        Repeater {
            id: rep
            model: Jobs.model
            delegate: JobCard {
                required property int index
                timeoutMs: 300
            }
        }
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            if (win.phase === 0 && Backend.helloDone) {
                win.started = Jobs.run("image.compress", ["/tmp/a b.png"], {}, "Compress");
                win.phase = 1;
            } else if (win.phase === 1 && rep.count === 1 && rep.itemAt(0).succeeded) {
                const card = rep.itemAt(0);
                eq(card.outputs.length, 1, "outputs through the ListModel role");
                eq(card.outputs[0].name, "a b.png", "output name");
                eq(card.undoable, true, "undoable through the model");
                win.phase = 2;
            } else if (win.phase === 2 && Jobs.count === 0) {
                eq(rep.count, 0, "auto-dismiss removed the card");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "JobsModel", body, env=env)


def test_job_card_holds_its_row_until_it_leaves(tmp_path):
    """Jobs.prune must not take away a card that is meant to stay: Stop sharing, or hud.timeout_ms = 0."""
    body = """
    property int phase: 0
    property double phaseStarted: 0
    Column {
        // away from the window origin: the offscreen platform reports the pointer at (0, 0) = "hovered"
        x: 300
        y: 300
        Repeater {
            id: rep
            model: Jobs.model
            delegate: JobCard {
                required property int index
                timeoutMs: 200
            }
        }
    }
    function done_job(id, result) {
        Jobs.handleEvent({ job: id, state: "done", action: "a", label: "L", files: ["x"], result: result });
    }
    function next() {
        phase++;
        phaseStarted = Date.now();
    }
    Component.onCompleted: {
        Jobs.pruneAfterMs = 0;
        done_job("share", { kind: "share", url: "http://x", share_id: "s1", expires: Date.now() / 1000 + 1.2 });
        Jobs.handleEvent({ job: "gone", state: "done", action: "a", label: "L", files: ["x"], result: { kind: "share", expires: Date.now() / 1000 - 60 } });
        win.next();
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            if (win.phase === 1 && rep.count === 2) {
                // a share over before its card was created: the card sees it within a second
                Jobs.prune();
                eq(Jobs.count, 2, "held rows stay, whatever prune thinks of them");
                eq(rep.itemAt(0).dismissAfter, 0, "live share: the card stays");
                win.next();
            } else if (win.phase === 2 && Date.now() - win.phaseStarted > 300) {
                Jobs.prune();
                eq(Jobs.count >= 1, true, "the live share is still there after a prune");
                let live = null;
                for (let i = 0; i < rep.count; i++) {
                    if (rep.itemAt(i).jobId === "share")
                        live = rep.itemAt(i);
                }
                check(live !== null, "the card of the live share exists");
                eq(live.dismissAfter, 0, "still running");
                win.next();
            } else if (win.phase === 3 && Jobs.count === 0) {
                // the share expired: the Stop button went, the normal countdown removed the card and its row
                eq(rep.count, 0, "expired shares leave through the normal countdown");
                finish();
            }
        }
    }
    """
    _run(tmp_path, "JobCardHold", body)
