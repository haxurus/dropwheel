pragma ComponentBehavior: Bound

// M3Slider — M3 expressive slider (Caelestia look): active track, thin vertical handle with gaps, inactive track
// with a stop dot, optional tick marks and a value label bubble above the handle while dragging / adjusting.
// Keyboard: ← ↓ / → ↑ step, PageUp/PageDown 10 %, Home/End. activeFocusOnTab is true.
//
//   property real from: 0, to: 1, value: 0, stepSize: 0 (0 = continuous)
//   property bool disabled
//   property bool showValue: true          value bubble while pressed / keyboard-adjusted
//   property bool valueAlwaysVisible: false
//   property bool showTicks: false         dots at every step (only when there are ≤ 50 steps)
//   property int decimals: -1              digits in the value label (-1 = derived from stepSize)
//   property string unit: ""               appended to the value label ("%", " px")
//   property var formatValue: null         function(value) → string, overrides decimals/unit
//   readonly property bool pressed
//   readonly property real position       0..1
//   readonly property string valueText
//   signal moved()                         the user changed `value` (drag, click, keys)
//   function increase(), decrease(), setValue(v)  (setValue snaps/clamps and emits moved() when it changes)

import QtQuick
import "../services"

Item {
    id: root

    property real from: 0
    property real to: 1
    property real value: 0
    property real stepSize: 0
    property bool disabled: false
    property bool showValue: true
    property bool valueAlwaysVisible: false
    property bool showTicks: false
    property int decimals: -1
    property string unit: ""
    property var formatValue: null

    readonly property bool pressed: mouse.pressed
    readonly property real position: {
        const span = root.to - root.from;
        if (!isFinite(span) || span === 0)
            return 0;
        return Math.max(0, Math.min(1, (root.value - root.from) / span));
    }
    readonly property string valueText: {
        const v = root.value;
        if (typeof root.formatValue === "function") {
            try {
                return String(root.formatValue(v));
            } catch (e) {
                return String(v);
            }
        }
        let d = root.decimals;
        if (d < 0) {
            if (root.stepSize > 0) {
                const s = String(root.stepSize);
                const dot = s.indexOf(".");
                d = dot >= 0 ? Math.min(4, s.length - dot - 1) : 0;
            } else {
                const span = Math.abs(root.to - root.from);
                d = span <= 2 ? 2 : (span <= 20 ? 1 : 0);
            }
        }
        return I18n.formatNumber(v, d) + root.unit;
    }

    signal moved

    function snap(v: real): real {
        const lo = Math.min(root.from, root.to);
        const hi = Math.max(root.from, root.to);
        let x = Math.max(lo, Math.min(hi, v));
        if (root.stepSize > 0) {
            x = root.from + Math.round((x - root.from) / root.stepSize) * root.stepSize;
            x = Math.max(lo, Math.min(hi, x));
        }
        return Number(x.toFixed(10));
    }

    function setValue(v: real): void {
        const next = root.snap(v);
        if (next !== root.value) {
            root.value = next;
            root.moved();
        }
    }

    function increase(): void {
        root.setValue(root.value + priv.keyStep);
    }

    function decrease(): void {
        root.setValue(root.value - priv.keyStep);
    }

    enabled: !disabled
    activeFocusOnTab: true
    implicitWidth: Math.round(200 * Theme.scale)
    implicitHeight: Math.round(44 * Theme.scale)

    Keys.onPressed: event => {
        const span = root.to - root.from;
        let handled = true;
        switch (event.key) {
        case Qt.Key_Left:
        case Qt.Key_Down:
            root.decrease();
            break;
        case Qt.Key_Right:
        case Qt.Key_Up:
            root.increase();
            break;
        case Qt.Key_PageUp:
            root.setValue(root.value + Math.max(priv.keyStep, span / 10));
            break;
        case Qt.Key_PageDown:
            root.setValue(root.value - Math.max(priv.keyStep, span / 10));
            break;
        case Qt.Key_Home:
            root.setValue(root.from);
            break;
        case Qt.Key_End:
            root.setValue(root.to);
            break;
        default:
            handled = false;
        }
        if (handled) {
            priv.keyboardActive = true;
            keyboardHide.restart();
            event.accepted = true;
        }
    }

    onActiveFocusChanged: {
        if (!activeFocus)
            priv.keyboardActive = false;
    }

    QtObject {
        id: priv

        readonly property real keyStep: root.stepSize > 0 ? root.stepSize : Math.abs(root.to - root.from) / 100
        property bool keyboardActive: false
        readonly property real trackHeight: Math.round(16 * Theme.scale)
        readonly property real handleWidth: Math.max(2, Math.round(4 * Theme.scale))
        readonly property real gap: Math.max(2, Math.round(6 * Theme.scale))
        readonly property real usable: Math.max(0, track.width - handleWidth)
        readonly property real handleX: usable * root.position
        readonly property bool bubbleVisible: root.showValue && (root.valueAlwaysVisible || mouse.pressed || keyboardActive)
        readonly property int tickCount: {
            if (!root.showTicks || root.stepSize <= 0)
                return 0;
            const n = Math.round(Math.abs(root.to - root.from) / root.stepSize);
            return n > 0 && n <= 50 ? n + 1 : 0;
        }
        readonly property color activeColour: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3primary
        readonly property color inactiveColour: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.12) : Theme.palette.m3secondaryContainer
    }

    Timer {
        id: keyboardHide

        interval: 1200
        onTriggered: priv.keyboardActive = false
    }

    Item {
        id: track

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: priv.trackHeight

        // Active part (left of the handle).
        Rectangle {
            id: activePart

            x: 0
            width: Math.max(0, priv.handleX - priv.gap)
            height: parent.height
            visible: width > 0.5
            color: priv.activeColour
            topLeftRadius: height / 2
            bottomLeftRadius: height / 2
            topRightRadius: Math.min(width / 2, Math.round(2 * Theme.scale))
            bottomRightRadius: Math.min(width / 2, Math.round(2 * Theme.scale))

            Behavior on color {
                CAnim {}
            }
        }

        // Inactive part (right of the handle).
        Rectangle {
            id: inactivePart

            x: priv.handleX + priv.handleWidth + priv.gap
            width: Math.max(0, parent.width - x)
            height: parent.height
            visible: width > 0.5
            color: priv.inactiveColour
            topRightRadius: height / 2
            bottomRightRadius: height / 2
            topLeftRadius: Math.min(width / 2, Math.round(2 * Theme.scale))
            bottomLeftRadius: Math.min(width / 2, Math.round(2 * Theme.scale))

            Behavior on color {
                CAnim {}
            }
        }

        // Stop indicator at the end of the inactive track.
        Rectangle {
            readonly property real d: Math.max(2, Math.round(4 * Theme.scale))

            visible: inactivePart.width > d * 3 && priv.tickCount === 0
            width: d
            height: d
            radius: d / 2
            x: parent.width - d - (parent.height - d) / 2
            anchors.verticalCenter: parent.verticalCenter
            color: priv.activeColour
        }

        Repeater {
            model: priv.tickCount

            Rectangle {
                id: tick

                required property int index
                readonly property real d: Math.max(2, Math.round(4 * Theme.scale))
                readonly property real cx: priv.handleWidth / 2 + priv.usable * (tick.index / Math.max(1, priv.tickCount - 1))
                readonly property bool onActive: cx < priv.handleX - priv.gap

                visible: Math.abs(cx - (priv.handleX + priv.handleWidth / 2)) > priv.gap + d
                width: d
                height: d
                radius: d / 2
                x: Math.max(d, Math.min(track.width - 2 * d, cx - d / 2))
                anchors.verticalCenter: parent.verticalCenter
                color: tick.onActive ? (root.disabled ? Theme.palette.m3surface : Theme.palette.m3onPrimary) : (root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3onSecondaryContainer)
            }
        }

        // Handle.
        Rectangle {
            id: handle

            // M3: the handle thins from 4 dp to 2 dp while pressed.
            x: priv.handleX + (priv.handleWidth - width) / 2
            anchors.verticalCenter: parent.verticalCenter
            width: mouse.pressed ? Math.max(1, Math.round(priv.handleWidth / 2)) : priv.handleWidth
            height: Math.round(44 * Theme.scale)
            radius: width / 2
            color: priv.activeColour

            Behavior on width {
                Anim {
                    type: Anim.FastSpatial
                }
            }
        }

        // Focus ring around the handle.
        Rectangle {
            x: handle.x - Math.round(3 * Theme.scale)
            y: handle.y - Math.round(3 * Theme.scale)
            width: handle.width + Math.round(6 * Theme.scale)
            height: handle.height + Math.round(6 * Theme.scale)
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1, Math.round(2 * Theme.scale))
            border.color: Theme.palette.m3secondary
            visible: root.activeFocus && !mouse.pressed && !priv.keyboardActive
        }

        // Value label bubble.
        StyledRect {
            id: bubble

            x: Math.round(priv.handleX + priv.handleWidth / 2 - width / 2)
            y: handle.y - height - Math.round(6 * Theme.scale)
            implicitWidth: Math.max(Math.round(48 * Theme.scale), bubbleText.implicitWidth + Theme.padding.medium * 2)
            implicitHeight: bubbleText.implicitHeight + Theme.padding.small * 2
            width: implicitWidth
            height: implicitHeight
            radius: height / 2
            color: Theme.palette.m3inverseSurface
            opacity: priv.bubbleVisible ? 1 : 0
            scale: priv.bubbleVisible ? 1 : 0.6
            transformOrigin: Item.Bottom
            visible: opacity > 0

            StyledText {
                id: bubbleText

                anchors.centerIn: parent
                text: root.valueText
                color: Theme.palette.m3inverseOnSurface
                font.pointSize: Theme.font.size.normal
                font.weight: Font.Medium
            }

            Behavior on opacity {
                Anim {
                    type: Anim.FastEffects
                }
            }

            Behavior on scale {
                Anim {
                    type: Anim.FastSpatial
                }
            }
        }
    }

    MouseArea {
        id: mouse

        function valueAt(px: real): real {
            const p = priv.usable > 0 ? (px - track.x - priv.handleWidth / 2) / priv.usable : 0;
            return root.from + Math.max(0, Math.min(1, p)) * (root.to - root.from);
        }

        anchors.fill: parent
        enabled: !root.disabled
        hoverEnabled: true
        preventStealing: true
        cursorShape: root.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
        onPressed: event => {
            root.forceActiveFocus();
            priv.keyboardActive = false;
            root.setValue(valueAt(event.x));
        }
        onPositionChanged: event => {
            if (pressed)
                root.setValue(valueAt(event.x));
        }
    }
}
