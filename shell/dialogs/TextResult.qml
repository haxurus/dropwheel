pragma ComponentBehavior: Bound

// TextResult — expanded view of a finished job with a text result (OCR, QR / barcode reading, checksums, extracted
// text…): selectable read-only text (wrap and monospace toggles, character / line count), Copy, Save as .txt
// (Backend.util "save_text", next to the job's files when their paths are known, then "Show in folder") and
// Open link when the whole text is a URL.
//
//   property var job        Jobs snapshot (result.text, result.message, label, files, paths?, result.outputs)
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

    readonly property string text: {
        const r = root.job?.result;
        const t = r?.text;
        if (typeof t === "string" && t !== "")
            return t;
        if (typeof r?.url === "string" && r.url !== "")
            return r.url;
        return String(r?.message ?? "");
    }
    readonly property string message: String(root.job?.result?.message ?? "")
    readonly property bool isUrl: DU.isUrl(root.text)
    readonly property int lineCount: root.text === "" ? 0 : root.text.split("\n").length
    property bool wrap: true
    property bool mono: /^[0-9a-fA-F]{16,}(\s|$)/.test(root.text) || /\t|^ {2,}/m.test(root.text)
    property string savedPath: ""
    property string error: ""
    property bool saving: false

    signal closeRequested

    function copy(): void {
        Backend.util("copy_text", {
            text: root.text
        }, (res, err) => {
            if (err) {
                root.error = String(err.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.error = "";
            copyButton.flash();
        });
    }

    function save(): void {
        if (root.saving || root.text === "")
            return;
        root.saving = true;
        root.error = "";
        const args = {
            text: root.text,
            ext: "txt"
        };
        const sources = priv.sourcePaths();
        if (sources.length > 0)
            args.paths = sources;
        const first = Array.isArray(root.job?.files) && root.job.files.length > 0 ? String(root.job.files[0]) : "";
        if (first !== "")
            args.name = DU.stemOf(first) + " (" + I18n.t("dialog.text.sfx") + ")";
        Backend.util("save_text", args, (res, err) => {
            root.saving = false;
            if (err || !res) {
                root.error = String(err?.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.savedPath = String(res.path ?? "");
        });
    }

    function openLink(): void {
        if (root.isUrl)
            Qt.openUrlExternally(DU.urlFor(root.text));
    }

    implicitWidth: Theme.s(720)
    implicitHeight: Math.min(Theme.s(640), Math.max(Theme.s(320), textView.implicitHeight + bottomBar.height + topBar.height + Theme.s(64)))
    focus: true

    Component.onCompleted: Qt.callLater(() => flick.forceActiveFocus())

    Keys.onPressed: event => {
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_S) {
            root.save();
            event.accepted = true;
        } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && textView.selectedText === "") {
            root.copy();
            event.accepted = true;
        }
    }

    QtObject {
        id: priv

        function sourcePaths(): var {
            const own = root.job?.paths;
            if (Array.isArray(own) && own.length > 0)
                return own.map(p => String(p)).filter(p => p.startsWith("/"));
            const outs = root.job?.result?.outputs;
            if (Array.isArray(outs))
                return outs.map(o => String(o?.path ?? "")).filter(p => p.startsWith("/"));
            return [];
        }
    }

    // ------------------------------------------------------------------ top bar
    Item {
        id: topBar

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Theme.s(40)

        StyledText {
            anchors.left: parent.left
            anchors.right: toggles.left
            anchors.rightMargin: Theme.spacing.medium
            anchors.verticalCenter: parent.verticalCenter
            text: {
                const stats = I18n.tn("dialog.text.chars", root.text.length) + " · " + I18n.tn("dialog.text.lines", root.lineCount);
                return root.message !== "" && root.message !== root.text ? root.message + " · " + stats : stats;
            }
            elide: Text.ElideRight
            font.pointSize: Theme.font.size.small
            color: Theme.palette.m3onSurfaceVariant
        }

        Row {
            id: toggles

            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacing.extraSmall

            IconButton {
                type: ButtonBase.Text
                isToggle: true
                checked: root.wrap
                icon: "wrap_text"
                iconSize: Theme.font.iconSize.small
                tooltip: I18n.t("dialog.text.wrap")
                onClicked: root.wrap = !root.wrap
            }

            IconButton {
                type: ButtonBase.Text
                isToggle: true
                checked: root.mono
                icon: "code"
                iconSize: Theme.font.iconSize.small
                tooltip: I18n.t("dialog.text.mono")
                onClicked: root.mono = !root.mono
            }
        }
    }

    // ------------------------------------------------------------------ text
    StyledRect {
        id: textFrame

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: topBar.bottom
        anchors.bottom: bottomBar.top
        anchors.bottomMargin: Theme.spacing.medium
        radius: Theme.rounding.large
        color: Theme.palette.m3surfaceContainerLow

        Flickable {
            id: flick

            anchors.fill: parent
            anchors.margins: Theme.padding.large
            clip: true
            contentWidth: root.wrap ? width : Math.max(width, textView.implicitWidth)
            contentHeight: textView.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            activeFocusOnTab: true

            Keys.onPressed: event => {
                const line = Theme.s(40);
                if (event.key === Qt.Key_Down)
                    flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), flick.contentY + line);
                else if (event.key === Qt.Key_Up)
                    flick.contentY = Math.max(0, flick.contentY - line);
                else if (event.key === Qt.Key_PageDown)
                    flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), flick.contentY + flick.height * 0.9);
                else if (event.key === Qt.Key_PageUp)
                    flick.contentY = Math.max(0, flick.contentY - flick.height * 0.9);
                else
                    return;
                event.accepted = true;
            }

            TextEdit {
                id: textView

                width: root.wrap ? flick.width : Math.max(flick.width, implicitWidth)
                readOnly: true
                selectByMouse: true
                persistentSelection: true
                text: root.text
                textFormat: TextEdit.PlainText
                wrapMode: root.wrap ? TextEdit.Wrap : TextEdit.NoWrap
                font.family: root.mono ? Theme.font.family.mono : Theme.font.family.sans
                font.pointSize: Theme.font.size.normal
                color: Theme.palette.m3onSurface
                selectionColor: Theme.palette.m3primary
                selectedTextColor: Theme.palette.m3onPrimary
            }
        }

        StyledText {
            anchors.centerIn: parent
            visible: root.text === ""
            text: I18n.t("dialog.text.empty")
            color: Theme.palette.m3onSurfaceVariant
        }
    }

    // ------------------------------------------------------------------ actions
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
            text: root.error !== "" ? root.error : (root.savedPath !== "" ? I18n.t("dialog.text.saved", {
                path: root.savedPath
            }) : "")
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
                visible: root.isUrl
                type: ButtonBase.Tonal
                icon: "open_in_new"
                text: I18n.t("dialog.text.open_link")
                onClicked: root.openLink()
            }

            TextButton {
                type: ButtonBase.Tonal
                icon: root.saving ? "hourglass_top" : "save"
                text: I18n.t("dialog.text.save")
                disabled: root.text === "" || root.saving
                onClicked: root.save()
            }

            CopyButton {
                id: copyButton

                disabled: root.text === ""
                onClicked: root.copy()
            }
        }
    }

    // Filled "Copy" button that shows "Copied" for a moment.
    component CopyButton: TextButton {
        id: cb

        property bool copied: false

        function flash(): void {
            cb.copied = true;
            copiedTimer.restart();
        }

        type: ButtonBase.Filled
        icon: cb.copied ? "check" : "content_copy"
        text: cb.copied ? I18n.t("ui.copied") : I18n.t("ui.copy")

        Timer {
            id: copiedTimer

            interval: 1600
            onTriggered: cb.copied = false
        }
    }
}
