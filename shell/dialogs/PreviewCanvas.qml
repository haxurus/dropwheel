// PreviewCanvas — a preview image fitted (aspect preserved, centred) inside the item, plus an overlay item that
// covers exactly the displayed image: editors put their crop frames, redaction boxes, effects… in it and work in
// normalised coordinates (0..1 of the image) with toNorm()/fromNorm().
//
//   property string source        local PNG path or URL ("" = nothing)
//   property real sourceWidth     logical size used for the aspect ratio (original pixels, PDF points…);
//   property real sourceHeight    0 → the loaded image's own size
//   property real padding         free space around the image (default 8 × scale)
//   property bool imageVisible    false hides the plain image (e.g. when an effect renders it)
//   property color backdrop       colour drawn behind the image (transparent PNGs), default surfaceContainerHighest
//   readonly property Image image             the Image item (MultiEffect source)
//   readonly property Item overlay            item covering the displayed image
//   readonly property real imageX, imageY, imageWidth, imageHeight   displayed rect (item coordinates)
//   readonly property real fitScale           displayed pixels per source unit
//   readonly property bool ready              the image is loaded
//   readonly property bool failed             the image could not be loaded
//   default property alias content            children go into `overlay`
//   function toNorm(x, y) → {x, y}            item coordinates → normalised (clamped to 0..1)
//   function fromNorm(nx, ny) → {x, y}        normalised → item coordinates

import QtQuick
import "../services"

Item {
    id: root

    property string source: ""
    property real sourceWidth: 0
    property real sourceHeight: 0
    property real padding: Theme.s(8)
    property bool imageVisible: true
    property color backdrop: Theme.palette.m3surfaceContainerHighest

    readonly property Image image: img
    readonly property Item overlay: overlayItem
    readonly property real aspectW: root.sourceWidth > 0 ? root.sourceWidth : Math.max(1, img.implicitWidth)
    readonly property real aspectH: root.sourceHeight > 0 ? root.sourceHeight : Math.max(1, img.implicitHeight)
    readonly property real fitScale: {
        const w = Math.max(1, root.width - root.padding * 2);
        const h = Math.max(1, root.height - root.padding * 2);
        return Math.min(w / root.aspectW, h / root.aspectH);
    }
    readonly property real imageWidth: Math.max(1, Math.round(root.aspectW * root.fitScale))
    readonly property real imageHeight: Math.max(1, Math.round(root.aspectH * root.fitScale))
    readonly property real imageX: Math.round((root.width - root.imageWidth) / 2)
    readonly property real imageY: Math.round((root.height - root.imageHeight) / 2)
    readonly property bool ready: img.status === Image.Ready
    readonly property bool failed: img.status === Image.Error

    default property alias content: overlayItem.data

    function toNorm(x: real, y: real): var {
        return {
            x: Math.max(0, Math.min(1, (x - root.imageX) / root.imageWidth)),
            y: Math.max(0, Math.min(1, (y - root.imageY) / root.imageHeight))
        };
    }

    function fromNorm(nx: real, ny: real): var {
        return {
            x: root.imageX + nx * root.imageWidth,
            y: root.imageY + ny * root.imageHeight
        };
    }

    Rectangle {
        x: root.imageX
        y: root.imageY
        width: root.imageWidth
        height: root.imageHeight
        visible: root.ready || (root.sourceWidth > 0 && root.sourceHeight > 0)
        color: root.backdrop
    }

    Image {
        id: img

        x: root.imageX
        y: root.imageY
        width: root.imageWidth
        height: root.imageHeight
        source: root.source === "" ? "" : (root.source.indexOf("://") !== -1 ? root.source : Backend.fileUrl(root.source))
        fillMode: Image.Stretch
        asynchronous: true
        cache: true
        smooth: true
        mipmap: true
        visible: root.imageVisible
    }

    Item {
        id: overlayItem

        x: root.imageX
        y: root.imageY
        width: root.imageWidth
        height: root.imageHeight
    }
}
