pragma ComponentBehavior: Bound

// ShareResult — expanded view of a "share" result (LAN share for the phone: large QR code, URL, copy / open, countdown
// to the expiry with a progress bar, "Stop sharing" through Backend.shareStop) and of plain "qr" results (QR image
// of a text / URL with copy text, copy image, show in folder).
//
//   property var job        Jobs snapshot: result {kind, url, qr (PNG path), share_id, expires (epoch s), text,
//                           message}, finishedAt (ms)
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

    readonly property var result: root.job?.result ?? null
    readonly property bool isShare: String(root.result?.kind ?? "") === "share" || String(root.result?.share_id ?? "") !== ""
    readonly property string shareId: String(root.result?.share_id ?? "")
    readonly property string url: {
        const u = root.result?.url;
        if (typeof u === "string" && u !== "")
            return u;
        return String(root.result?.text ?? "");
    }
    readonly property string qr: {
        const q = root.result?.qr;
        if (typeof q === "string" && q !== "")
            return q;
        const outs = root.result?.outputs;
        if (Array.isArray(outs)) {
            const png = outs.find(o => /\.png$/i.test(String(o?.path ?? "")));
            if (png)
                return String(png.path);
        }
        return "";
    }
    readonly property real expiresMs: (DU.num(root.result?.expires) ?? 0) * 1000
    readonly property real startMs: {
        const f = DU.num(root.job?.finishedAt);
        return (f !== null && f > 0) ? f : priv.openedAt;
    }
    readonly property real remainingMs: root.expiresMs > 0 ? Math.max(0, root.expiresMs - priv.now) : -1
    readonly property bool expired: root.isShare && root.expiresMs > 0 && root.remainingMs <= 0
    readonly property bool active: root.isShare && !priv.stopped && !root.expired
    property string error: ""

    signal closeRequested

    function copyText(text: string, button: var): void {
        Backend.util("copy_text", {
            text: text
        }, (res, err) => {
            if (err) {
                root.error = String(err.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.error = "";
            if (button)
                button.flash();
        });
    }

    function copyImage(): void {
        if (root.qr === "")
            return;
        Backend.util("copy_files", {
            paths: [root.qr]
        }, (res, err) => {
            if (err) {
                root.error = String(err.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            root.error = "";
            copyImageButton.flash();
        });
    }

    function stop(): void {
        if (root.shareId === "" || priv.stopping || priv.stopped)
            return;
        priv.stopping = true;
        root.error = "";
        Backend.shareStop(root.shareId, (res, err) => {
            priv.stopping = false;
            if (err) {
                root.error = String(err.message ?? I18n.t("ui.error.unexpected"));
                return;
            }
            priv.stopped = true;
        });
    }

    function countdown(ms: real): string {
        const s = Math.ceil(ms / 1000);
        return I18n.formatDuration(s);
    }

    implicitWidth: Theme.s(560)
    implicitHeight: content.implicitHeight + Theme.s(16)
    focus: true

    QtObject {
        id: priv

        property real now: Date.now()
        readonly property real openedAt: Date.now()
        property bool stopping: false
        property bool stopped: false
    }

    Timer {
        interval: 1000
        repeat: true
        running: root.isShare && root.expiresMs > 0 && !priv.stopped && !root.expired
        triggeredOnStart: true
        onTriggered: priv.now = Date.now()
    }

    Column {
        id: content

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: Theme.s(4)
        spacing: Theme.spacing.large

        // QR code
        StyledRect {
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.min(Theme.s(300), parent.width)
            height: width
            radius: Theme.rounding.large
            color: "white"

            Image {
                id: qrImage

                anchors.fill: parent
                anchors.margins: Theme.s(12)
                visible: root.qr !== ""
                source: root.qr !== "" ? Backend.fileUrl(root.qr) : ""
                fillMode: Image.PreserveAspectFit
                smooth: false
                asynchronous: true
                opacity: root.isShare && !root.active ? 0.15 : 1

                Behavior on opacity {
                    Anim {
                        type: Anim.DefaultEffects
                    }
                }
            }

            Column {
                anchors.centerIn: parent
                width: parent.width - Theme.s(40)
                spacing: Theme.spacing.small
                visible: root.qr === "" || qrImage.status === Image.Error || (root.isShare && !root.active)

                MaterialIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.isShare && !root.active ? (priv.stopped ? "cancel" : "timer_off") : "qr_code_2"
                    size: Theme.font.iconSize.extraLarge
                    color: "#444444"
                }

                StyledText {
                    width: parent.width
                    text: {
                        if (root.isShare && priv.stopped)
                            return I18n.t("dialog.share.stopped");
                        if (root.expired)
                            return I18n.t("dialog.share.expired");
                        return I18n.t("dialog.share.no_qr");
                    }
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: "#444444"
                }
            }
        }

        StyledText {
            width: parent.width
            visible: root.isShare
            text: I18n.t("dialog.share.instructions")
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.pointSize: Theme.font.size.normal
            color: Theme.palette.m3onSurfaceVariant
        }

        // URL / text
        StyledRect {
            width: parent.width
            height: Math.max(Theme.s(44), urlText.implicitHeight + Theme.s(16))
            radius: Theme.rounding.medium
            color: Theme.palette.m3surfaceContainerHigh
            visible: root.url !== ""

            TextEdit {
                id: urlText

                anchors.left: parent.left
                anchors.right: urlButtons.left
                anchors.leftMargin: Theme.padding.large
                anchors.rightMargin: Theme.spacing.small
                anchors.verticalCenter: parent.verticalCenter
                readOnly: true
                selectByMouse: true
                text: root.url
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.WrapAnywhere
                font.family: Theme.font.family.mono
                font.pointSize: Theme.font.size.small
                font.strikeout: root.isShare && !root.active
                color: Theme.palette.m3onSurface
                selectionColor: Theme.palette.m3primary
                selectedTextColor: Theme.palette.m3onPrimary
            }

            Row {
                id: urlButtons

                anchors.right: parent.right
                anchors.rightMargin: Theme.spacing.extraSmall
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0

                FlashIconButton {
                    id: copyUrlButton

                    icon: copyUrlButton.done ? "check" : "content_copy"
                    tooltip: root.isShare || DU.isUrl(root.url) ? I18n.t("dialog.share.copy_link") : I18n.t("ui.copy")
                    disabled: root.isShare && !root.active
                    onClicked: root.copyText(root.url, copyUrlButton)
                }

                IconButton {
                    type: ButtonBase.Text
                    visible: DU.isUrl(root.url)
                    icon: "open_in_new"
                    iconSize: Theme.font.iconSize.small
                    tooltip: I18n.t("dialog.text.open_link")
                    disabled: root.isShare && !root.active
                    onClicked: Qt.openUrlExternally(DU.urlFor(root.url))
                }
            }
        }

        // Countdown
        Column {
            width: parent.width
            spacing: Theme.spacing.small
            visible: root.isShare && root.expiresMs > 0

            Item {
                width: parent.width
                height: countdownText.implicitHeight

                StyledText {
                    id: countdownText

                    anchors.left: parent.left
                    text: {
                        if (priv.stopped)
                            return I18n.t("dialog.share.stopped");
                        if (root.expired)
                            return I18n.t("dialog.share.expired");
                        return I18n.t("dialog.share.expires_in", {
                            time: root.countdown(root.remainingMs)
                        });
                    }
                    font.pointSize: Theme.font.size.normal
                    font.features: ({
                            "tnum": 1
                        })
                    color: root.active ? Theme.palette.m3onSurface : Theme.palette.m3onSurfaceVariant
                }

                StyledText {
                    anchors.right: parent.right
                    visible: root.active
                    text: I18n.t("dialog.share.until", {
                        time: Qt.formatTime(new Date(root.expiresMs), "HH:mm")
                    })
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }
            }

            LinearProgress {
                width: parent.width
                visible: root.active
                value: {
                    const total = root.expiresMs - root.startMs;
                    return total > 0 ? root.remainingMs / total : 0;
                }
                showStop: false
            }
        }

        StyledText {
            width: parent.width
            visible: text !== ""
            text: root.error
            wrapMode: Text.Wrap
            font.pointSize: Theme.font.size.small
            color: Theme.palette.m3error
        }

        // Actions
        Item {
            width: parent.width
            height: Theme.s(44)

            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacing.small

                TextButton {
                    visible: !root.isShare && root.qr !== ""
                    type: ButtonBase.Text
                    icon: "folder_open"
                    text: I18n.t("ui.show_in_folder")
                    onClicked: Backend.util("reveal", {
                        paths: [root.qr]
                    }, null)
                }

                FlashTextButton {
                    id: copyImageButton

                    visible: !root.isShare && root.qr !== ""
                    type: ButtonBase.Tonal
                    icon: "image"
                    idleText: I18n.t("dialog.share.copy_image")
                    onClicked: root.copyImage()
                }

                TextButton {
                    visible: root.isShare
                    type: ButtonBase.Tonal
                    danger: true
                    icon: priv.stopping ? "hourglass_top" : "stop_circle"
                    text: I18n.t("dialog.share.stop")
                    disabled: !root.active || priv.stopping || root.shareId === ""
                    onClicked: root.stop()
                }

                TextButton {
                    type: ButtonBase.Filled
                    icon: "check"
                    text: I18n.t("ui.close")
                    onClicked: root.closeRequested()
                }
            }
        }
    }

    component FlashIconButton: IconButton {
        id: fib

        property bool done: false

        function flash(): void {
            fib.done = true;
            fibTimer.restart();
        }

        type: ButtonBase.Text
        iconSize: Theme.font.iconSize.small

        Timer {
            id: fibTimer

            interval: 1400
            onTriggered: fib.done = false
        }
    }

    component FlashTextButton: TextButton {
        id: ftb

        property string idleText: ""
        property bool done: false

        function flash(): void {
            ftb.done = true;
            ftbTimer.restart();
        }

        text: ftb.done ? I18n.t("ui.copied") : ftb.idleText

        Timer {
            id: ftbTimer

            interval: 1400
            onTriggered: ftb.done = false
        }
    }
}
