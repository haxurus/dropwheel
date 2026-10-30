// FileIcon — file preview tile: the thumbnail (rounded, cropped) when available, otherwise a tonal tile with the
// Material Symbols icon of the file category; optional extension badge and multi-file count badge.
//
//   property string thumb       thumbnail path or URL ("" = none); falls back to the icon while loading / on error
//   property string category    detect.py category ("image", "pdf", "video", "folder", …)
//   property string icon        explicit Material Symbols name (FileInfo.icon / menu.icon); overrides the category
//   property string ext         badge text (extension without dot); "" hides the badge
//   property bool showBadge: true
//   property int count: 1       > 1 draws a stacked tile and a count badge
//   property real size          edge length (default 48 × scale)
//   property real radius        default Theme.rounding.medium (scaled down for small sizes)
//   readonly property bool thumbReady
//   readonly property string iconName   resolved icon
//   function iconFor(category) → Material Symbols name

import QtQuick
import Quickshell.Widgets
import "../services"

Item {
    id: root

    property string thumb: ""
    property string category: ""
    property string icon: ""
    property string ext: ""
    property bool showBadge: true
    property int count: 1
    property real size: Math.round(48 * Theme.scale)
    property real radius: Math.min(Theme.rounding.medium, size / 4)

    readonly property bool thumbReady: image.status === Image.Ready && root.thumb !== ""
    readonly property string iconName: root.icon !== "" ? root.icon : root.iconFor(root.category)

    function iconFor(cat: string): string {
        switch (cat) {
        case "image":
            return "image";
        case "svg":
            return "shapes";
        case "pdf":
            return "picture_as_pdf";
        case "document":
            return "description";
        case "spreadsheet":
            return "table_chart";
        case "presentation":
            return "slideshow";
        case "markdown":
            return "article";
        case "text":
            return "notes";
        case "code":
            return "code";
        case "data":
            return "data_object";
        case "ebook":
            return "menu_book";
        case "subtitle":
            return "subtitles";
        case "audio":
            return "music_note";
        case "video":
            return "movie";
        case "archive":
            return "folder_zip";
        case "font":
            return "font_download";
        case "folder":
            return "folder";
        case "url":
            return "link";
        case "appimage":
            return "apps";
        case "disk":
            return "album";
        case "package":
            return "inventory_2";
        case "mixed":
            return "stacks";
        default:
            return "draft";
        }
    }

    implicitWidth: size
    implicitHeight: size

    QtObject {
        id: priv

        readonly property color container: {
            switch (root.category) {
            case "image":
            case "svg":
            case "video":
                return Theme.palette.m3tertiaryContainer;
            case "pdf":
            case "document":
            case "spreadsheet":
            case "presentation":
            case "ebook":
                return Theme.palette.m3primaryContainer;
            default:
                return Theme.palette.m3secondaryContainer;
            }
        }
        readonly property color onContainer: {
            switch (root.category) {
            case "image":
            case "svg":
            case "video":
                return Theme.palette.m3onTertiaryContainer;
            case "pdf":
            case "document":
            case "spreadsheet":
            case "presentation":
            case "ebook":
                return Theme.palette.m3onPrimaryContainer;
            default:
                return Theme.palette.m3onSecondaryContainer;
            }
        }
        readonly property real stackOffset: root.count > 1 ? Math.max(2, Math.round(root.size * 0.06)) : 0
    }

    // Stacked look for multiple files.
    StyledRect {
        visible: root.count > 1
        x: priv.stackOffset * 2
        y: 0
        width: root.size - priv.stackOffset * 2
        height: root.size - priv.stackOffset * 2
        radius: root.radius
        color: Qt.alpha(priv.container, 0.55)
    }

    Item {
        id: face

        x: 0
        y: priv.stackOffset * 2
        width: root.size - priv.stackOffset * 2
        height: root.size - priv.stackOffset * 2

        StyledRect {
            anchors.fill: parent
            radius: root.radius
            visible: !root.thumbReady
            color: priv.container

            MaterialIcon {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: 1
                text: root.iconName
                fill: 1
                // Point size for ~45 % of the tile (1 pt = 4/3 px at the 96 dpi logical density used by Wayland Qt).
                size: Math.max(6, face.height * 0.45 * 0.75)
                color: priv.onContainer
            }
        }

        ClippingRectangle {
            anchors.fill: parent
            radius: root.radius
            color: "transparent"
            visible: root.thumbReady

            Image {
                id: image

                anchors.fill: parent
                source: root.thumb !== "" ? Backend.fileUrl(root.thumb) : ""
                sourceSize.width: Math.ceil(face.width * 2)
                sourceSize.height: Math.ceil(face.height * 2)
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: true
                smooth: true
                mipmap: true
            }
        }

        // Extension badge.
        StyledRect {
            readonly property real h: Math.max(10, Math.round(root.size * 0.3))

            visible: root.showBadge && root.ext !== "" && root.size >= 24
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: -Math.round(h * 0.25)
            anchors.bottomMargin: -Math.round(h * 0.15)
            height: h
            width: Math.min(root.size, Math.max(h * 1.4, badgeText.implicitWidth + h * 0.6))
            radius: h / 2
            color: Theme.palette.m3primary
            border.width: Math.max(1, Math.round(Theme.scale))
            border.color: Theme.palette.m3surface

            StyledText {
                id: badgeText

                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - parent.h * 0.4)
                text: root.ext.toUpperCase()
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
                color: Theme.palette.m3onPrimary
                font.pointSize: Math.max(5, parent.h * 0.55 * 0.75)
                font.weight: Font.Bold
            }
        }
    }

    // Multi-file count badge.
    StyledRect {
        readonly property real h: Math.max(14, Math.round(root.size * 0.34))

        visible: root.count > 1
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.rightMargin: -Math.round(h * 0.3)
        anchors.topMargin: -Math.round(h * 0.3)
        height: h
        width: Math.max(h, countText.implicitWidth + h * 0.5)
        radius: h / 2
        color: Theme.palette.m3error
        border.width: Math.max(1, Math.round(Theme.scale))
        border.color: Theme.palette.m3surface

        StyledText {
            id: countText

            anchors.centerIn: parent
            text: root.count > 99 ? "99+" : String(root.count)
            color: Theme.palette.m3onError
            font.pointSize: Math.max(5, parent.h * 0.55 * 0.75)
            font.weight: Font.Bold
        }
    }
}
