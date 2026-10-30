// LinearProgress — M3 linear progress indicator.
// Determinate: rounded active bar, gap, remaining track and the stop dot at the end; value changes animate.
// Indeterminate: the two M3 bars (line 1 / line 2 head and tail timings over 1800 ms) sweeping across with gaps.
//
//   property real value            0..1 (ignored while indeterminate)
//   property bool indeterminate
//   property real thickness        default 4 × scale (implicitHeight)
//   property bool showStop: true   stop indicator dot at the end of the track (determinate)
//   property color fgColour        default Theme.palette.m3primary
//   property color bgColour        default Theme.palette.m3secondaryContainer
//   property bool running: visible

import QtQuick
import "../services"

Item {
    id: root

    property real value: 0
    property bool indeterminate: false
    property real thickness: Math.max(2, Math.round(4 * Theme.scale))
    property bool showStop: true
    property color fgColour: Theme.palette.m3primary
    property color bgColour: Theme.palette.m3secondaryContainer
    property bool running: visible

    readonly property real clampedValue: Math.max(0, Math.min(1, isNaN(value) ? 0 : value))
    property real animatedValue: clampedValue
    readonly property real gap: Math.max(2, Math.round(4 * Theme.scale))

    // Indeterminate segment fractions (0..1 of the width).
    property real head1: 0
    property real tail1: 0
    property real head2: 0
    property real tail2: 0

    implicitWidth: Math.round(200 * Theme.scale)
    implicitHeight: thickness

    Behavior on animatedValue {
        enabled: !root.indeterminate

        Anim {
            type: Anim.StandardLarge
        }
    }

    // Inline components do not share the id scope of this file: the bar thickness is passed in explicitly.
    component Segment: Rectangle {
        property real from: 0
        property real until: 0
        property real thickness: 4

        x: from
        width: Math.max(0, until - from)
        height: thickness
        anchors.verticalCenter: parent.verticalCenter
        radius: height / 2
        visible: width > 0.5
    }

    // ---------------------------------------------------------------- determinate
    Item {
        anchors.fill: parent
        visible: !root.indeterminate

        Segment {
            thickness: root.thickness
            from: 0
            until: root.animatedValue > 0 ? Math.max(root.thickness, root.width * root.animatedValue) : 0
            color: root.fgColour
        }

        Segment {
            thickness: root.thickness
            from: root.animatedValue > 0 ? Math.max(root.thickness, root.width * root.animatedValue) + root.gap : 0
            until: root.width
            color: root.bgColour
        }

        Rectangle {
            visible: root.showStop && root.width * (1 - root.animatedValue) > root.thickness * 3
            width: root.thickness
            height: root.thickness
            radius: width / 2
            x: root.width - width
            anchors.verticalCenter: parent.verticalCenter
            color: root.fgColour
        }
    }

    // ---------------------------------------------------------------- indeterminate
    Item {
        id: indet

        // Visible bars sorted left → right, and the track pieces around them (with gaps), in pixels.
        readonly property var pieces: {
            const w = root.width;
            const g = root.gap;
            const bars = [];
            if (root.head1 - root.tail1 > 0.0005)
                bars.push([root.tail1 * w, root.head1 * w]);
            if (root.head2 - root.tail2 > 0.0005)
                bars.push([root.tail2 * w, root.head2 * w]);
            bars.sort((a, b) => a[0] - b[0]);
            const track = [];
            let cursor = 0;
            for (const b of bars) {
                if (b[0] - g - cursor > 0.5)
                    track.push([cursor, b[0] - g]);
                cursor = b[1] + g;
            }
            if (w - cursor > 0.5)
                track.push([cursor, w]);
            return {
                bars: bars,
                track: track
            };
        }

        anchors.fill: parent
        visible: root.indeterminate

        Segment {
            thickness: root.thickness
            from: indet.pieces.track[0]?.[0] ?? 0
            until: indet.pieces.track[0]?.[1] ?? 0
            color: root.bgColour
        }
        Segment {
            thickness: root.thickness
            from: indet.pieces.track[1]?.[0] ?? 0
            until: indet.pieces.track[1]?.[1] ?? 0
            color: root.bgColour
        }
        Segment {
            thickness: root.thickness
            from: indet.pieces.track[2]?.[0] ?? 0
            until: indet.pieces.track[2]?.[1] ?? 0
            color: root.bgColour
        }
        Segment {
            thickness: root.thickness
            from: indet.pieces.bars[0]?.[0] ?? 0
            until: indet.pieces.bars[0]?.[1] ?? 0
            color: root.fgColour
        }
        Segment {
            thickness: root.thickness
            from: indet.pieces.bars[1]?.[0] ?? 0
            until: indet.pieces.bars[1]?.[1] ?? 0
            color: root.fgColour
        }
    }

    ParallelAnimation {
        running: root.indeterminate && root.running
        loops: Animation.Infinite

        // Line 1 head: 0 → 750 ms
        SequentialAnimation {
            NumberAnimation {
                target: root
                property: "head1"
                from: 0
                to: 1
                duration: 750
                easing.type: Easing.BezierSpline
                easing.bezierCurve: [0.2, 0, 0.8, 1, 1, 1]
            }
            PauseAnimation {
                duration: 1050
            }
        }
        // Line 1 tail: 333 → 1183 ms
        SequentialAnimation {
            PropertyAction {
                target: root
                property: "tail1"
                value: 0
            }
            PauseAnimation {
                duration: 333
            }
            NumberAnimation {
                target: root
                property: "tail1"
                from: 0
                to: 1
                duration: 850
                easing.type: Easing.BezierSpline
                easing.bezierCurve: [0.4, 0, 1, 1, 1, 1]
            }
            PauseAnimation {
                duration: 617
            }
        }
        // Line 2 head: 1000 → 1567 ms
        SequentialAnimation {
            PropertyAction {
                target: root
                property: "head2"
                value: 0
            }
            PauseAnimation {
                duration: 1000
            }
            NumberAnimation {
                target: root
                property: "head2"
                from: 0
                to: 1
                duration: 567
                easing.type: Easing.BezierSpline
                easing.bezierCurve: [0, 0, 0.65, 1, 1, 1]
            }
            PauseAnimation {
                duration: 233
            }
        }
        // Line 2 tail: 1267 → 1800 ms
        SequentialAnimation {
            PropertyAction {
                target: root
                property: "tail2"
                value: 0
            }
            PauseAnimation {
                duration: 1267
            }
            NumberAnimation {
                target: root
                property: "tail2"
                from: 0
                to: 1
                duration: 533
                easing.type: Easing.BezierSpline
                easing.bezierCurve: [0.1, 0, 0.45, 1, 1, 1]
            }
        }
    }
}
