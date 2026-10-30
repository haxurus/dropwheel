// StyledRect — Rectangle with a transparent default colour and animated colour changes (CAnim).
// All Rectangle properties are available (radius, topLeftRadius…, border).

import QtQuick

Rectangle {
    id: root

    color: "transparent"

    Behavior on color {
        CAnim {}
    }
}
