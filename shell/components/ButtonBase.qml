pragma ComponentBehavior: Bound

// ButtonBase — shared base of IconButton and TextButton (Caelestia ButtonBase): colours per type, radius morph
// (pressed → rounding.small, checked → rounding.medium, round → height/2, default → rounding.large), StateLayer,
// keyboard activation (Space/Enter/Return when focused), focus ring and optional tooltip.
//
//   enum Type: ButtonBase.Filled | ButtonBase.Tonal | ButtonBase.Text | ButtonBase.Outlined
//             (also reachable as IconButton.Filled, TextButton.Tonal, …)
//   property int type          default Filled
//   property bool checked      external checked state (copied into internalChecked)
//   property bool isToggle     clicking flips internalChecked
//   property bool disabled
//   property bool danger       error colours (destructive actions)
//   property bool isRound      fully round when idle
//   property bool radiusMorph  default true
//   property bool shapeMorph   bigger ripple
//   property string tooltip    shows a Tooltip on hover when not empty (also used as Accessible.name)
//   property real padding / horizontalPadding / verticalPadding
//   property color activeColour, inactiveColour, activeOnColour, inactiveOnColour, disabledColour, disabledOnColour
//   readonly property color onColour      the content colour for the current state
//   readonly property bool pressed, hovered;  readonly property StateLayer stateLayer
//   property bool internalChecked         current checked state (toggle buttons)
//   signal clicked()
//   function trigger()                    same as a click (ripple + toggle + clicked)
//   Set `activeFocusOnTab: true` to make it keyboard focusable.

import QtQuick
import "../services"

StyledRect {
    id: root

    enum Type {
        Filled,
        Tonal,
        Text,
        Outlined
    }

    property int type: ButtonBase.Filled
    property bool checked: false
    property bool isToggle: false
    property bool disabled: false
    property bool danger: false
    property bool isRound: false
    property bool radiusMorph: true
    property alias shapeMorph: stateLayerItem.shapeMorph
    property string tooltip: ""
    property bool showFocusRing: true

    property real padding: 0
    property real horizontalPadding: padding
    property real verticalPadding: padding

    readonly property alias pressed: stateLayerItem.pressed
    readonly property alias hovered: stateLayerItem.containsMouse
    readonly property alias stateLayer: stateLayerItem

    property color activeColour: Theme.palette.m3primary
    property color inactiveColour: Theme.palette.m3primary
    property color activeOnColour: Theme.palette.m3onPrimary
    property color inactiveOnColour: Theme.palette.m3onPrimary
    property color disabledColour: Qt.alpha(Theme.palette.m3onSurface, 0.1)
    property color disabledOnColour: Qt.alpha(Theme.palette.m3onSurface, 0.38)

    property bool internalChecked: false
    readonly property color onColour: disabled ? disabledOnColour : internalChecked ? activeOnColour : inactiveOnColour

    property real pressedRadius: Theme.rounding.small
    property real checkedRadius: Theme.rounding.medium
    property real defaultRadius: Theme.rounding.large

    signal clicked

    function trigger(): void {
        if (root.disabled)
            return;
        if (root.isToggle)
            root.internalChecked = !root.internalChecked;
        root.clicked();
    }

    onCheckedChanged: internalChecked = checked
    Component.onCompleted: internalChecked = checked

    radius: {
        if (radiusMorph && pressed)
            return pressedRadius;
        if (internalChecked && isToggle)
            return checkedRadius;
        if (isRound)
            return (height || implicitHeight) / 2;
        return defaultRadius;
    }
    color: {
        if (type === ButtonBase.Text || (type === ButtonBase.Outlined && !internalChecked))
            return "transparent";
        if (disabled)
            return disabledColour;
        return internalChecked ? activeColour : inactiveColour;
    }
    border.width: type === ButtonBase.Outlined && !internalChecked ? Math.max(1, Math.round(Theme.scale)) : 0
    border.color: disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : Theme.palette.m3outlineVariant

    Accessible.role: Accessible.Button
    Accessible.name: root.tooltip
    Accessible.onPressAction: root.trigger()

    Keys.onPressed: event => {
        if (root.disabled)
            return;
        if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            stateLayerItem.press(root.width / 2, root.height / 2);
            root.trigger();
            event.accepted = true;
        }
    }

    StateLayer {
        id: stateLayerItem

        color: root.internalChecked ? root.activeOnColour : root.inactiveOnColour
        disabled: root.disabled
        manualHoverOverride: root.activeFocus
        onClicked: root.trigger()
    }

    // Keyboard focus indicator (M3: 3 dp outline in secondary, offset from the container).
    Rectangle {
        anchors.fill: parent
        anchors.margins: -Math.round(3 * Theme.scale)
        radius: root.radius + Math.round(3 * Theme.scale)
        color: "transparent"
        border.width: Math.max(1, Math.round(2 * Theme.scale))
        border.color: Theme.palette.m3secondary
        visible: root.activeFocus && root.showFocusRing && !root.disabled
    }

    Loader {
        active: root.tooltip !== ""
        sourceComponent: Tooltip {
            target: root
            text: root.tooltip
        }
    }

    Behavior on radius {
        Anim {
            type: Anim.DefaultEffects
        }
    }
}
