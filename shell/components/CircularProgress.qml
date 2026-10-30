// CircularProgress — M3 circular progress indicator (Shape + PathAngleArc, CurveRenderer).
// Determinate: an arc from 12 o'clock clockwise plus the remaining track separated by small gaps, animated value.
// Indeterminate: the arc grows and shrinks while the whole indicator rotates (M3 timing, Theme curves).
//
//   property real value            0..1 (ignored while indeterminate)
//   property bool indeterminate
//   property bool showTrack: true
//   property real strokeWidth      default 4 × scale
//   property real implicitSize     default 40 × scale
//   property color fgColour        default Theme.palette.m3primary
//   property color bgColour        default Theme.palette.m3secondaryContainer
//   property bool running: visible animations run only while visible

import QtQuick
import QtQuick.Shapes
import "../services"

Item {
    id: root

    property real value: 0
    property bool indeterminate: false
    property bool showTrack: true
    property real strokeWidth: Math.max(2, Math.round(4 * Theme.scale))
    property real implicitSize: Math.round(40 * Theme.scale)
    property color fgColour: Theme.palette.m3primary
    property color bgColour: Theme.palette.m3secondaryContainer
    property bool running: visible

    readonly property real size: Math.min(width, height)
    readonly property real arcRadius: Math.max(0, (size - strokeWidth) / 2)
    readonly property real clampedValue: Math.max(0, Math.min(1, isNaN(value) ? 0 : value))
    property real animatedValue: clampedValue
    // Gap between the indicator and the track, in degrees (≈ 4 px plus the round caps).
    readonly property real gapAngle: arcRadius > 0 ? ((Math.round(4 * Theme.scale) + strokeWidth) / arcRadius) * (180 / Math.PI) : 0

    // Indeterminate state: head/tail in degrees on top of a constant rotation.
    property real headT: 0
    property real tailT: 0
    property real cycleBase: 0
    readonly property real minSweep: 10
    readonly property real headAngle: cycleBase + headT * 260
    readonly property real tailAngle: cycleBase + tailT * 260

    implicitWidth: implicitSize
    implicitHeight: implicitSize

    Behavior on animatedValue {
        enabled: !root.indeterminate

        Anim {
            type: Anim.StandardLarge
        }
    }

    Shape {
        id: shape

        width: root.size
        height: root.size
        anchors.centerIn: parent
        preferredRendererType: Shape.CurveRenderer

        // Remaining track (determinate only).
        ShapePath {
            fillColor: "transparent"
            strokeColor: root.showTrack && !root.indeterminate && trackArc.sweepAngle > 0.5 ? root.bgColour : "transparent"
            strokeWidth: root.strokeWidth
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                id: trackArc

                centerX: shape.width / 2
                centerY: shape.height / 2
                radiusX: root.arcRadius
                radiusY: root.arcRadius
                startAngle: -90 + root.animatedValue * 360 + (root.animatedValue > 0 ? root.gapAngle : 0)
                sweepAngle: Math.max(0, 360 * (1 - root.animatedValue) - (root.animatedValue > 0 ? root.gapAngle * 2 : 0))
            }
        }

        // Indicator.
        ShapePath {
            fillColor: "transparent"
            strokeColor: (root.indeterminate || root.animatedValue > 0.001) ? root.fgColour : "transparent"
            strokeWidth: root.strokeWidth
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: shape.width / 2
                centerY: shape.height / 2
                radiusX: root.arcRadius
                radiusY: root.arcRadius
                startAngle: root.indeterminate ? -90 + root.tailAngle : -90
                sweepAngle: root.indeterminate ? Math.max(root.minSweep, root.headAngle - root.tailAngle + root.minSweep) : Math.max(0.1, root.animatedValue * 360)
            }
        }

        RotationAnimator on rotation {
            running: root.indeterminate && root.running
            from: 0
            to: 360
            duration: 1568
            loops: Animation.Infinite
        }
    }

    // M3 indeterminate arc: expand (head moves 260°) then collapse (tail catches up), repeat.
    SequentialAnimation {
        running: root.indeterminate && root.running
        loops: Animation.Infinite

        NumberAnimation {
            target: root
            property: "headT"
            from: 0
            to: 1
            duration: 667
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Theme.anim.curves.standard
        }
        NumberAnimation {
            target: root
            property: "tailT"
            from: 0
            to: 1
            duration: 667
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Theme.anim.curves.standard
        }
        ScriptAction {
            script: {
                root.cycleBase = (root.cycleBase + 260) % 360;
                root.headT = 0;
                root.tailT = 0;
            }
        }
    }

    onIndeterminateChanged: {
        if (!indeterminate) {
            shape.rotation = 0;
            headT = 0;
            tailT = 0;
            cycleBase = 0;
        }
    }
}
