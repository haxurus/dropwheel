// M3Switch — M3 switch (52 × 32 dp track) with growing handle, check/close icons and hover/press halo.
// Click, Space, Enter or Return toggles. activeFocusOnTab is true.
//
//   property bool checked
//   property bool disabled
//   property bool icons: true          check / close glyph inside the handle
//   readonly property bool pressed, hovered
//   signal toggled()                   emitted after `checked` changed by the user
//   function toggle()

import QtQuick
import "../services"

Item {
    id: root

    property bool checked: false
    property bool disabled: false
    property bool icons: true
    readonly property bool pressed: mouse.pressed
    readonly property bool hovered: mouse.containsMouse

    signal toggled

    function toggle(): void {
        if (root.disabled)
            return;
        root.checked = !root.checked;
        root.toggled();
    }

    enabled: !disabled
    activeFocusOnTab: true
    implicitWidth: Math.round(52 * Theme.scale)
    implicitHeight: Math.round(32 * Theme.scale)

    Accessible.role: Accessible.CheckBox
    Accessible.checkable: true
    Accessible.checked: root.checked
    Accessible.onToggleAction: root.toggle()
    Accessible.onPressAction: root.toggle()

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            root.toggle();
            event.accepted = true;
        }
    }

    StyledRect {
        id: track

        anchors.fill: parent
        radius: height / 2
        color: {
            if (root.disabled)
                return root.checked ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : Qt.alpha(Theme.palette.m3surfaceContainerHighest, 0.12);
            return root.checked ? Theme.palette.m3primary : Theme.palette.m3surfaceContainerHighest;
        }
        border.width: root.checked ? 0 : Math.max(1, Math.round(2 * Theme.scale))
        border.color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : Theme.palette.m3outline

        // Keyboard focus ring.
        Rectangle {
            anchors.fill: parent
            anchors.margins: -Math.round(3 * Theme.scale)
            radius: height / 2
            color: "transparent"
            border.width: Math.max(1, Math.round(2 * Theme.scale))
            border.color: Theme.palette.m3secondary
            visible: root.activeFocus && !root.disabled
        }

        Item {
            id: handleSlot

            readonly property real size: {
                if (root.pressed)
                    return Math.round(28 * Theme.scale);
                if (root.checked || root.icons)
                    return Math.round(24 * Theme.scale);
                return Math.round(16 * Theme.scale);
            }

            width: track.height
            height: track.height
            x: root.checked ? track.width - width : 0

            Behavior on x {
                Anim {
                    type: Anim.FastSpatial
                }
            }

            // Hover / press halo (40 dp).
            StyledRect {
                anchors.centerIn: parent
                width: Math.round(40 * Theme.scale)
                height: width
                radius: width / 2
                color: root.checked ? Theme.palette.m3primary : Theme.palette.m3onSurface
                opacity: root.disabled ? 0 : root.pressed ? 0.1 : (root.hovered || root.activeFocus) ? 0.08 : 0

                Behavior on opacity {
                    Anim {
                        type: Anim.DefaultEffects
                    }
                }
            }

            StyledRect {
                id: handle

                anchors.centerIn: parent
                width: handleSlot.size
                height: handleSlot.size
                radius: width / 2
                color: {
                    if (root.disabled)
                        return root.checked ? Theme.palette.m3surface : Qt.alpha(Theme.palette.m3onSurface, 0.38);
                    if (root.checked)
                        return root.pressed || root.hovered ? Theme.palette.m3primaryContainer : Theme.palette.m3onPrimary;
                    return root.pressed || root.hovered ? Theme.palette.m3onSurfaceVariant : Theme.palette.m3outline;
                }

                Behavior on width {
                    Anim {
                        type: Anim.FastSpatial
                    }
                }
                Behavior on height {
                    Anim {
                        type: Anim.FastSpatial
                    }
                }

                MaterialIcon {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: 1
                    visible: root.icons
                    text: root.checked ? "check" : "close"
                    size: Theme.font.iconSize.small * 0.85
                    fill: 1
                    weight: 600
                    color: {
                        if (root.disabled)
                            return root.checked ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3surfaceContainerHighest;
                        return root.checked ? Theme.palette.m3onPrimaryContainer : Theme.palette.m3surfaceContainerHighest;
                    }
                }
            }
        }
    }

    MouseArea {
        id: mouse

        anchors.fill: parent
        anchors.margins: -Math.round(4 * Theme.scale)
        enabled: !root.disabled
        hoverEnabled: true
        cursorShape: root.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
        onClicked: root.toggle()
    }
}
