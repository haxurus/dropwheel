pragma ComponentBehavior: Bound

// Filmstrip — file switcher of the multi-file editors: a horizontal strip of thumbnails (Backend.preview "thumb",
// loaded lazily) with the file name, a dot on files that have their own edits, and an optional "Same for all" switch.
// Hidden when there is only one file. Keyboard: ← / → move between files while the strip has focus.
//
//   property var files            normalised files (EditorBase.fileList)
//   property int currentIndex
//   property bool showSameForAll  show the "Same for all" switch
//   property bool sameForAll
//   property var edited           array of booleans (or indices) marking files with their own edits
//   signal selected(int index)
//   signal sameForAllToggled(bool value)

import QtQuick
import "../services"
import "../components"

FocusScope {
    id: root

    property var files: []
    property int currentIndex: 0
    property bool showSameForAll: false
    property bool sameForAll: false
    property var edited: []

    signal selected(int index)
    signal sameForAllToggled(bool value)

    property var thumbs: ({})
    readonly property var life: ({
            alive: true
        })
    readonly property int count: root.files?.length ?? 0

    function isEdited(i: int): bool {
        const e = root.edited;
        if (!Array.isArray(e))
            return false;
        if (e.length > 0 && typeof e[0] === "boolean")
            return e[i] === true;
        return e.indexOf(i) !== -1;
    }

    function requestThumb(path: string): void {
        if (path === "" || root.thumbs[path] !== undefined)
            return;
        const life = root.life;
        const next = Object.assign({}, root.thumbs);
        next[path] = "";
        root.thumbs = next;
        Backend.preview("thumb", {
            path: path,
            size: 128
        }, (res, err) => {
            if (!life.alive)
                return;
            const png = (!err && res && typeof res.png === "string") ? res.png : "";
            if (png === "")
                return;
            const upd = Object.assign({}, root.thumbs);
            upd[path] = png;
            root.thumbs = upd;
        });
    }

    implicitHeight: Theme.s(72)
    implicitWidth: Theme.s(480)
    visible: root.count > 1
    activeFocusOnTab: true

    Component.onDestruction: root.life.alive = false
    onSameForAllChanged: {
        if (sameSwitch.checked !== root.sameForAll)
            sameSwitch.checked = root.sameForAll;
    }

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Left && root.currentIndex > 0) {
            root.selected(root.currentIndex - 1);
            event.accepted = true;
        } else if (event.key === Qt.Key_Right && root.currentIndex < root.count - 1) {
            root.selected(root.currentIndex + 1);
            event.accepted = true;
        }
    }

    Row {
        id: sameRow

        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spacing.small
        visible: root.showSameForAll
        width: visible ? implicitWidth : 0

        M3Switch {
            id: sameSwitch

            anchors.verticalCenter: parent.verticalCenter
            checked: root.sameForAll
            onToggled: root.sameForAllToggled(sameSwitch.checked)
        }

        StyledText {
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(implicitWidth, Theme.s(140))
            text: I18n.t("dialog.same_for_all")
            wrapMode: Text.Wrap
            color: Theme.palette.m3onSurfaceVariant
        }
    }

    ListView {
        id: strip

        anchors.left: sameRow.right
        anchors.leftMargin: root.showSameForAll ? Theme.spacing.large : 0
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        orientation: ListView.Horizontal
        spacing: Theme.spacing.small
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: root.files
        currentIndex: root.currentIndex
        highlightFollowsCurrentItem: false
        onCurrentIndexChanged: strip.positionViewAtIndex(strip.currentIndex, ListView.Contain)

        delegate: Item {
            id: cell

            required property var modelData
            required property int index
            readonly property bool current: cell.index === root.currentIndex
            readonly property string path: String(cell.modelData?.path ?? "")

            width: Theme.s(84)
            height: ListView.view.height

            Component.onCompleted: root.requestThumb(cell.path)

            StyledRect {
                anchors.fill: parent
                radius: Theme.rounding.medium
                color: cell.current ? Theme.palette.m3secondaryContainer : "transparent"
                border.width: cell.current ? 0 : Math.max(1, Math.round(Theme.scale))
                border.color: Theme.palette.m3outlineVariant

                StateLayer {
                    color: cell.current ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurface
                    onClicked: {
                        root.forceActiveFocus();
                        root.selected(cell.index);
                    }
                }
            }

            FileIcon {
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.spacing.small
                size: Math.max(Theme.s(24), cell.height - Theme.s(34))
                thumb: root.thumbs[cell.path] ?? ""
                category: String(cell.modelData?.category ?? "")
                icon: String(cell.modelData?.icon ?? "")
                ext: String(cell.modelData?.ext ?? "")
                showBadge: false
            }

            StyledText {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: Theme.spacing.extraSmall
                text: String(cell.modelData?.name ?? "")
                elide: Text.ElideMiddle
                horizontalAlignment: Text.AlignHCenter
                font.pointSize: Theme.font.size.small
                color: cell.current ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurfaceVariant
            }

            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.spacing.extraSmall
                width: Theme.s(8)
                height: width
                radius: width / 2
                visible: root.isEdited(cell.index)
                color: Theme.palette.m3primary
            }
        }
    }
}
