// CAnim — ColorAnimation with Caelestia's colour timing (expressiveSlowEffects: 300 ms and its curve).
//
//   Behavior on color { CAnim {} }

import QtQuick
import "../services"

ColorAnimation {
    duration: Theme.anim.durations.expressiveSlowEffects
    easing.type: Easing.BezierSpline
    easing.bezierCurve: Theme.anim.curves.expressiveSlowEffects
}
