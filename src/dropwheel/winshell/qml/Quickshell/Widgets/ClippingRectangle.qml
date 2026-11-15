import QtQuick
import QtQuick.Effects

// A rectangle whose children are clipped to its rounded shape (a MultiEffect mask). The software renderer (no GPU,
// some remote sessions) runs no shaders: there the children are clipped to the plain rectangle instead.
Rectangle {
    id: root

    default property alias content: inner.data
    readonly property bool shaders: GraphicsInfo.api !== GraphicsInfo.Software

    Item {
        id: inner

        anchors.fill: parent
        clip: !root.shaders
        layer.enabled: root.shaders
        visible: !root.shaders
    }

    Rectangle {
        id: shape

        anchors.fill: parent
        radius: root.radius
        layer.enabled: root.shaders
        visible: false
    }

    MultiEffect {
        anchors.fill: parent
        visible: root.shaders
        source: root.shaders ? inner : null
        maskEnabled: true
        maskSource: shape
    }
}
