import QtQuick

// Creates `component` once `loading` or `active` turns true; `item` is the object.
QtObject {
    id: root

    default property Component component
    property bool loading: false
    property bool active: false
    property QtObject item: null

    function ensure(): void {
        if (root.item === null && root.component !== null && (root.loading || root.active)) {
            root.item = root.component.createObject(root);
            if (root.item === null)
                console.warn("LazyLoader:", root.component.errorString());
        }
    }

    onLoadingChanged: ensure()
    onActiveChanged: ensure()
    onComponentChanged: ensure()
    Component.onCompleted: ensure()
}
