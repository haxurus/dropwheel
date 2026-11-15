import QtQuick
import QtQuick.Window
import DropwheelHost
import Quickshell.Wayland

// PanelWindow on Windows: a frameless, always-on-top tool window placed like a layer surface.
//   * anchors + margins + implicit size → geometry on `screen` (the work area without the taskbar, or the whole
//     screen with ExclusionMode.Ignore); two opposite anchors and no size stretch, as in wlr-layer-shell;
//   * `mask` (a Region) is emulated: while the window is shown, the cursor is checked every 30 ms and the window
//     lets input through (WS_EX_TRANSPARENT) whenever it is outside the region, so clicks and drops land on the
//     windows below, as they do outside a layer surface's input region;
//   * WlrLayershell.keyboardFocus None → the window never activates; Exclusive / OnDemand → it is activated when
//     shown;
//   * the hot zone (namespace "dropwheel-hotzone") only takes drops: it lets clicks through unless the mouse button
//     went down outside it, so a click in the screen corner still reaches the close button of a maximised window.
QtObject {
    id: root

    default property alias data: win.data
    property var screen: null
    property bool visible: true
    property color color: "white"
    property real implicitWidth: 0
    property real implicitHeight: 0
    readonly property real width: win.width
    readonly property real height: win.height
    readonly property PanelAnchors anchors: PanelAnchors {}
    readonly property PanelMargins margins: PanelMargins {}
    property int exclusionMode: ExclusionMode.Normal
    property var mask: null
    readonly property Item contentItem: win.contentItem
    readonly property Window window: win

    readonly property bool dwNoFocus: WlrLayershell.keyboardFocus === WlrKeyboardFocus.None
    readonly property var dwScreen: root.screen ?? DesktopHost.primaryScreen

    // Evaluated only once the derived window set its own bindings (else every one of them is a binding loop).
    property bool dwReady: false
    readonly property rect dwGeometry: {
        if (!root.dwReady)
            return Qt.rect(0, 0, 1, 1);
        const s = root.dwScreen;
        const ignore = root.exclusionMode === ExclusionMode.Ignore;
        const ax = s ? Number(ignore ? s.x : s.availableX) : 0;
        const ay = s ? Number(ignore ? s.y : s.availableY) : 0;
        const aw = s ? Number(ignore ? s.width : s.availableWidth) : 800;
        const ah = s ? Number(ignore ? s.height : s.availableHeight) : 600;
        const a = root.anchors;
        const m = root.margins;
        const span = (start, size, before, after, lo, hi, want) => {
            // [position, length] along one axis
            if (lo && hi && want <= 0)
                return [start + before, Math.max(1, size - before - after)];
            const len = Math.max(1, Math.ceil(want));
            if (lo && hi)
                return [start + before + (size - before - after - len) / 2, len];
            if (lo)
                return [start + before, len];
            if (hi)
                return [start + size - after - len, len];
            return [start + (size - len) / 2, len];
        };
        const h = span(ax, aw, m.left, m.right, a.left, a.right, root.implicitWidth);
        const v = span(ay, ah, m.top, m.bottom, a.top, a.bottom, root.implicitHeight);
        return Qt.rect(Math.round(h[0]), Math.round(v[0]), h[1], v[1]);
    }

    // Is the global point inside the input region?
    function maskContains(gx: real, gy: real): bool {
        const lx = gx - win.x;
        const ly = gy - win.y;
        if (lx < 0 || ly < 0 || lx >= win.width || ly >= win.height)
            return false;
        const m = root.mask;
        if (!m)
            return true;
        const it = m.item;
        if (!it || !it.visible)
            return false;
        const p = it.mapToItem(null, 0, 0);
        const w = it.width;
        const h = it.height;
        if (m.shape === RegionShape.Ellipse) {
            const rx = w / 2;
            const ry = h / 2;
            if (rx <= 0 || ry <= 0)
                return false;
            const dx = (lx - p.x - rx) / rx;
            const dy = (ly - p.y - ry) / ry;
            return dx * dx + dy * dy <= 1;
        }
        return lx >= p.x && ly >= p.y && lx < p.x + w && ly < p.y + h;
    }

    readonly property bool dwDropOnly: WlrLayershell.namespace === "dropwheel-hotzone"
    property bool dwArmed: false

    function dwClickThrough(): bool {
        const c = DesktopHost.cursor();
        if (root.dwDropOnly) {
            const held = DesktopHost.buttonDown();
            const inside = root.maskContains(c.x, c.y);
            if (!held)
                root.dwArmed = false;
            else if (!inside)
                root.dwArmed = true; // a drag that started elsewhere may come in
            return !(held && root.dwArmed);
        }
        if (!root.mask)
            return false;
        return !root.maskContains(c.x, c.y);
    }

    function dwApplyStyle(): void {
        DesktopHost.updateWindow(win, root.dwClickThrough(), root.dwNoFocus);
    }

    onDwNoFocusChanged: {
        root.dwApplyStyle();
        if (!root.dwNoFocus && win.visible)
            DesktopHost.activate(win);
    }
    onMaskChanged: root.dwApplyStyle()
    Component.onCompleted: {
        root.dwReady = true;
        root.dwApplyStyle();
    }

    property Timer dwMaskTimer: Timer {
        interval: 30
        repeat: true
        running: win.visible && (root.mask !== null || root.dwDropOnly) && DesktopHost.isWindows
        onTriggered: root.dwApplyStyle()
    }

    property Window dwWindow: Window {
        id: win

        x: root.dwGeometry.x
        y: root.dwGeometry.y
        width: root.dwGeometry.width
        height: root.dwGeometry.height
        color: root.color
        visible: root.visible
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint

        onVisibleChanged: {
            if (!win.visible)
                return;
            root.dwApplyStyle();
            if (!root.dwNoFocus)
                DesktopHost.activate(win);
        }
    }
}
