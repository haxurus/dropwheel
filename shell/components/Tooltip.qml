// Tooltip — M3 plain tooltip (inverse surface bubble) shown after a hover delay.
// Declare it inside the item it describes (target defaults to the parent); it adds a passive HoverHandler to the
// target, so clicks are not affected. The bubble is re-parented to the window's root item so it is never clipped and
// stays above everything (it renders outside a window mask, but it never takes input).
//
//   property Item target      item to watch (default: parent)
//   property string text      tooltip text (empty → never shown)
//   property int delay        hover delay in ms (default 500)
//   property bool shown       force the tooltip open (manual mode, e.g. wheel segments); hover still works
//   property bool hoverEnabled  default true; false → only `shown` controls it
//   property int position     Tooltip.Top (default) | Tooltip.Bottom | Tooltip.Left | Tooltip.Right
//                             (flips automatically when there is no room)
//   property real maxWidth    wrap width (default 280 × scale)
//   readonly property bool open
//
//   IconButton { icon: "content_copy"; Tooltip { text: I18n.t("ui.copy") } }

import QtQuick
import "../services"

Item {
    id: root

    enum Position {
        Top,
        Bottom,
        Left,
        Right
    }

    property Item target: parent
    property string text: ""
    property int delay: 500
    property bool shown: false
    property bool hoverEnabled: true
    property int position: Tooltip.Top
    property real maxWidth: 280 * Theme.scale
    readonly property bool open: priv.open

    width: 0
    height: 0
    visible: true

    function overlayRoot(): Item {
        let p = root.target ?? root.parent;
        while (p && p.parent)
            p = p.parent;
        return p;
    }

    function place(): void {
        const overlay = bubble.parent;
        const t = root.target;
        if (!overlay || !t)
            return;
        const gap = Math.round(6 * Theme.scale);
        const margin = Math.round(4 * Theme.scale);
        const tl = t.mapToItem(overlay, 0, 0);
        const w = bubble.width;
        const h = bubble.height;
        const cx = tl.x + t.width / 2;
        const cy = tl.y + t.height / 2;
        let pos = root.position;
        if (pos === Tooltip.Top && tl.y - gap - h < margin)
            pos = Tooltip.Bottom;
        else if (pos === Tooltip.Bottom && tl.y + t.height + gap + h > overlay.height - margin)
            pos = Tooltip.Top;
        else if (pos === Tooltip.Left && tl.x - gap - w < margin)
            pos = Tooltip.Right;
        else if (pos === Tooltip.Right && tl.x + t.width + gap + w > overlay.width - margin)
            pos = Tooltip.Left;
        let x;
        let y;
        if (pos === Tooltip.Top || pos === Tooltip.Bottom) {
            x = cx - w / 2;
            y = pos === Tooltip.Top ? tl.y - gap - h : tl.y + t.height + gap;
        } else {
            y = cy - h / 2;
            x = pos === Tooltip.Left ? tl.x - gap - w : tl.x + t.width + gap;
        }
        bubble.x = Math.round(Math.max(margin, Math.min(overlay.width - w - margin, x)));
        bubble.y = Math.round(Math.max(margin, Math.min(overlay.height - h - margin, y)));
    }

    QtObject {
        id: priv

        readonly property bool wanted: root.text !== "" && root.target !== null && root.enabled && (root.shown || (root.hoverEnabled && hover.hovered))
        property bool open: false

        onWantedChanged: {
            if (wanted) {
                showTimer.interval = root.shown ? 0 : root.delay;
                showTimer.restart();
            } else {
                showTimer.stop();
                open = false;
            }
        }
    }

    HoverHandler {
        id: hover

        parent: root.target
        enabled: root.hoverEnabled && root.target !== null
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad | PointerDevice.Stylus
    }

    Timer {
        id: showTimer

        onTriggered: {
            if (!priv.wanted)
                return;
            const overlay = root.overlayRoot();
            if (!overlay)
                return;
            if (bubble.parent !== overlay)
                bubble.parent = overlay;
            root.place();
            priv.open = true;
            Qt.callLater(root.place);
        }
    }

    // mapToItem() is not reactive: keep the bubble attached while the target moves (layouts settling, scrolling).
    Timer {
        interval: 200
        repeat: true
        running: priv.open
        onTriggered: root.place()
    }

    StyledRect {
        id: bubble

        z: 100000
        visible: opacity > 0
        opacity: priv.open ? 1 : 0
        scale: priv.open ? 1 : 0.9
        transformOrigin: Item.Center
        color: Theme.palette.m3inverseSurface
        radius: Theme.rounding.extraSmall
        implicitWidth: Math.ceil(Math.min(root.maxWidth, label.implicitWidth) + Theme.padding.small * 2)
        implicitHeight: Math.ceil(label.implicitHeight + Theme.padding.extraSmall * 2 + Math.round(2 * Theme.scale))
        width: implicitWidth
        height: implicitHeight

        onWidthChanged: if (priv.open)
            root.place()
        onHeightChanged: if (priv.open)
            root.place()

        StyledText {
            id: label

            anchors.centerIn: parent
            width: Math.min(root.maxWidth, implicitWidth)
            text: root.text
            color: Theme.palette.m3inverseOnSurface
            font.pointSize: Theme.font.size.small
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
        }

        Behavior on opacity {
            Anim {
                type: Anim.FastEffects
            }
        }

        Behavior on scale {
            Anim {
                type: Anim.FastEffects
            }
        }
    }
}
