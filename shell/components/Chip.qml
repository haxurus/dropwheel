pragma ComponentBehavior: Bound

// Chip — M3 chips: assist (outlined, primary icon), filter (selected → tonal fill + check), input (removable)
// and suggestion. The chip does not change `selected` by itself: handle clicked() and update your binding.
//
//   enum Type: Chip.Assist | Chip.Filter (default) | Chip.Input | Chip.Suggestion
//   property int type
//   property string text
//   property string icon          leading icon (filter chips show "check" instead while selected)
//   property bool selected        filter / input chips
//   property bool disabled
//   property bool elevated        surfaceContainerLow fill + level 1 shadow instead of the outline
//   property bool removable       trailing × (input chips); emits removed()
//   property string tooltip
//   signal clicked()   signal removed()
//   Keyboard: Space/Enter/Return = click, Delete/Backspace = remove (when removable). activeFocusOnTab is false by
//   default; set it to true in forms.

import QtQuick
import "../services"

StyledRect {
    id: root

    enum Type {
        Assist,
        Filter,
        Input,
        Suggestion
    }

    property int type: Chip.Filter
    property string text: ""
    property string icon: ""
    property bool selected: false
    property bool disabled: false
    property bool elevated: false
    property bool removable: false
    property string tooltip: ""

    readonly property bool showSelected: root.selected && (root.type === Chip.Filter || root.type === Chip.Input || root.type === Chip.Suggestion)
    readonly property string leadingIcon: root.type === Chip.Filter && root.selected ? "check" : root.icon
    readonly property color contentColour: {
        if (root.disabled)
            return Qt.alpha(Theme.palette.m3onSurface, 0.38);
        if (root.showSelected)
            return Theme.palette.m3onSecondaryContainer;
        return root.type === Chip.Assist ? Theme.palette.m3onSurface : Theme.palette.m3onSurfaceVariant;
    }
    readonly property color iconColour: {
        if (root.disabled)
            return Qt.alpha(Theme.palette.m3onSurface, 0.38);
        if (root.showSelected)
            return Theme.palette.m3onSecondaryContainer;
        return Theme.palette.m3primary;
    }

    signal clicked
    signal removed

    implicitHeight: Math.round(32 * Theme.scale)
    implicitWidth: row.implicitWidth + (leading.visible ? Theme.padding.small : Theme.padding.large) + (trailing.visible ? Theme.padding.small : Theme.padding.large)
    radius: Theme.rounding.small
    color: {
        if (root.disabled)
            return root.showSelected ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : "transparent";
        if (root.showSelected)
            return Theme.palette.m3secondaryContainer;
        return root.elevated ? Theme.palette.m3surfaceContainerLow : "transparent";
    }
    border.width: root.showSelected || root.elevated ? 0 : Math.max(1, Math.round(Theme.scale))
    border.color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : (root.type === Chip.Assist ? Theme.palette.m3outline : Theme.palette.m3outlineVariant)

    Accessible.role: root.type === Chip.Filter ? Accessible.CheckBox : Accessible.Button
    Accessible.name: root.text
    Accessible.checkable: root.type === Chip.Filter
    Accessible.checked: root.selected
    Accessible.onPressAction: if (!root.disabled)
        root.clicked()

    Keys.onPressed: event => {
        if (root.disabled)
            return;
        if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            stateLayer.press(root.width / 2, root.height / 2);
            root.clicked();
            event.accepted = true;
        } else if (root.removable && (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace)) {
            root.removed();
            event.accepted = true;
        }
    }

    Elevation {
        anchors.fill: parent
        radius: root.radius
        z: -1
        level: root.elevated && !root.disabled ? (stateLayer.containsMouse ? 2 : 1) : 0
    }

    StateLayer {
        id: stateLayer

        color: root.contentColour
        disabled: root.disabled
        manualHoverOverride: root.activeFocus
        onClicked: root.clicked()
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -Math.round(3 * Theme.scale)
        radius: root.radius + Math.round(3 * Theme.scale)
        color: "transparent"
        border.width: Math.max(1, Math.round(2 * Theme.scale))
        border.color: Theme.palette.m3secondary
        visible: root.activeFocus && !root.disabled
    }

    Row {
        id: row

        anchors.verticalCenter: parent.verticalCenter
        x: leading.visible ? Theme.padding.small : Theme.padding.large
        spacing: Theme.spacing.small

        MaterialIcon {
            id: leading

            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: 1
            visible: root.leadingIcon !== ""
            text: root.leadingIcon
            size: Theme.font.iconSize.small
            fill: root.showSelected ? 1 : 0
            color: root.iconColour
        }

        StyledText {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            color: root.contentColour
            font.pointSize: Theme.font.size.smaller
            font.weight: Font.Medium
        }

        Item {
            id: trailing

            anchors.verticalCenter: parent.verticalCenter
            visible: root.removable
            implicitWidth: removeIcon.implicitWidth
            implicitHeight: removeIcon.implicitHeight

            MaterialIcon {
                id: removeIcon

                anchors.centerIn: parent
                anchors.verticalCenterOffset: 1
                text: "close"
                size: Theme.font.iconSize.small
                color: root.contentColour
            }

            MouseArea {
                anchors.fill: parent
                anchors.margins: -Math.round(4 * Theme.scale)
                enabled: !root.disabled
                cursorShape: Qt.PointingHandCursor
                onClicked: root.removed()
            }
        }
    }

    Loader {
        active: root.tooltip !== ""
        sourceComponent: Tooltip {
            target: root
            text: root.tooltip
        }
    }
}
