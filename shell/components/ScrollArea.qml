// ScrollArea — vertical Flickable with a slim Caelestia-style scrollbar (fades in while scrolling or hovered,
// draggable, click on the track to jump). Children go into the scrolled content; contentHeight follows the content
// (childrenRect) unless you set it. Give full-width children `width: parent.width` (parent = contentItem).
//
//   property bool scrollbarAlwaysOn: false
//   property real scrollbarWidth       default 4 × scale (6 × scale while hovered/dragged)
//   readonly property bool scrollable  content taller than the view
//   function scrollToItem(item, margin)  ensure an item inside the content is visible
//   function scrollToTop(), scrollToBottom()
//   Keyboard (when the area has focus): ↑ ↓ PageUp PageDown Home End.
//
//   ScrollArea { anchors.fill: parent; Column { width: parent.width; … } }

import QtQuick
import "../services"

Flickable {
    id: root

    property bool scrollbarAlwaysOn: false
    property real scrollbarWidth: Math.max(2, Math.round(4 * Theme.scale))
    readonly property bool scrollable: contentHeight > height + 0.5

    function scrollToItem(item: Item, margin: real): void {
        if (!item)
            return;
        const m = (typeof margin === "number" && margin >= 0) ? margin : Theme.spacing.small;
        const p = item.mapToItem(root.contentItem, 0, 0);
        if (p.y - m < root.contentY)
            root.contentY = Math.max(0, p.y - m);
        else if (p.y + item.height + m > root.contentY + root.height)
            root.contentY = Math.min(Math.max(0, root.contentHeight - root.height), p.y + item.height + m - root.height);
    }

    function scrollToTop(): void {
        root.contentY = 0;
    }

    function scrollToBottom(): void {
        root.contentY = Math.max(0, root.contentHeight - root.height);
    }

    function scrollBy(dy: real): void {
        root.contentY = Math.max(0, Math.min(Math.max(0, root.contentHeight - root.height), root.contentY + dy));
    }

    clip: true
    contentWidth: width
    contentHeight: contentItem.childrenRect.height
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    maximumFlickVelocity: 3000 * Theme.scale
    flickDeceleration: 3000 * Theme.scale
    interactive: scrollable

    Keys.onPressed: event => {
        const line = Math.round(40 * Theme.scale);
        switch (event.key) {
        case Qt.Key_Up:
            root.scrollBy(-line);
            break;
        case Qt.Key_Down:
            root.scrollBy(line);
            break;
        case Qt.Key_PageUp:
            root.scrollBy(-root.height * 0.9);
            break;
        case Qt.Key_PageDown:
            root.scrollBy(root.height * 0.9);
            break;
        case Qt.Key_Home:
            root.scrollToTop();
            break;
        case Qt.Key_End:
            root.scrollToBottom();
            break;
        default:
            return;
        }
        event.accepted = true;
    }

    // Scrollbar: parented to the Flickable itself (not the content) so it stays in place.
    Item {
        id: bar

        readonly property bool active: root.scrollbarAlwaysOn || root.moving || barMouse.containsMouse || barMouse.pressed || hideDelay.running
        readonly property real trackHeight: root.height - Theme.spacing.extraSmall * 2
        readonly property real thumbHeight: root.contentHeight > 0 ? Math.max(Math.round(24 * Theme.scale), trackHeight * Math.min(1, root.height / root.contentHeight)) : trackHeight
        readonly property real maxContentY: Math.max(1, root.contentHeight - root.height)

        parent: root
        z: 10
        visible: root.scrollable
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.margins: Theme.spacing.extraSmall
        width: Math.round(10 * Theme.scale)

        StyledRect {
            id: thumb

            anchors.right: parent.right
            width: barMouse.containsMouse || barMouse.pressed ? Math.round(root.scrollbarWidth * 1.5) : root.scrollbarWidth
            height: bar.thumbHeight
            y: (bar.trackHeight - bar.thumbHeight) * Math.max(0, Math.min(1, root.contentY / bar.maxContentY))
            radius: width / 2
            color: Theme.palette.m3secondary
            opacity: bar.active ? (barMouse.pressed ? 1 : barMouse.containsMouse ? 0.8 : 0.6) : 0

            Behavior on opacity {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
            Behavior on width {
                Anim {
                    type: Anim.FastEffects
                }
            }
        }

        MouseArea {
            id: barMouse

            property real grabOffset: 0

            function scrollToThumbY(y: real): void {
                const range = bar.trackHeight - bar.thumbHeight;
                const f = range > 0 ? Math.max(0, Math.min(1, y / range)) : 0;
                root.contentY = f * Math.max(0, root.contentHeight - root.height);
            }

            anchors.fill: parent
            hoverEnabled: true
            preventStealing: true
            cursorShape: Qt.PointingHandCursor
            onPressed: event => {
                // While the bar is hidden, let clicks reach the content underneath.
                if (thumb.opacity < 0.05) {
                    event.accepted = false;
                    return;
                }
                if (event.y >= thumb.y && event.y <= thumb.y + thumb.height) {
                    grabOffset = event.y - thumb.y;
                } else {
                    grabOffset = bar.thumbHeight / 2;
                    scrollToThumbY(event.y - grabOffset);
                }
            }
            onPositionChanged: event => {
                if (pressed)
                    scrollToThumbY(event.y - grabOffset);
            }
            onWheel: event => {
                root.scrollBy(-event.angleDelta.y / 120 * Math.round(60 * Theme.scale));
                event.accepted = true;
            }
        }

        Timer {
            id: hideDelay

            interval: 800
        }

        Connections {
            target: root

            function onContentYChanged(): void {
                hideDelay.restart();
            }
        }
    }
}
