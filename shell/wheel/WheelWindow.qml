pragma ComponentBehavior: Bound

// WheelWindow — the Overlay layer surface that hosts the Wheel (SPEC §2.2, verified facts):
//   * namespace "dropwheel-wheel", WlrLayer.Overlay, ExclusionMode.Ignore, transparent;
//   * anchored top+left with margins so the SURFACE CENTRE IS THE CURSOR — Hyprland sends the DnD enter at the surface
//     centre and no motion until the pointer moves. Only the DISC is clamped to the screen: near an edge the margins
//     go negative and the transparent label area hangs off the screen (Hyprland adds layer margins without clamping);
//     the Wheel lays the pill and the breadcrumb out inside the part that is visible (Wheel.visibleArea), and the
//     wheel scale is capped per screen so the disc always fits (Wheel.maxScale);
//   * mask = Region { item: disc; shape: RegionShape.Ellipse }: only the disc takes input, drops elsewhere fall
//     through to the windows below;
//   * keyboardFocus None in drag mode (an overlay with focus would steal it from the drag source). In click mode a
//     HyprlandFocusGrab closes the wheel when the user clicks elsewhere, and the focus depends on how the surface
//     started: Exclusive when it was created in click mode, OnDemand when it was already mapped. Hyprland ends the
//     seat grab whenever a mapped surface turns Exclusive (CLayerSurface::onCommit → setGrab(nullptr)), and the grab
//     is always committed first because the interactivity only goes out with the next surface commit: the wheel
//     would close itself right after a drop on a group. OnDemand has no such path, and the grab's own refocus gives
//     the wheel the keyboard;
//   * the empty drop target is OnDemand without a grab: it gets the keyboard when it appears (Esc closes it), but
//     clicking another window moves the focus there, so the user can go and find the files;
//   * DropArea keys ["text/uri-list"]: enter coordinates are ignored (the synthetic move that follows the enter at
//     the same position too), positions come from onPositionChanged; onDropped reads drop.urls BEFORE
//     drop.accept(Qt.CopyAction) (Hyprland completes the DnD only when the data was received) and handles the drop
//     with Qt.callLater — the window is never hidden synchronously inside onDropped. Drops are always copies.
// The controller (shell.qml) owns files/menu state and runs actions; this window only reports what happened.
//
//   property string mode            "drag" | "click"
//   property var files              paths / URLs on the wheel ([] = none yet)
//   property var menu; property bool menuLoading; property string menuError, menuErrorDetails
//   property bool emptyTarget       click mode without files: drop target
//   property int springMs           spring-loading delay (config wheel.spring_ms)
//   readonly property Wheel wheel
//   readonly property bool dragInside   a drag is over the disc;  readonly property bool dragSeen  (since present())
//   readonly property bool closing;  readonly property real cursorX, cursorY (global logical px, see present())
//   signal filesHovered(var files)      a drag carrying these files is over the wheel (drag mode)
//   signal filesDropped(var files)      files dropped on a click-mode / empty wheel (they replace the current files)
//   signal actionChosen(var node, bool options)   signal cancelled()   signal clickModeRequested()
//   signal retryRequested()   signal dragExited()   signal closed() (close animation done, surface unmapped)
//   function present(screen, x, y, cursorKnown)   map on `screen` centred on the global point (x, y) + open animation
//   function dismiss(toHud, dx, dy)     close animation (towards the HUD when toHud), then unmap and emit closed()
//   function focusWheel()               give the Wheel keyboard focus (click mode)

import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Hyprland
import "../services"

