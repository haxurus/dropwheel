pragma ComponentBehavior: Bound

// ResultChip — one output file of a finished job, draggable to any app (SPEC §2.4).
// Pressing it grabs the chip into an image (grabToImage) used as the drag icon; moving past the drag threshold starts
// a native Wayland drag (Drag.Automatic) offering text/uri-list (URI + CRLF) and text/plain (path), copy only.
// A click (without dragging) opens the file. Hyprland does not report the drop action back, so dragFinished's action
// is ignored.
//
//   required property var output   {path, uri, name, size, size_h, category, mime, thumb} (SPEC §5.2)
//   property real maxWidth         default 320 × scale
//   readonly property bool dragging     a drag of this chip is in progress
//   readonly property bool hovered
//   readonly property string path, uri, name, sizeText
//   signal clicked()               activated without dragging (the HUD opens the file)

import QtQuick
import "../services"
import "../components"
import "../wheel/WheelText.js" as WT

Item {
    id: root

    required property var output
    property real maxWidth: Math.round(320 * Theme.scale)

    readonly property string path: String(root.output?.path ?? "")
    readonly property string uri: {
        const u = String(root.output?.uri ?? "");
        return u !== "" ? u : Backend.fileUrl(root.path);
    }
    readonly property string name: {
        const n = String(root.output?.name ?? "");
        if (n !== "")
            return n;
        const p = root.path.replace(/\/+$/, "");
        return p.slice(p.lastIndexOf("/") + 1);
    }
    readonly property string sizeText: {
        const h = String(root.output?.size_h ?? "");
        if (h !== "")
            return h;
        const s = root.output?.size;
        return (typeof s === "number" && isFinite(s) && s >= 0) ? I18n.formatSize(s) : "";
    }
    readonly property string ext: {
        const i = root.name.lastIndexOf(".");
        return i > 0 && i < root.name.length - 1 ? root.name.slice(i + 1).toLowerCase() : "";
    }
    readonly property bool dragging: dragArea.drag.active
    readonly property bool hovered: dragArea.containsMouse
    property var grabResult: null

    signal clicked

    implicitWidth: Math.min(root.maxWidth, Math.ceil(row.implicitWidth + Theme.padding.small * 2))
    implicitHeight: Math.ceil(Math.max(icon.height, textColumn.implicitHeight) + Theme.padding.small * 2)

    Accessible.role: Accessible.Button
    Accessible.name: root.name
    Accessible.description: WT.tr(I18n, "hud.drag_hint")
    Accessible.onPressAction: root.clicked()

    Drag.active: dragArea.drag.active
    Drag.dragType: Drag.Automatic
    Drag.supportedActions: Qt.CopyAction
    Drag.proposedAction: Qt.CopyAction
    Drag.mimeData: ({
            "text/uri-list": root.uri + "\r\n",
            "text/plain": root.path !== "" ? root.path : root.uri
        })
    Drag.onDragFinished: dropAction => {
        dragProxy.x = 0;
        dragProxy.y = 0;
    }

    StyledRect {
        id: face

        anchors.fill: parent
        radius: Theme.rounding.medium
        color: root.dragging ? Theme.palette.m3secondaryContainer : Theme.tPalette.m3surfaceContainerHigh
        border.width: Math.max(1, Math.round(Theme.scale))
        border.color: root.hovered ? Qt.alpha(Theme.palette.m3primary, 0.55) : Qt.alpha(Theme.palette.m3outlineVariant, 0.4)

        Row {
            id: row

            anchors.left: parent.left
            anchors.leftMargin: Theme.padding.small
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacing.small

            FileIcon {
                id: icon

                anchors.verticalCenter: parent.verticalCenter
                size: Math.round(32 * Theme.scale)
                thumb: String(root.output?.thumb ?? "")
                category: String(root.output?.category ?? "")
                ext: root.ext
                showBadge: false
            }

            Column {
                id: textColumn

                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, Math.min(Math.max(nameLabel.implicitWidth, sizeLabel.implicitWidth), root.maxWidth - Theme.padding.small * 2 - icon.width - grip.width - Theme.spacing.small * 2))

                StyledText {
                    id: nameLabel

                    width: parent.width
                    text: root.name
                    elide: Text.ElideMiddle
                    font.pointSize: Theme.font.size.smaller
                    font.weight: Font.Medium
                }

                StyledText {
                    id: sizeLabel

                    width: parent.width
                    visible: text !== ""
                    text: root.sizeText
                    elide: Text.ElideRight
                    color: Theme.palette.m3onSurfaceVariant
                    font.pointSize: Theme.font.size.small
                }
            }

            MaterialIcon {
                id: grip

                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: 1
                text: "drag_indicator"
                color: Theme.palette.m3onSurfaceVariant
                opacity: root.hovered || root.dragging ? 0.9 : 0.45
                size: Theme.font.iconSize.small
            }
        }
    }

    // The MouseArea moves this dummy so `drag.active` turns on; the chip itself stays in place.
    Item {
        id: dragProxy

        width: 1
        height: 1
    }

    MouseArea {
        id: dragArea

        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: dragArea.drag.active ? Qt.ClosedHandCursor : Qt.PointingHandCursor
        drag.target: dragProxy
        drag.threshold: Math.max(4, Math.round(6 * Theme.scale))

        onPressed: event => {
            dragProxy.x = 0;
            dragProxy.y = 0;
            root.Drag.hotSpot.x = event.x;
            root.Drag.hotSpot.y = event.y;
            face.grabToImage(result => {
                root.grabResult = result; // keep the image alive while it is the drag icon
                root.Drag.imageSource = result.url;
            });
        }
        onReleased: {
            dragProxy.x = 0;
            dragProxy.y = 0;
        }
        onClicked: {
            if (!dragArea.drag.active)
                root.clicked();
        }
    }

    Tooltip {
        target: root
        text: root.path !== "" ? root.path + "\n" + WT.tr(I18n, "hud.drag_hint") : WT.tr(I18n, "hud.drag_hint")
        enabled: !root.dragging
    }
}
