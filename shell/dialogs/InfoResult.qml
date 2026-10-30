pragma ComponentBehavior: Bound

// InfoResult — expanded view of a finished job with an "info" result (properties, media info, checksums…): the rows
// [{group, key, value}] grouped in a table with a filter field, per-row copy (hover) and selectable values, plus
// "Copy all" (plain text, grouped) and "Save as .txt" (Backend.util "save_text").
//
//   property var job        Jobs snapshot (result.info, result.message, label, files, paths?)
//   property var host       DialogSurface (unused; kept for a uniform API)
//   signal closeRequested()

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

FocusScope {
    id: root

    property var job: null
    property var host: null
    property string query: ""
    property string savedPath: ""
    property string error: ""
    property bool saving: false

    readonly property var info: {
        const raw = root.job?.result?.info;
        if (!Array.isArray(raw))
            return [];
        return raw.filter(r => r !== null && typeof r === "object").map(r => ({
                    group: String(r.group ?? ""),
                    key: String(r.key ?? ""),
                    value: (r.value === null || r.value === undefined) ? "" : (typeof r.value === "object" ? JSON.stringify(r.value) : String(r.value))
                }));
    }
    readonly property var rows: {
        const q = root.query.trim().toLowerCase();
        const out = [];
        let lastGroup = null;
        for (const r of root.info) {
            if (q !== "" && r.key.toLowerCase().indexOf(q) === -1 && r.value.toLowerCase().indexOf(q) === -1 && r.group.toLowerCase().indexOf(q) === -1)
                continue;
            if (r.group !== lastGroup) {
                lastGroup = r.group;
                if (r.group !== "")
                    out.push({
                        header: true,
                        group: r.group,
                        key: "",
                        value: ""
                    });
            }
            out.push({
                header: false,
                group: r.group,
                key: r.key,
                value: r.value
            });
        }
        return out;
    }
    readonly property string plainText: {
        const lines = [];
        let lastGroup = null;
        for (const r of root.info) {
            if (r.group !== lastGroup) {
                if (lines.length > 0)
                    lines.push("");
                lastGroup = r.group;
                if (r.group !== "")
                    lines.push(r.group);
            }
            lines.push((r.group !== "" ? "  " : "") + r.key + ": " + r.value);
        }
        return lines.join("\n");
    }

    signal closeRequested

    function copyText(text: string, onDone: var): void {
        Backend.util("copy_text", {
            text: text
        }, (res, err) => {
            if (err) {
                root.error = String(err.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.error = "";
            if (typeof onDone === "function")
                onDone();
        });
    }

    function save(): void {
        if (root.saving || root.plainText === "")
            return;
        root.saving = true;
        root.error = "";
        const args = {
            text: root.plainText + "\n",
            ext: "txt"
        };
        const own = root.job?.paths;
        if (Array.isArray(own) && own.length > 0)
            args.paths = own.map(p => String(p)).filter(p => p.startsWith("/"));
        const first = Array.isArray(root.job?.files) && root.job.files.length > 0 ? String(root.job.files[0]) : "";
        if (first !== "")
            args.name = DU.stemOf(first) + " (" + I18n.t("dialog.info.sfx") + ")";
        Backend.util("save_text", args, (res, err) => {
            root.saving = false;
            if (err || !res) {
                root.error = String(err?.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.savedPath = String(res.path ?? "");
        });
    }

    implicitWidth: Theme.s(720)
    implicitHeight: Math.min(Theme.s(640), Math.max(Theme.s(300), list.contentHeight + filter.height + bottomBar.height + Theme.s(72)))
    focus: true

    Component.onCompleted: Qt.callLater(() => list.forceActiveFocus())

    Keys.onPressed: event => {
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_F) {
            filter.focusInput();
            event.accepted = true;
        } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_S) {
            root.save();
            event.accepted = true;
        }
    }

    M3TextField {
        id: filter

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        visible: root.info.length > 8
        height: visible ? implicitHeight : 0
        leadingIcon: "search"
        label: I18n.t("dialog.info.filter")
        clearButton: true
        text: root.query
        onTextChanged: root.query = filter.text
        // Enter only filters (the list already follows the text): it must not close the result.
        Keys.onReturnPressed: event => event.accepted = true
        Keys.onEnterPressed: event => event.accepted = true
    }

    StyledRect {
        id: frame

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: filter.bottom
        anchors.topMargin: filter.visible ? Theme.spacing.medium : 0
        anchors.bottom: bottomBar.top
        anchors.bottomMargin: Theme.spacing.medium
        radius: Theme.rounding.large
        color: Theme.palette.m3surfaceContainerLow

        ListView {
            id: list

            anchors.fill: parent
            anchors.margins: Theme.padding.small
            clip: true
            model: root.rows
            boundsBehavior: Flickable.StopAtBounds
            activeFocusOnTab: true

            Keys.onPressed: event => {
                const line = Theme.s(40);
                const maxY = Math.max(0, list.contentHeight - list.height);
                if (event.key === Qt.Key_Down)
                    list.contentY = Math.min(maxY, list.contentY + line);
                else if (event.key === Qt.Key_Up)
                    list.contentY = Math.max(0, list.contentY - line);
                else if (event.key === Qt.Key_PageDown)
                    list.contentY = Math.min(maxY, list.contentY + list.height * 0.9);
                else if (event.key === Qt.Key_PageUp)
                    list.contentY = Math.max(0, list.contentY - list.height * 0.9);
                else
                    return;
                event.accepted = true;
            }

            delegate: Item {
                id: row

                required property var modelData
                required property int index
                readonly property bool header: row.modelData.header === true
                property bool copied: false

                width: ListView.view.width
                height: row.header ? Theme.s(36) : Math.max(Theme.s(32), valueText.implicitHeight + Theme.s(12))

                StyledText {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.padding.small
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Theme.s(6)
                    visible: row.header
                    text: String(row.modelData.group)
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.DemiBold
                    color: Theme.palette.m3primary
                }

                StyledRect {
                    anchors.fill: parent
                    visible: !row.header
                    radius: Theme.rounding.small
                    color: rowHover.hovered ? Qt.alpha(Theme.palette.m3onSurface, 0.05) : "transparent"

                    HoverHandler {
                        id: rowHover
                    }

                    StyledText {
                        id: keyText

                        anchors.left: parent.left
                        anchors.leftMargin: Theme.padding.small
                        anchors.top: parent.top
                        anchors.topMargin: Theme.s(6)
                        width: Math.round(parent.width * 0.34)
                        text: String(row.modelData.key)
                        elide: Text.ElideRight
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3onSurfaceVariant
                    }

                    TextEdit {
                        id: valueText

                        anchors.left: keyText.right
                        anchors.leftMargin: Theme.spacing.medium
                        anchors.right: copyOne.left
                        anchors.rightMargin: Theme.spacing.small
                        anchors.top: parent.top
                        anchors.topMargin: Theme.s(5)
                        readOnly: true
                        selectByMouse: true
                        text: String(row.modelData.value)
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.WrapAnywhere
                        font.family: /^[0-9a-fA-F]{16,}$/.test(String(row.modelData.value)) ? Theme.font.family.mono : Theme.font.family.sans
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3onSurface
                        selectionColor: Theme.palette.m3primary
                        selectedTextColor: Theme.palette.m3onPrimary
                    }

                    IconButton {
                        id: copyOne

                        anchors.right: parent.right
                        anchors.rightMargin: Theme.spacing.extraSmall
                        anchors.verticalCenter: parent.verticalCenter
                        type: ButtonBase.Text
                        opacity: rowHover.hovered || row.copied ? 1 : 0
                        icon: row.copied ? "check" : "content_copy"
                        iconSize: Theme.font.iconSize.small
                        tooltip: I18n.t("ui.copy")
                        onClicked: root.copyText(String(row.modelData.value), () => {
                            row.copied = true;
                            rowCopiedTimer.restart();
                        })

                        Timer {
                            id: rowCopiedTimer

                            interval: 1400
                            onTriggered: row.copied = false
                        }
                    }
                }
            }
        }

        StyledText {
            anchors.centerIn: parent
            width: parent.width - Theme.s(48)
            visible: root.rows.length === 0
            text: root.info.length === 0 ? I18n.t("dialog.info.empty") : I18n.t("dialog.info.no_results", {
                query: root.query.trim()
            })
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            color: Theme.palette.m3onSurfaceVariant
        }
    }

    Item {
        id: bottomBar

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Theme.s(52)

        StyledText {
            anchors.left: parent.left
            anchors.right: actions.left
            anchors.rightMargin: Theme.spacing.medium
            anchors.verticalCenter: parent.verticalCenter
            text: {
                if (root.error !== "")
                    return root.error;
                if (root.savedPath !== "")
                    return I18n.t("dialog.text.saved", {
                        path: root.savedPath
                    });
                return String(root.job?.result?.message ?? "");
            }
            elide: Text.ElideMiddle
            font.pointSize: Theme.font.size.small
            color: root.error !== "" ? Theme.palette.m3error : Theme.palette.m3onSurfaceVariant
        }

        Row {
            id: actions

            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacing.small

            TextButton {
                visible: root.savedPath !== ""
                type: ButtonBase.Text
                icon: "folder_open"
                text: I18n.t("ui.show_in_folder")
                onClicked: Backend.util("reveal", {
                    paths: [root.savedPath]
                }, null)
            }

            TextButton {
                type: ButtonBase.Tonal
                icon: root.saving ? "hourglass_top" : "save"
                text: I18n.t("dialog.text.save")
                disabled: root.info.length === 0 || root.saving
                onClicked: root.save()
            }

            TextButton {
                id: copyAll

                property bool copied: false

                type: ButtonBase.Filled
                icon: copyAll.copied ? "check" : "content_copy"
                text: copyAll.copied ? I18n.t("ui.copied") : I18n.t("dialog.info.copy_all")
                disabled: root.info.length === 0
                onClicked: root.copyText(root.plainText, () => {
                    copyAll.copied = true;
                    copyAllTimer.restart();
                })

                Timer {
                    id: copyAllTimer

                    interval: 1600
                    onTriggered: copyAll.copied = false
                }
            }
        }
    }
}
