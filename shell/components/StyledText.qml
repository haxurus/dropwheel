pragma ComponentBehavior: Bound

// StyledText — Text with the Theme sans font (GoogleSansFlex/Rubik), on-surface colour and animated colour.
//
//   property bool animate   fade out → swap → fade in when `text` changes (Caelestia behaviour). Default false.
//   Defaults: font.family Theme.font.family.sans, font.pointSize Theme.font.size.smaller (12 pt × scale),
//   font.weight Font.Normal, font.variableAxes Theme.font.axes, textFormat PlainText, renderType QtRendering
//   (scales cleanly inside the wheel's open animation). Override any of them per instance.

import QtQuick
import "../services"

Text {
    id: root

    property bool animate: false

    renderType: Text.QtRendering
    textFormat: Text.PlainText
    color: Theme.palette.m3onSurface
    font.family: Theme.font.family.sans
    font.pointSize: Theme.font.size.smaller
    font.weight: Font.Normal
    font.variableAxes: Theme.font.axes

    Behavior on color {
        CAnim {}
    }

    Behavior on text {
        enabled: root.animate

        SequentialAnimation {
            Anim {
                target: root
                property: "opacity"
                to: 0
                type: Anim.FastEffects
            }
            PropertyAction {}
            Anim {
                target: root
                property: "opacity"
                to: 1
                type: Anim.DefaultEffects
            }
        }
    }
}
