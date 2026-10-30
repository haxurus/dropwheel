pragma ComponentBehavior: Bound

// Segment — one wedge of the Dropwheel ring (SPEC §2.3).
//
// The wedge is a donut sector drawn with QtQuick.Shapes: outer PathAngleArc, straight edge, inner reverse
// PathAngleArc, with every corner rounded by a tangent PathArc fillet. Neighbouring wedges are separated by a gap of
// constant width (the straight edges are offset from the radial lines by gap / 2). The content sits upright at mid
// radius: a Material Symbols icon above an uppercase label that wraps to two lines before it shrinks, or the big format
// text (node.text, e.g. "PNG"). An unavailable action is dimmed and carries a "download" badge, on either content.
// The item covers the whole wheel area (same size as the ring); hit-testing is done by the Wheel (atan2 + radius),
// so a Segment never takes input itself.
//
//   required property var node        menu node (SPEC §5.3): {type, id, label, icon, text, available, danger, hint, …}
//   required property int index       position in the ring: 0 is centred at 12 o'clock, then clockwise
//   property int count                number of segments in the ring (1..8)
//   property real centerX, centerY    wheel centre in item coordinates (default: the item centre)
//   property real innerRadius, outerRadius, gap, cornerRadius, growBy   geometry in px (defaults: SPEC §2.3 × scale)
//   property real fit                 shrink factor for what is sized from the Theme tokens (the wheel's capped scale)
//   property bool highlighted         hovered / keyboard highlight: grows outward by growBy, primary (error) fill
//   property bool flash               paints the wedge in the error colour (refused drop, missing tool)
//   property real springProgress      0..1 spring-loading arc along the outer edge (groups and Back in drag mode)
//   property bool showNumber          shows the 1–8 keyboard shortcut (click mode while Alt is held)
//   readonly property bool isGroup, available, danger
//   readonly property Item badge, labelItem, bigTextItem   the content items, for layout checks
//   readonly property real midAngle   centre angle in degrees (0 = 3 o'clock, clockwise, PathAngleArc convention)
//   readonly property var geometry    the current path points (see geometryFor)
//   function geometryFor(cx, cy, r0, r1, startDeg, endDeg, gap, rc) → {valid, points…}  pure helper (tests use it)

import QtQuick
import QtQuick.Shapes
import "../services"
import "../components"

