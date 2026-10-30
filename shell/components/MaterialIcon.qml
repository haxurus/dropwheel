// MaterialIcon — a Material Symbols Rounded ligature ("close", "compress", …) rendered as text.
//
//   property string text (inherited)   icon name
//   property real fill    FILL axis 0..1 (animatable; Behavior is up to the user)
//   property int grade    GRAD axis (Caelestia: 0 in light mode, -25 in dark mode)
//   property int weight   wght axis 100..700 (default 400)
//   property real size    point size, default Theme.font.iconSize.small (15 pt × scale); also sets opsz
//   When centring an icon in a box use `anchors.centerIn: parent; anchors.verticalCenterOffset: 1` — the glyphs sit
//   one pixel high.

import QtQuick
import "../services"

StyledText {
    id: root

    property real fill: 0
    property int grade: Theme.light ? 0 : -25
    property int weight: 400
    property real size: Theme.font.iconSize.small

    font.family: Theme.font.family.icon
    font.pointSize: Math.max(1, root.size)
    font.weight: root.weight
    font.variableAxes: ({
            "FILL": Math.max(0, Math.min(1, root.fill)),
            "GRAD": root.grade,
            "opsz": Math.max(20, Math.min(48, root.size)),
            "wght": Math.max(100, Math.min(700, root.weight))
        })
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    elide: Text.ElideNone
    wrapMode: Text.NoWrap
    Accessible.ignored: true
}
