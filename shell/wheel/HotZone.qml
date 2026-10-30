pragma ComponentBehavior: Bound

// HotZone — a tiny always-mapped Overlay drop target in a screen corner (SPEC §2.1 trigger 2, config [hotzone]).
// Dragging files onto it opens the wheel in drag mode next to it (the user then moves onto the wheel); dropping
// directly on it opens the wheel in click mode with the dropped files. It never takes keyboard focus and is fully
// transparent; the shell creates one per screen (Variants over Quickshell.screens) while the hot zone is enabled.
//
//   property var targetScreen     ShellScreen to live on (required for multi-monitor; null = compositor default)
//   property string corner        "top-left" | "top-right" (default) | "bottom-left" | "bottom-right" — corners are
//                                 size×size squares; "top" | "bottom" | "left" | "right" — a strip centred on that
//                                 edge (size thick, max(120, 20 × size) long)
//   property int size             thickness in logical px (config hotzone.size, default 6, clamped 1–64)
//   readonly property bool dragInside
//   readonly property point anchorPoint   global logical position of the hot zone (where the wheel should open)
//   signal dragEntered(var files, point at)   a drag entered (files may be [] when the source has not sent them)
//   signal filesDropped(var files, point at)  files were dropped on the hot zone

import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: root

    property var targetScreen: null
    property string corner: "top-right"
    property int size: 6

    readonly property bool dragInside: dropArea.containsDrag
    readonly property int thickness: Math.max(1, Math.min(64, root.size))
    readonly property bool isEdge: ["top", "bottom", "left", "right"].indexOf(root.corner) !== -1
    readonly property int stripLength: Math.max(120, 20 * root.thickness)
    readonly property string effectiveCorner: ["top-left", "top-right", "bottom-left", "bottom-right", "top", "bottom", "left", "right"].indexOf(root.corner) !== -1 ? root.corner : "top-right"
    readonly property bool atTop: root.effectiveCorner.startsWith("top")
    readonly property bool atBottom: root.effectiveCorner.startsWith("bottom")
    readonly property bool atLeft: root.effectiveCorner.endsWith("left")
    readonly property bool atRight: root.effectiveCorner.endsWith("right")

    readonly property point anchorPoint: {
        const s = root.targetScreen;
        const sx = Number(s?.x ?? 0) || 0;
        const sy = Number(s?.y ?? 0) || 0;
        const sw = Number(s?.width ?? 0) || 0;
        const sh = Number(s?.height ?? 0) || 0;
        const x = root.atLeft ? sx : root.atRight ? sx + sw - 1 : sx + sw / 2;
        const y = root.atTop ? sy : root.atBottom ? sy + sh - 1 : sy + sh / 2;
        return Qt.point(Math.round(x), Math.round(y));
    }

    signal dragEntered(var files, point at)
    signal filesDropped(var files, point at)

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

    screen: root.targetScreen
    color: "transparent"
    implicitWidth: root.isEdge && (root.effectiveCorner === "top" || root.effectiveCorner === "bottom") ? root.stripLength : root.thickness
    implicitHeight: root.isEdge && (root.effectiveCorner === "left" || root.effectiveCorner === "right") ? root.stripLength : root.thickness
    anchors.top: root.atTop
    anchors.bottom: root.atBottom
    anchors.left: root.atLeft
    anchors.right: root.atRight
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dropwheel-hotzone"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    DropArea {
        id: dropArea

        anchors.fill: parent
        keys: ["text/uri-list"]

        onEntered: drag => {
            drag.accept(Qt.CopyAction);
            const files = root.urlList(drag.urls);
            root.dragEntered(files, root.anchorPoint);
        }

        onDropped: drop => {
            // Read the data before accepting (Hyprland only completes received offers); emit after the event.
            const files = root.urlList(drop.urls);
            drop.accept(Qt.CopyAction);
            const at = root.anchorPoint;
            Qt.callLater(() => root.filesDropped(files, at));
        }
    }
}