PanelWindow {
    id: root

    property string mode: "drag"
    property var files: []
    property var menu: null
    property bool menuLoading: false
    property string menuError: ""
    property string menuErrorDetails: ""
    property bool emptyTarget: false
    property int springMs: 450

    readonly property Wheel wheel: wheelItem
    readonly property bool dragInside: dropArea.containsDrag
    readonly property bool dragSeen: priv.entered
    readonly property bool closing: wheelItem.closing
    readonly property real cursorX: priv.cursorX
    readonly property real cursorY: priv.cursorY

    signal filesHovered(var files)
    signal filesDropped(var files)
    signal actionChosen(var node, bool options)
    signal cancelled
    signal clickModeRequested
    signal retryRequested
    signal dragExited
    signal closed

    function present(targetScreen: var, x: real, y: real, cursorKnown: bool): void {
        if (targetScreen)
            priv.screenObj = targetScreen;
        priv.cursorX = x;
        priv.cursorY = y;
        priv.cursorKnown = cursorKnown;
        priv.entered = false;
        priv.moved = false;
        // Exclusive only for a surface about to be created (see the header): a mapped one must never turn Exclusive.
        priv.exclusiveSurface = root.mode === "click" && !root.emptyTarget && (!root.visible || priv.exclusiveSurface);
        // open() first: it ends the `closing` state of the last session, which would otherwise create the surface
        // with no keyboard focus.
        wheelItem.open();
        root.visible = true;
        if (root.mode === "click")
            root.focusWheel();
    }

    function dismiss(toHud: bool, dx: real, dy: real): void {
        if (!root.visible) {
            root.closed();
            return;
        }
        priv.exclusiveSurface = false;
        wheelItem.close(toHud, dx, dy);
    }

    function focusWheel(): void {
        wheelItem.forceActiveFocus();
    }

    function urlList(list: var): var {
        const out = [];
        const n = list?.length ?? 0;
        for (let i = 0; i < n; i++) {
            const s = String(list[i] ?? "").trim();
            if (s !== "" && out.indexOf(s) === -1)
                out.push(s);
        }
        return out;
    }

    function sameFiles(a: var, b: var): bool {
        const x = Backend.toPaths(a);
        const y = Backend.toPaths(b);
        if (x.length !== y.length)
            return false;
        for (let i = 0; i < x.length; i++) {
            if (x[i] !== y[i])
                return false;
        }
        return true;
    }

    // Position of the global cursor inside the surface (used while the pointer has not moved since the enter).
    function cursorLocal(): var {
        return {
            x: priv.cursorX - priv.screenX - priv.marginLeft,
            y: priv.cursorY - priv.screenY - priv.marginTop
        };
    }

    // Layer margin along one axis that puts the disc centre on `cursor` (screen-relative), keeping the disc on the
    // screen. The result is negative when the surface has to hang off the screen for that.
    function discMargin(cursor: real, screenSize: real, surfaceSize: real): int {
        if (screenSize <= 0)
            return Math.max(0, Math.round(cursor - surfaceSize / 2));
        const reach = wheelItem.discRadius + wheelItem.edgeMargin;
        const centre = screenSize > 2 * reach ? Math.max(reach, Math.min(screenSize - reach, cursor)) : screenSize / 2;
        return Math.round(centre - surfaceSize / 2);
    }

    function handleDrop(urls: var, x: real, y: real): void {
        if (!root.visible || wheelItem.closing)
            return;
        if (urls.length === 0) {
            root.cancelled();
            return;
        }
        if (root.emptyTarget || root.mode === "click") {
            root.filesDropped(urls);
            return;
        }
        if (!root.sameFiles(urls, root.files))
            root.filesHovered(urls);
        wheelItem.dropAt(x, y);
    }

    visible: false
    color: "transparent"
    implicitWidth: wheelItem.implicitWidth
    implicitHeight: wheelItem.implicitHeight
    anchors.top: true
    anchors.left: true
    screen: priv.screenObj
    margins.left: priv.marginLeft
    margins.top: priv.marginTop
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dropwheel-wheel"
    WlrLayershell.keyboardFocus: {
        if (root.mode !== "click" || wheelItem.closing)
            return WlrKeyboardFocus.None;
        return priv.exclusiveSurface ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand;
    }
    mask: Region {
        item: wheelItem.disc
        shape: RegionShape.Ellipse
    }

    onModeChanged: {
        if (root.mode === "click" && root.visible)
            Qt.callLater(root.focusWheel);
    }

    QtObject {
        id: priv

        property real cursorX: 0
        property real cursorY: 0
        property bool cursorKnown: false
        property bool entered: false
        property bool moved: false
        property real enterX: NaN
        property real enterY: NaN
        // The surface was created in click mode and may stay Exclusive (see the header).
        property bool exclusiveSurface: false
        // The ShellScreen the wheel is on (typed `var`: geometry is read in JS, logical px).
        property var screenObj: null
        readonly property real screenX: Number(screenObj?.x ?? 0) || 0
        readonly property real screenY: Number(screenObj?.y ?? 0) || 0
        readonly property real screenW: Number(screenObj?.width ?? 0) || 0
        readonly property real screenH: Number(screenObj?.height ?? 0) || 0
        readonly property real fitScale: {
            const side = Math.min(screenW, screenH);
            return side > 0 ? side / wheelItem.fitUnits : 1000;
        }
        readonly property int marginLeft: root.discMargin(cursorX - screenX, screenW, root.implicitWidth)
        readonly property int marginTop: root.discMargin(cursorY - screenY, screenH, root.implicitHeight)
    }

    // Click mode: a click outside the wheel (or on another window) closes it. Not while it is an empty drop target:
    // starting a drag in a file manager would otherwise dismiss the target before the files arrive.
    HyprlandFocusGrab {
        windows: [root]
        active: root.visible && root.mode === "click" && !root.emptyTarget && !wheelItem.closing
        onCleared: {
            if (root.visible && !wheelItem.closing && root.mode === "click")
                root.cancelled();
        }
    }

    Wheel {
        id: wheelItem

        anchors.fill: parent
        focus: true
        menu: root.menu
        mode: root.mode
        loading: root.menuLoading
        errorText: root.menuError
        errorDetails: root.menuErrorDetails
        empty: root.emptyTarget
        dropActive: dropArea.containsDrag
        springMs: root.springMs
        maxScale: priv.fitScale
        visibleArea: priv.screenW > 0 && priv.screenH > 0 ? Qt.rect(-priv.marginLeft, -priv.marginTop, priv.screenW, priv.screenH) : Qt.rect(0, 0, root.implicitWidth, root.implicitHeight)

        onActivated: (node, options) => root.actionChosen(node, options)
        onCancelled: root.cancelled()
        onClickModeRequested: root.clickModeRequested()
        onRetryRequested: root.retryRequested()
        onCloseFinished: {
            root.visible = false;
            root.closed();
        }
    }

    DropArea {
        id: dropArea

        anchors.fill: parent
        keys: ["text/uri-list"]

        onEntered: drag => {
            drag.accept(Qt.CopyAction);
            const first = !priv.entered;
            priv.entered = true;
            priv.moved = false;
            // Hyprland reports the surface centre here, not the pointer: remember it to skip the synthetic move.
            priv.enterX = drag.x;
            priv.enterY = drag.y;
            const urls = root.urlList(drag.urls);
            if (urls.length > 0 && !root.emptyTarget && root.mode === "drag" && !root.sameFiles(urls, root.files))
                root.filesHovered(urls);
            if (first && priv.cursorKnown && root.mode === "drag") {
                // Mapped under a still pointer: the cursor position from hyprctl is the real one.
                const p = root.cursorLocal();
                wheelItem.pointerMoved(p.x, p.y);
            }
        }

        onPositionChanged: drag => {
            if (!priv.moved) {
                if (Math.abs(drag.x - priv.enterX) < 0.5 && Math.abs(drag.y - priv.enterY) < 0.5)
                    return;
                priv.moved = true;
            }
            wheelItem.pointerMoved(drag.x, drag.y);
        }

        onExited: {
            priv.moved = false;
            wheelItem.pointerLeft();
            root.dragExited();
        }

        onDropped: drop => {
            // Read the data first: Hyprland completes the drag only if the offer was received.
            const urls = root.urlList(drop.urls);
            drop.accept(Qt.CopyAction);
            let x = drop.x;
            let y = drop.y;
            if (!priv.moved && priv.cursorKnown) {
                const p = root.cursorLocal();
                x = p.x;
                y = p.y;
            }
            priv.moved = false;
            Qt.callLater(() => root.handleDrop(urls, x, y));
        }
    }
}
