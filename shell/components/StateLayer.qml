// StateLayer — Caelestia hover/press feedback: an 8 % hover tint plus a radial ripple from the press point.
// Put it inside the item it decorates; it fills the parent and picks up the parent's radius (and per-corner radii).
// It IS a MouseArea: use onClicked / onPressed / acceptedButtons / containsMouse / pressed as usual.
//
//   property color color            tint colour (default Theme.palette.m3onSurface; use the "on" colour of the
//                                   container, e.g. m3onPrimary on a primary button)
//   property bool disabled          disables input, hover and ripple (cursor back to arrow)
//   property bool showHoverBackground  default true
//   property bool manualHoverOverride / manualPressOverride   force the hover tint / keep the ripple (keyboard focus)
//   property bool shapeMorph        enlarge the ripple for shape-morphing buttons
//   property real radius, topLeftRadius, topRightRadius, bottomLeftRadius, bottomRightRadius
//   readonly property Item rect     the hover rectangle
//   function press(x, y)            start a ripple programmatically (e.g. on Enter/Space)

import QtQuick
import QtQuick.Shapes
import "../services"

MouseArea {
    id: root

    property bool disabled: false
    property bool showHoverBackground: true
    property bool manualPressOverride: false
    property bool manualHoverOverride: false
    readonly property alias rect: base

    property bool shapeMorph: false
    property real stateOpacity: root.showHoverBackground && !root.disabled && (root.containsMouse || root.manualHoverOverride) ? 0.08 : 0

    property real pressX: width / 2
    property real pressY: height / 2
    property real circleRadius: 0

    property alias color: base.color
    property alias radius: base.radius
    property alias topLeftRadius: base.topLeftRadius
    property alias topRightRadius: base.topRightRadius
    property alias bottomLeftRadius: base.bottomLeftRadius
    property alias bottomRightRadius: base.bottomRightRadius

    readonly property real endRadius: {
        const d1 = distSq(0, 0);
        const d2 = distSq(width, 0);
        const d3 = distSq(0, height);
        const d4 = distSq(width, height);
        return (Math.sqrt(Math.max(d1, d2, d3, d4)) + (shapeMorph ? 24 : 0)) * 1.3;
    }
    property real endRadiusAtPress: 0

    function distSq(x: real, y: real): real {
        return (pressX - x) ** 2 + (pressY - y) ** 2;
    }

    function clampRadius(r: real): real {
        return Math.max(0, Math.min(r, width / 2, height / 2));
    }

    function clamp01(v: real, lo: real, hi: real): real {
        return Math.max(lo, Math.min(hi, isFinite(v) ? v : lo));
    }

    function press(x: real, y: real): void {
        if (root.disabled)
            return;
        pressX = x;
        pressY = y;
        fadeAnim.complete();
        circleRadius = 0;
        circle.opacity = 0.1;
        rippleAnim.restart();
        endRadiusAtPress = endRadius;
    }

    anchors.fill: parent
    enabled: !disabled
    cursorShape: disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
    hoverEnabled: true

    onPressed: event => press(event.x, event.y)

    onPressedChanged: {
        if (!(pressed || manualPressOverride) && !rippleAnim.running && circle.opacity > 0)
            fadeAnim.start();
    }

    onManualPressOverrideChanged: {
        if (!(pressed || manualPressOverride) && circleRadius > endRadiusAtPress * 0.99 && !fadeAnim.running)
            fadeAnim.start();
    }

    onCircleRadiusChanged: {
        if (!(pressed || manualPressOverride) && circleRadius > endRadiusAtPress * 0.99 && !fadeAnim.running)
            fadeAnim.start();
    }

    Anim {
        id: rippleAnim

        alwaysRunToEnd: true
        target: root
        property: "circleRadius"
        to: root.endRadius
        easing.bezierCurve: Theme.anim.curves.standard
        duration: Theme.anim.durations.expressiveSlowEffects * 2
    }

    Anim {
        id: fadeAnim

        target: circle
        property: "opacity"
        to: 0
        type: Anim.SlowEffects
    }

    StyledRect {
        id: base

        anchors.fill: parent
        opacity: root.stateOpacity
        color: Theme.palette.m3onSurface
        // Pick up the radius from the parent when it has one (Rectangle-like parents).
        // qmllint disable missing-property
        radius: root.parent?.radius ?? 0
        topLeftRadius: root.parent?.topLeftRadius ?? radius
        topRightRadius: root.parent?.topRightRadius ?? radius
        bottomLeftRadius: root.parent?.bottomLeftRadius ?? radius
        bottomRightRadius: root.parent?.bottomRightRadius ?? radius
        // qmllint enable missing-property
    }

    Shape {
        id: circle

        anchors.fill: parent
        opacity: 0
        visible: opacity > 0
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeWidth: 0
            strokeColor: "transparent"
            fillColor: base.color
            fillGradient: RadialGradient {
                centerX: root.pressX
                centerY: root.pressY
                centerRadius: Math.max(0.001, root.circleRadius)
                focalX: root.pressX
                focalY: root.pressY

                GradientStop {
                    position: 0
                    color: Qt.alpha(base.color, 1)
                }
                GradientStop {
                    position: root.clamp01(1 - 0.2 * root.endRadius / Math.max(0.001, root.circleRadius), 0.01, 0.99)
                    color: Qt.alpha(base.color, 1)
                }
                GradientStop {
                    position: 1
                    color: Qt.alpha(base.color, root.clamp01((root.circleRadius / Math.max(0.001, root.endRadius) - 0.9) / 0.1, 0, 1))
                }
            }

            startX: root.clampRadius(base.topLeftRadius)
            startY: 0

            PathLine {
                x: root.width - root.clampRadius(base.topRightRadius)
                y: 0
            }
            PathArc {
                relativeX: root.clampRadius(base.topRightRadius)
                relativeY: root.clampRadius(base.topRightRadius)
                radiusX: root.clampRadius(base.topRightRadius)
                radiusY: root.clampRadius(base.topRightRadius)
            }
            PathLine {
                x: root.width
                y: root.height - root.clampRadius(base.bottomRightRadius)
            }
            PathArc {
                relativeX: -root.clampRadius(base.bottomRightRadius)
                relativeY: root.clampRadius(base.bottomRightRadius)
                radiusX: root.clampRadius(base.bottomRightRadius)
                radiusY: root.clampRadius(base.bottomRightRadius)
            }
            PathLine {
                x: root.clampRadius(base.bottomLeftRadius)
                y: root.height
            }
            PathArc {
                relativeX: -root.clampRadius(base.bottomLeftRadius)
                relativeY: -root.clampRadius(base.bottomLeftRadius)
                radiusX: root.clampRadius(base.bottomLeftRadius)
                radiusY: root.clampRadius(base.bottomLeftRadius)
            }
            PathLine {
                x: 0
                y: root.clampRadius(base.topLeftRadius)
            }
            PathArc {
                x: root.clampRadius(base.topLeftRadius)
                y: 0
                radiusX: root.clampRadius(base.topLeftRadius)
                radiusY: root.clampRadius(base.topLeftRadius)
            }
        }
    }

    Behavior on stateOpacity {
        Anim {
            type: Anim.DefaultEffects
        }
    }
}
