pragma ComponentBehavior: Bound

// CenterHub — the centre disc of the wheel (SPEC §2.3): the file thumbnail or category icon (with the multi-file
// count badge), a spinner while the menu loads, the error icon, the empty drop-target icon, Back inside a sub-ring
// and × (cancel / close) when the centre is hovered at the top level. It never takes input itself: the Wheel
// hit-tests the centre (radius < inner radius) and drives `highlighted` / `springProgress`.
//
//   property real radius            disc radius (default 48 × Theme.scale)
//   property real fit               shrink factor for the Theme-sized icon and text (the wheel's capped scale, default 1)
//   property string mode            "file" | "loading" | "error" | "empty" | "back" | "cancel"
//   property bool highlighted       hovered / keyboard-selected
//   property real springProgress    0..1 ring drawn around the disc while Back is hovered in drag mode
//   property bool dropActive        a drag is over the wheel (the empty target turns primary)
//   property string thumb, category, icon, ext;  property int count     forwarded to FileIcon
//   readonly property string iconName   the Material Symbols icon shown for the non-file modes

import QtQuick
import QtQuick.Shapes
import "../services"
import "../components"
import "WheelText.js" as WT

Item {
    id: root

    property real radius: 48 * Theme.scale
    property real fit: 1
    property string mode: "file"
    property bool highlighted: false
    property real springProgress: 0
    property bool dropActive: false
    property string thumb: ""
    property string category: ""
    property string icon: ""
    property string ext: ""
    property int count: 1

    readonly property string iconName: {
        switch (root.mode) {
        case "error":
            return "error";
        case "empty":
            return "move_to_inbox";
        case "back":
            return "arrow_back";
        case "cancel":
            return "close";
        default:
            return "";
        }
    }

    readonly property color discColour: {
        switch (root.mode) {
        case "error":
            return Theme.palette.m3errorContainer;
        case "empty":
            return root.dropActive ? Theme.palette.m3primaryContainer : Theme.tPalette.m3surfaceContainerHigh;
        case "back":
            return root.highlighted ? Theme.palette.m3secondary : Theme.palette.m3secondaryContainer;
        case "cancel":
            return Theme.palette.m3surfaceContainerHighest;
        default:
            return Theme.tPalette.m3surfaceContainer;
        }
    }

    readonly property color contentColour: {
        switch (root.mode) {
        case "error":
            return Theme.palette.m3onErrorContainer;
        case "empty":
            return root.dropActive ? Theme.palette.m3onPrimaryContainer : Theme.palette.m3onSurfaceVariant;
        case "back":
            return root.highlighted ? Theme.palette.m3onSecondary : Theme.palette.m3onSecondaryContainer;
        default:
            return Theme.palette.m3onSurface;
        }
    }

    implicitWidth: root.radius * 2
    implicitHeight: root.radius * 2
    width: implicitWidth
    height: implicitHeight

    Accessible.role: Accessible.Button
    Accessible.name: root.mode === "back" ? I18n.t("ui.back") : root.mode === "cancel" ? WT.tr(I18n, "wheel.cancel") : ""

    StyledRect {
        id: disc

        anchors.fill: parent
        radius: width / 2
        color: root.discColour
        scale: root.highlighted || (root.mode === "empty" && root.dropActive) ? 1.05 : 1

        Behavior on scale {
            Anim {
                type: Anim.FastSpatial
            }
        }
    }

    FileIcon {
        anchors.centerIn: parent
        visible: root.mode === "file"
        size: Math.round(root.radius * 1.15)
        thumb: root.thumb
        category: root.category
        icon: root.icon
        ext: root.ext
        count: Math.max(1, root.count)
    }

    CircularProgress {
        anchors.centerIn: parent
        visible: root.mode === "loading"
        running: visible
        indeterminate: true
        width: Math.round(root.radius * 0.9)
        height: width
        strokeWidth: Math.max(2, Math.round(3.5 * Theme.scale * root.fit))
    }

    Column {
        anchors.centerIn: parent
        visible: root.iconName !== ""
        spacing: 0

        MaterialIcon {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.iconName
            color: root.contentColour
            size: (root.mode === "back" ? Theme.font.iconSize.large : Theme.font.iconSize.extraLarge * 0.85) * root.fit
            fill: 1
        }

        StyledText {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: root.mode === "back"
            text: I18n.t("ui.back")
            color: root.contentColour
            font.pointSize: Theme.font.size.small * root.fit
            font.weight: Font.Medium
            width: Math.min(implicitWidth, root.radius * 1.6)
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
        }
    }

    // Spring-loading ring for Back (drag mode).
    Shape {
        anchors.fill: parent
        visible: root.springProgress > 0.001
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            fillColor: "transparent"
            strokeColor: Theme.palette.m3primary
            strokeWidth: Math.max(2, 3 * Theme.scale * root.fit)
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.width / 2
                centerY: root.height / 2
                radiusX: Math.max(1, root.radius - 2 * Theme.scale * root.fit)
                radiusY: Math.max(1, root.radius - 2 * Theme.scale * root.fit)
                startAngle: -90
                sweepAngle: Math.max(0.1, 359 * Math.max(0, Math.min(1, root.springProgress)))
            }
        }
    }
}
