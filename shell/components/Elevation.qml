// Elevation — Caelestia M3 shadow (RectangularShadow, Qt ≥ 6.9) for levels 0–5 (0, 1, 3, 6, 8, 12 dp).
// Place it behind the surface it shadows, with the same geometry and radius:
//   Elevation { anchors.fill: card; radius: card.radius; level: 2; z: -1 }
//
//   property int level   0..5 (animated)
//   property real dp     derived from level (× Theme.scale); animatable
//   color / blur / spread / offset / radius are the RectangularShadow properties.

import QtQuick
import QtQuick.Effects
import "../services"

RectangularShadow {
    id: root

    property int level: 1
    property real dp: [0, 1, 3, 6, 8, 12][Math.max(0, Math.min(5, root.level))] * Theme.scale

    color: Qt.alpha(Theme.palette.m3shadow, 0.7)
    blur: (root.dp * 5) ** 0.7
    spread: -root.dp * 0.3 + (root.dp * 0.1) ** 2
    offset.y: root.dp / 2
    opacity: Math.min(1, root.dp)
    visible: root.dp > 0

    Behavior on dp {
        Anim {
            type: Anim.SlowEffects
        }
    }
}
