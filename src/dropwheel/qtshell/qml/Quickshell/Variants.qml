import QtQuick

// One `delegate` instance per model entry (`required property var modelData`).
QtObject {
    id: root

    property var model: []
    default property Component delegate
    readonly property Instantiator instantiator: Instantiator {
        model: root.model
        delegate: root.delegate
    }
}
