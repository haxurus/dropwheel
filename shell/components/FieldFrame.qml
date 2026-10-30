// FieldFrame — M3 outlined field frame shared by M3TextField and M3Dropdown (usable for custom fields too):
// rounded outline with a gap for the floating label, label that floats onto the border when focused/populated,
// supporting / error text below. Put the actual content inside `box` coordinates (frame.box.x/y/width/height).
//
//   property string label            floating label ("" = none)
//   property bool focused, populated, hovered, error, disabled
//   property string supportingText   helper text below the box (replaced by errorText when error is true)
//   property string errorText
//   property real boxHeight          height of the outlined box (default 48 × scale)
//   property real radius             outline radius (default Theme.rounding.small)
//   property real horizontalPadding  default Theme.padding.large
//   property real labelLeft          x of the resting label (after a leading icon), default horizontalPadding
//   property bool labelAtTop         keep the resting label at the top instead of centred (multi-line fields)
//   readonly property Item box       the outlined area
//   readonly property color outlineColour, labelColour

import QtQuick
import QtQuick.Shapes
import "../services"

Item {
    id: root

    property string label: ""
    property bool focused: false
    property bool populated: false
    property bool hovered: false
    property bool error: false
    property bool disabled: false
    property string supportingText: ""
    property string errorText: ""
    property real boxHeight: Math.round(48 * Theme.scale)
    property real radius: Theme.rounding.small
    property real horizontalPadding: Theme.padding.large
    property real labelLeft: horizontalPadding
    property bool labelAtTop: false

    readonly property alias box: boxItem
    readonly property string effectiveSupportingText: root.error && root.errorText !== "" ? root.errorText : root.supportingText
    readonly property color outlineColour: {
        if (root.disabled)
            return Qt.alpha(Theme.palette.m3onSurface, 0.12);
        if (root.error)
            return Theme.palette.m3error;
        if (root.focused)
            return Theme.palette.m3primary;
        if (root.hovered)
            return Theme.palette.m3onSurface;
        return Theme.palette.m3outline;
    }
    readonly property color labelColour: {
        if (root.disabled)
            return Qt.alpha(Theme.palette.m3onSurface, 0.38);
        if (root.error)
            return Theme.palette.m3error;
        if (root.focused)
            return Theme.palette.m3primary;
        return Theme.palette.m3onSurfaceVariant;
    }

    implicitWidth: Math.round(240 * Theme.scale)
    implicitHeight: boxItem.height + (supporting.visible ? supporting.implicitHeight + Theme.spacing.extraSmall : 0)

    Item {
        id: boxItem

        width: root.width
        height: root.boxHeight

        Shape {
            id: outline

            readonly property real inset: path.strokeWidth / 2
            readonly property real r: Math.max(0, Math.min(root.radius, width / 2 - inset, height / 2 - inset))
            readonly property bool floated: labelText.floated && root.label !== ""
            readonly property real gapScale: floated ? 1 : 0
            readonly property real gapLeft: root.horizontalPadding - Theme.spacing.extraSmall
            readonly property real gapWidth: labelText.implicitWidth * labelText.smallScale + Theme.spacing.extraSmall * 2
            property real animatedGap: gapScale

            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer

            ShapePath {
                id: path

                readonly property real gl: Math.max(outline.inset + outline.r, outline.gapLeft + outline.gapWidth * (1 - outline.animatedGap) / 2)
                readonly property real gr: Math.min(outline.width - outline.inset - outline.r, outline.gapLeft + outline.gapWidth - outline.gapWidth * (1 - outline.animatedGap) / 2)

                strokeWidth: root.focused && !root.disabled ? Math.max(2, Math.round(2 * Theme.scale)) : Math.max(1, Math.round(Theme.scale))
                strokeColor: root.outlineColour
                fillColor: "transparent"
                capStyle: ShapePath.FlatCap
                joinStyle: ShapePath.RoundJoin

                startX: Math.max(path.gl, path.gr)
                startY: outline.inset

                PathLine {
                    x: outline.width - outline.inset - outline.r
                    y: outline.inset
                }
                PathArc {
                    x: outline.width - outline.inset
                    y: outline.inset + outline.r
                    radiusX: outline.r
                    radiusY: outline.r
                }
                PathLine {
                    x: outline.width - outline.inset
                    y: outline.height - outline.inset - outline.r
                }
                PathArc {
                    x: outline.width - outline.inset - outline.r
                    y: outline.height - outline.inset
                    radiusX: outline.r
                    radiusY: outline.r
                }
                PathLine {
                    x: outline.inset + outline.r
                    y: outline.height - outline.inset
                }
                PathArc {
                    x: outline.inset
                    y: outline.height - outline.inset - outline.r
                    radiusX: outline.r
                    radiusY: outline.r
                }
                PathLine {
                    x: outline.inset
                    y: outline.inset + outline.r
                }
                PathArc {
                    x: outline.inset + outline.r
                    y: outline.inset
                    radiusX: outline.r
                    radiusY: outline.r
                }
                PathLine {
                    x: Math.min(path.gl, path.gr)
                    y: outline.inset
                }

                Behavior on strokeColor {
                    CAnim {}
                }
            }

            Behavior on animatedGap {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
        }

        StyledText {
            id: labelText

            readonly property bool floated: root.focused || root.populated
            readonly property real smallScale: Theme.font.size.small / Theme.font.size.normal
            readonly property real restY: root.labelAtTop ? Math.round(Theme.padding.medium + Math.round(2 * Theme.scale)) : Math.round((boxItem.height - height) / 2)

            visible: root.label !== ""
            text: root.label
            color: root.labelColour
            font.pointSize: Theme.font.size.normal
            elide: Text.ElideRight
            width: Math.min(implicitWidth, Math.max(0, boxItem.width - root.labelLeft - root.horizontalPadding))
            transformOrigin: Item.Left
            x: floated ? root.horizontalPadding : root.labelLeft
            y: floated ? Math.round(-height / 2) : restY
            scale: floated ? smallScale : 1

            Behavior on x {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
            Behavior on y {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
            Behavior on scale {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
        }
    }

    StyledText {
        id: supporting

        anchors.top: boxItem.bottom
        anchors.topMargin: Theme.spacing.extraSmall
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: root.horizontalPadding
        anchors.rightMargin: root.horizontalPadding
        visible: text !== ""
        text: root.effectiveSupportingText
        color: root.error ? Theme.palette.m3error : Theme.palette.m3onSurfaceVariant
        font.pointSize: Theme.font.size.small
        wrapMode: Text.Wrap
    }
}