Item {
    id: root

    required property var node
    required property int index

    property int count: 1
    property real centerX: width / 2
    property real centerY: height / 2
    property real innerRadius: 52 * Theme.scale
    property real outerRadius: 132 * Theme.scale
    property real gap: 4 * Theme.scale
    property real cornerRadius: 10 * Theme.scale
    property real growBy: 4 * Theme.scale
    property real fit: 1
    property bool highlighted: false
    property bool flash: false
    property real springProgress: 0
    property bool showNumber: false

    readonly property real k: Theme.scale * root.fit
    readonly property Item badge: installBadge
    readonly property Item labelItem: label
    readonly property Item bigTextItem: bigLabel
    readonly property bool isGroup: root.node?.type === "group"
    readonly property bool available: root.node?.available !== false
    readonly property bool danger: root.node?.danger === true
    readonly property string bigText: (typeof root.node?.text === "string") ? root.node.text : ""
    readonly property string iconName: {
        const icon = root.node?.icon;
        if (typeof icon === "string" && icon !== "")
            return icon;
        return root.isGroup ? "more_horiz" : "bolt";
    }
    readonly property string labelText: String(root.node?.label ?? root.node?.id ?? "")
    readonly property real step: 360 / Math.max(1, root.count)
    readonly property real midAngle: -90 + root.index * root.step
    readonly property real rad: Math.PI / 180

    // Hover growth (outward only), animated with Caelestia's fast spatial curve.
    property real grow: root.highlighted ? root.growBy : 0
    readonly property real r0: root.innerRadius
    readonly property real r1: root.outerRadius + root.grow
    readonly property var geometry: root.count > 1 ? root.geometryFor(root.centerX, root.centerY, root.r0, root.r1, root.midAngle - root.step / 2, root.midAngle + root.step / 2, root.gap, root.cornerRadius) : root.emptyGeometry()

    readonly property color fillColour: {
        if (root.flash)
            return Theme.palette.m3error;
        if (root.highlighted) {
            if (!root.available)
                return Theme.palette.m3surfaceContainerHighest;
            return root.danger ? Theme.palette.m3error : Theme.palette.m3primary;
        }
        return Theme.tPalette.m3surfaceContainerHigh;
    }
    readonly property color onColour: {
        if (root.flash)
            return Theme.palette.m3onError;
        if (root.highlighted) {
            if (!root.available)
                return Theme.palette.m3onSurface;
            return root.danger ? Theme.palette.m3onError : Theme.palette.m3onPrimary;
        }
        return root.danger ? Theme.palette.m3error : Theme.palette.m3onSurface;
    }
    property color animatedFill: root.fillColour

    function emptyGeometry(): var {
        const p = {
            x: root.centerX,
            y: root.centerY
        };
        return {
            valid: false,
            rcOut: 0.5,
            rcIn: 0.5,
            sOutLine: p,
            sOutArc: p,
            eOutArc: p,
            eOutLine: p,
            eInLine: p,
            eInArc: p,
            sInArc: p,
            sInLine: p,
            outStart: 0,
            outSweep: 0,
            inStart: 0,
            inSweep: 0
        };
    }

    // Path points of a rounded donut wedge between startDeg and endDeg (degrees, 0 = 3 o'clock, clockwise).
    // The straight edges are offset by gap / 2 from the radial lines; each corner gets a fillet of radius rc that is
    // tangent to the edge and to the arc (rc shrinks automatically when the wedge is too narrow for it).
    function geometryFor(cx: real, cy: real, r0: real, r1: real, startDeg: real, endDeg: real, gap: real, rc: real): var {
        const rad = Math.PI / 180;
        const h = Math.max(0, gap) / 2;
        const ts = startDeg * rad;
        const te = endDeg * rad;
        const half = Math.max(0, (te - ts) / 2);
        const sinHalf = Math.sin(Math.min(half, Math.PI / 2));

        // Fillets must not overlap: across the ring thickness, on the inner arc and on the outer arc.
        let rcMax = Math.max(0, (r1 - r0) / 2 - 1);
        let rcInMax = rcMax;
        let rcOutMax = rcMax;
        if (sinHalf < 0.999) {
            rcInMax = Math.min(rcInMax, Math.max(0, 0.9 * (r0 * sinHalf - h) / (1 - sinHalf)));
            rcOutMax = Math.min(rcOutMax, Math.max(0, 0.9 * (r1 * sinHalf - h) / (1 + sinHalf)));
        }
        const rcIn = Math.max(0.5, Math.min(rc, rcInMax));
        const rcOut = Math.max(0.5, Math.min(rc, rcOutMax));

        const us = {
            x: Math.cos(ts),
            y: Math.sin(ts)
        };
        const ue = {
            x: Math.cos(te),
            y: Math.sin(te)
        };
        // Interior normals: the start edge looks towards increasing angles, the end edge towards decreasing ones.
        const ns = {
            x: -Math.sin(ts),
            y: Math.cos(ts)
        };
        const ne = {
            x: Math.sin(te),
            y: -Math.cos(te)
        };

        const fillet = (u, n, radius, r, outer) => {
            const d = h + r;
            const cr = outer ? radius - r : radius + r;
            const a = Math.sqrt(Math.max(0, cr * cr - d * d));
            const c = {
                x: a * u.x + d * n.x,
                y: a * u.y + d * n.y
            };
            const k = cr > 0 ? radius / cr : 0;
            return {
                line: {
                    x: cx + a * u.x + h * n.x,
                    y: cy + a * u.y + h * n.y
                },
                arc: {
                    x: cx + c.x * k,
                    y: cy + c.y * k
                },
                angle: Math.atan2(c.y, c.x) / rad
            };
        };

        const sOut = fillet(us, ns, r1, rcOut, true);
        const eOut = fillet(ue, ne, r1, rcOut, true);
        const sIn = fillet(us, ns, r0, rcIn, false);
        const eIn = fillet(ue, ne, r0, rcIn, false);

        let outSweep = eOut.angle - sOut.angle;
        while (outSweep <= 0)
            outSweep += 360;
        while (outSweep > 360)
            outSweep -= 360;
        let inSweep = sIn.angle - eIn.angle;
        while (inSweep >= 0)
            inSweep -= 360;
        while (inSweep < -360)
            inSweep += 360;

        const span = endDeg - startDeg;
        const valid = r1 > r0 && span > 0 && outSweep < span && -inSweep < span && isFinite(outSweep) && isFinite(inSweep);
        return {
            valid: valid,
            rcOut: rcOut,
            rcIn: rcIn,
            sOutLine: sOut.line,
            sOutArc: sOut.arc,
            eOutArc: eOut.arc,
            eOutLine: eOut.line,
            eInLine: eIn.line,
            eInArc: eIn.arc,
            sInArc: sIn.arc,
            sInLine: sIn.line,
            outStart: sOut.angle,
            outSweep: outSweep,
            inStart: eIn.angle,
            inSweep: inSweep
        };
    }

    Accessible.role: Accessible.Button
    Accessible.name: root.labelText + (root.available ? "" : " — " + I18n.t("ui.unavailable"))
    Accessible.description: String(root.node?.description ?? root.node?.hint ?? "")

    Behavior on grow {
        Anim {
            type: Anim.FastSpatial
        }
    }

    Behavior on animatedFill {
        CAnim {}
    }

    // Wedge (two or more segments).
    Shape {
        id: wedge

        anchors.fill: parent
        visible: root.count > 1 && root.geometry.valid
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            fillColor: root.animatedFill
            strokeColor: "transparent"
            strokeWidth: 0
            startX: root.geometry.sOutLine.x
            startY: root.geometry.sOutLine.y

            PathArc {
                x: root.geometry.sOutArc.x
                y: root.geometry.sOutArc.y
                radiusX: root.geometry.rcOut
                radiusY: root.geometry.rcOut
                direction: PathArc.Clockwise
            }
            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: root.r1
                radiusY: root.r1
                startAngle: root.geometry.outStart
                sweepAngle: root.geometry.outSweep
                moveToStart: false
            }
            PathArc {
                x: root.geometry.eOutLine.x
                y: root.geometry.eOutLine.y
                radiusX: root.geometry.rcOut
                radiusY: root.geometry.rcOut
                direction: PathArc.Clockwise
            }
            PathLine {
                x: root.geometry.eInLine.x
                y: root.geometry.eInLine.y
            }
            PathArc {
                x: root.geometry.eInArc.x
                y: root.geometry.eInArc.y
                radiusX: root.geometry.rcIn
                radiusY: root.geometry.rcIn
                direction: PathArc.Clockwise
            }
            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: root.r0
                radiusY: root.r0
                startAngle: root.geometry.inStart
                sweepAngle: root.geometry.inSweep
                moveToStart: false
            }
            PathArc {
                x: root.geometry.sInLine.x
                y: root.geometry.sInLine.y
                radiusX: root.geometry.rcIn
                radiusY: root.geometry.rcIn
                direction: PathArc.Clockwise
            }
            PathLine {
                x: root.geometry.sOutLine.x
                y: root.geometry.sOutLine.y
            }
        }

        // Spring-loading affordance: an arc that fills along the outer edge while the hover timer runs.
        ShapePath {
            fillColor: "transparent"
            strokeColor: root.springProgress > 0.001 ? Qt.alpha(root.onColour, 0.85) : "transparent"
            strokeWidth: Math.max(2, 3 * root.k)
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: Math.max(1, root.r1 - 7 * root.k)
                radiusY: Math.max(1, root.r1 - 7 * root.k)
                startAngle: root.geometry.outStart + 3
                sweepAngle: Math.max(0.1, (root.geometry.outSweep - 6) * Math.max(0, Math.min(1, root.springProgress)))
            }
        }
    }

    // Full ring (a single segment).
    Shape {
        anchors.fill: parent
        visible: root.count <= 1
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            fillColor: root.animatedFill
            strokeColor: "transparent"
            strokeWidth: 0
            fillRule: ShapePath.OddEvenFill

            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: root.r1
                radiusY: root.r1
                startAngle: 0
                sweepAngle: 360
            }
            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: root.r0
                radiusY: root.r0
                startAngle: 0
                sweepAngle: 360
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: root.springProgress > 0.001 ? Qt.alpha(root.onColour, 0.85) : "transparent"
            strokeWidth: Math.max(2, 3 * root.k)
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.centerX
                centerY: root.centerY
                radiusX: Math.max(1, root.r1 - 7 * root.k)
                radiusY: Math.max(1, root.r1 - 7 * root.k)
                startAngle: -90
                sweepAngle: Math.max(0.1, 359 * Math.max(0, Math.min(1, root.springProgress)))
            }
        }
    }

    // Group marker: a short tangential bar near the outer edge ("this opens a sub-ring").
    Rectangle {
        readonly property real markR: root.r1 - 6 * root.k

        visible: root.isGroup && root.springProgress <= 0.001
        width: Math.round(14 * root.k)
        height: Math.max(2, Math.round(3 * root.k))
        radius: height / 2
        color: Qt.alpha(root.onColour, root.available ? 0.55 : 0.3)
        x: root.centerX + Math.cos(root.midAngle * root.rad) * markR - width / 2
        y: root.centerY + Math.sin(root.midAngle * root.rad) * markR - height / 2
        rotation: root.midAngle + 90
        antialiasing: true
    }

    // Keyboard shortcut number (1–8) near the inner edge.
    StyledRect {
        readonly property real numR: root.r0 + 11 * root.k

        visible: root.showNumber && root.index < 8
        width: Math.round(16 * root.k)
        height: width
        radius: width / 2
        x: root.centerX + Math.cos(root.midAngle * root.rad) * numR - width / 2
        y: root.centerY + Math.sin(root.midAngle * root.rad) * numR - height / 2
        color: Theme.palette.m3secondaryContainer

        StyledText {
            anchors.centerIn: parent
            text: String(root.index + 1)
            color: Theme.palette.m3onSecondaryContainer
            font.pointSize: Math.max(6, Theme.font.size.small * 0.8 * root.fit)
            font.weight: Font.DemiBold
        }
    }

    // Upright content at mid radius.
    Item {
        id: content

        readonly property real rm: (root.innerRadius + root.outerRadius) / 2 + root.grow / 2
        // Measured without the hover growth: the label must not reflow while the highlight animates.
        readonly property real chord: root.count > 1 ? (root.innerRadius + root.outerRadius) * Math.sin(Math.min(Math.PI / 2, Math.PI / root.count)) - root.gap - 4 * root.k : (root.outerRadius - root.innerRadius) * 1.6

        width: Math.max(24 * root.k, Math.min(chord, (root.outerRadius - root.innerRadius) * 1.1))
        // The label box has room for two lines; only the lines in use count for centring the content.
        height: root.bigText !== "" ? bigLabel.height : iconBox.height + column.spacing + label.contentHeight
        x: root.centerX + Math.cos(root.midAngle * root.rad) * rm - width / 2
        y: root.centerY + Math.sin(root.midAngle * root.rad) * rm - height / 2

        Column {
            id: column

            width: parent.width
            spacing: Math.round(2 * root.k)
            opacity: root.available ? 1 : 0.38

            Item {
                id: iconBox

                visible: root.bigText === ""
                anchors.horizontalCenter: parent.horizontalCenter
                width: icon.implicitWidth
                height: icon.implicitHeight

                MaterialIcon {
                    id: icon

                    anchors.centerIn: parent
                    text: root.iconName
                    color: root.onColour
                    // 22 px at the 96 dpi logical density (16.5 pt), following Caelestia's font scale.
                    size: Theme.font.iconSize.large * (16.5 / 24) * root.fit
                    fill: root.highlighted ? 1 : 0

                    Behavior on fill {
                        Anim {
                            type: Anim.DefaultEffects
                        }
                    }
                }
            }

            StyledText {
                id: bigLabel

                visible: root.bigText !== ""
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.bigText
                color: root.onColour
                font.pointSize: Theme.font.size.larger * root.fit
                font.weight: Font.DemiBold
                fontSizeMode: Text.HorizontalFit
                minimumPointSize: Math.max(6, Theme.font.size.small * 0.7 * root.fit)
                elide: Text.ElideRight
            }

            // Text.Fit shrinks the text only when it overflows the box, so the box height is what limits it to two lines
            // (maximumLineCount would truncate instead of shrinking). The elide is the last resort, below the minimum.
            StyledText {
                id: label

                visible: root.bigText === ""
                width: parent.width
                height: Math.ceil(2 * lineHeight * labelMetrics.height) + 1
                horizontalAlignment: Text.AlignHCenter
                text: root.labelText
                color: root.onColour
                font.pointSize: labelMetrics.font.pointSize
                font.weight: labelMetrics.font.weight
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 0.3 * root.k
                lineHeight: 0.9
                wrapMode: Text.WordWrap
                fontSizeMode: Text.Fit
                minimumPointSize: Math.max(5, Theme.font.size.small * (7.5 / 11) * root.fit)
                elide: Text.ElideRight
            }
        }

        // "Install first" badge on unavailable actions, at the top right of the icon or of the big text. It stays
        // solid over the dimmed content: it is the part that says what to do.
        StyledRect {
            id: installBadge

            readonly property real contentW: root.bigText !== "" ? Math.min(bigLabel.contentWidth, content.width) : iconBox.width
            readonly property real contentTop: root.bigText !== "" ? (bigLabel.height - bigLabel.contentHeight) / 2 : 0

            visible: !root.available && !root.isGroup
            width: Math.round(14 * root.k)
            height: width
            radius: width / 2
            x: (content.width + contentW) / 2 - width * 0.45
            y: contentTop - height * 0.35
            color: Theme.palette.m3tertiary

            MaterialIcon {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: 0.5
                text: "download"
                color: Theme.palette.m3onTertiary
                size: Math.max(5, Theme.font.iconSize.small * 0.55 * root.fit)
                fill: 1
            }
        }

        FontMetrics {
            id: labelMetrics

            font.family: Theme.font.family.sans
            font.pointSize: Theme.font.size.small * (9.5 / 11) * root.fit
            font.weight: Font.DemiBold
        }
    }
}
