import QtQuick

// An input region: `item` (its bounds, as a rectangle or an ellipse); no item = empty.
QtObject {
    property Item item: null
    property int shape: RegionShape.Rect
}
