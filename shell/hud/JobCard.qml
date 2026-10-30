pragma ComponentBehavior: Bound

// JobCard — one HUD card for a Jobs snapshot (SPEC §2.4), Caelestia toast look (rounding.large, Elevation 3, 1 px
// translucent border, icon tile + title/subtitle).
//   * queued / running: action label, file name(s), linear progress (indeterminate when progress is null), message,
//     percentage and a stop button (Jobs.cancel);
//   * done, files: draggable ResultChips (first 3 outputs, "+N more"), size delta ("2,4 MB → 380 kB (−84%)"),
//     Open / Show in folder / Copy / Undo (when undoable) — auto-dismiss after timeoutMs, paused while hovered;
//   * done, text / info / qr / share: compact preview + Copy + Expand (DialogWindow.showResult via expandRequested);
//     a share result also offers Stop sharing and stays until dismissed;
//   * failed: error container colours, message, expandable details, Copy install command / Copy details; stays
//     until ×;  cancelled / undone: short auto-dismiss;  warnings: a small line on every state.
//
//   required property string jobId
//   required property var job          Jobs snapshot (see services/Jobs.qml)
//   property int timeoutMs             auto-dismiss delay of finished jobs (config hud.timeout_ms; 0 = never)
//   property real cardWidth            default 360 × scale
//   property int slideFrom             +1 slide in from the right (default), -1 from the left
//   readonly property bool paused      hovered, dragging a chip or showing details (auto-dismiss paused)
//   readonly property bool leaving     exit animation running
//   signal expandRequested(var job)    open the full result view (text / info / qr / share)
//   function leave()                   exit animation, then Jobs.dismiss(jobId)

import QtQuick
import QtQuick.Layouts
import "../services"
import "../components"
import "../wheel/WheelText.js" as WT

Item {
    id: root

    required property string jobId
    required property var job
    property int timeoutMs: 7000
    property real cardWidth: Math.round(360 * Theme.scale)
    property int slideFrom: 1

    readonly property string jobState: String(root.job?.state ?? "queued")
    readonly property bool running: root.jobState === "queued" || root.jobState === "running"
    readonly property bool succeeded: root.jobState === "done"
    readonly property bool failedState: root.jobState === "failed"
    readonly property bool cancelledState: root.jobState === "cancelled"
    readonly property bool cancelling: root.job?.cancelling === true
    readonly property bool undone: root.job?.undone === true
    readonly property bool undoing: root.job?.undoing === true
    readonly property var result: root.succeeded && root.job?.result && typeof root.job.result === "object" ? root.job.result : null
    readonly property string kind: String(root.result?.kind ?? "files")
    readonly property var outputs: root.list(root.result?.outputs).filter(o => o && typeof o === "object")
    readonly property var outputPaths: root.outputs.map(o => String(o.path ?? "")).filter(p => p !== "")
    readonly property bool undoable: root.succeeded && !root.undone && root.result?.undoable === true && root.outputPaths.length > 0
    readonly property var warnings: root.list(root.job?.warnings).filter(w => String(w ?? "") !== "")
    readonly property var error: root.failedState ? (root.job?.error ?? null) : null
    readonly property bool leaving: priv.leaving
    readonly property bool paused: hover.hovered || priv.chipDragging || priv.showDetails

    readonly property color fg: root.failedState ? Theme.palette.m3onErrorContainer : Theme.palette.m3onSurface
    readonly property color fgVariant: root.failedState ? Qt.alpha(Theme.palette.m3onErrorContainer, 0.8) : Theme.palette.m3onSurfaceVariant

    readonly property string title: {
        const label = String(root.job?.label ?? "");
        if (label !== "")
            return label;
        const action = String(root.job?.action ?? "");
        return action !== "" ? action : WT.tr(I18n, "hud.running");
    }

    readonly property string filesSummary: {
        const names = root.list(root.job?.files).map(f => String(f ?? "")).filter(f => f !== "");
        if (names.length === 0)
            return "";
        if (names.length === 1)
            return names[0];
        return WT.trn(I18n, "hud.files_more", names.length - 1, {
            name: names[0]
        });
    }

    readonly property string subtitle: {
        if (priv.feedback !== "")
            return priv.feedback;
        if (root.undone) {
            const m = String(root.job?.undoMessage ?? "");
            return m !== "" ? m : WT.tr(I18n, "hud.undone");
        }
        if (root.cancelling)
            return WT.tr(I18n, "hud.cancelling");
        if (root.cancelledState)
            return root.filesSummary !== "" ? WT.tr(I18n, "hud.cancelled") + " · " + root.filesSummary : WT.tr(I18n, "hud.cancelled");
        if (root.succeeded) {
            const m = String(root.result?.message ?? "");
            return m !== "" ? m : root.filesSummary;
        }
        return root.filesSummary;
    }

    readonly property string sizeDelta: {
        const before = Number(root.result?.size_before);
        let after = Number(root.result?.size_after);
        if (!isFinite(after) || root.result?.size_after === undefined || root.result?.size_after === null) {
            // Fall back to the sum of the output sizes.
            let sum = 0;
            let known = root.outputs.length > 0;
            for (const o of root.outputs) {
                if (typeof o.size === "number" && isFinite(o.size))
                    sum += o.size;
                else
                    known = false;
            }
            after = known ? sum : NaN;
        }
        if (root.result?.size_before === undefined || root.result?.size_before === null || !isFinite(before) || before <= 0 || !isFinite(after) || after < 0)
            return "";
        if (after === before)
            return WT.tr(I18n, "hud.size_same", {
                size: I18n.formatSize(after)
            });
        const frac = (after - before) / before;
        const pct = Math.abs(frac) < 0.005 ? "0%" : (frac < 0 ? "−" : "+") + I18n.formatPercent(Math.min(1, Math.abs(frac))) + (Math.abs(frac) > 1 ? "+" : "");
        return WT.tr(I18n, "hud.size_delta", {
            before: I18n.formatSize(before),
            after: I18n.formatSize(after),
            delta: pct
        });
    }

    readonly property string statusIcon: {
        if (root.undone)
            return "undo";
        if (root.failedState)
            return "error";
        if (root.cancelledState)
            return "block";
        if (root.succeeded) {
            switch (root.kind) {
            case "text":
                return "notes";
            case "info":
                return "info";
            case "qr":
                return "qr_code_2";
            case "share":
                return "share";
            default:
                return "check_circle";
            }
        }
        return "";
    }

    // Auto-dismiss delay for the current state (0 = stays until dismissed).
    readonly property int dismissAfter: {
        if (root.running || root.failedState || root.undoing)
            return 0;
        if (root.undone)
            return 2500;
        if (root.cancelledState)
            return root.timeoutMs > 0 ? Math.min(3000, root.timeoutMs) : 3000;
        if (root.succeeded) {
            if (root.kind === "share" && !priv.shareStopped)
                return 0;
            if (root.job?.undoError)
                return 0;
            return Math.max(0, root.timeoutMs);
        }
        return 0;
    }

    signal expandRequested(var job)

    // Arrays reach a delegate through the ListModel role as sequence wrappers (Array.isArray() is false for them):
    // copy any array-like into a plain array.
    function list(v: var): var {
        if (Array.isArray(v))
            return v;
        if (v === null || v === undefined || typeof v !== "object" || typeof v.length !== "number")
            return [];
        const out = [];
        for (let i = 0; i < v.length; i++)
            out.push(v[i]);
        return out;
    }

    function leave(): void {
        if (priv.leaving)
            return;
        priv.leaving = true;
        enterAnim.stop();
        leaveAnim.start();
    }

    function flashFeedback(text: string): void {
        priv.feedback = text;
        feedbackTimer.restart();
    }

    function utilCall(op: string, args: var, okText: string): void {
        Backend.util(op, args, (res, err) => {
            if (err)
                root.flashFeedback(String(err.message ?? WT.tr(I18n, "hud.copy_failed")));
            else if (okText !== "")
                root.flashFeedback(okText);
        });
    }

    function openPaths(paths: var): void {
        if (paths.length > 0)
            root.utilCall("open", {
                paths: paths
            }, "");
    }

    function copyText(text: string): void {
        if (text !== "")
            root.utilCall("copy_text", {
                text: text
            }, WT.tr(I18n, "hud.copied"));
    }

    function infoText(): string {
        const info = root.list(root.result?.info);
        const lines = [];
        let group = null;
        for (const row of info) {
            const g = String(row?.group ?? "");
            if (g !== "" && g !== group) {
                if (lines.length > 0)
                    lines.push("");
                lines.push(g);
                group = g;
            }
            lines.push(String(row?.key ?? "") + ": " + String(row?.value ?? ""));
        }
        return lines.join("\n");
    }

    function copyResult(): void {
        switch (root.kind) {
        case "text":
            root.copyText(String(root.result?.text ?? ""));
            break;
        case "info":
            root.copyText(root.infoText());
            break;
        case "qr":
            if (String(root.result?.qr ?? "") !== "")
                root.utilCall("copy_files", {
                    paths: [String(root.result.qr)]
                }, WT.tr(I18n, "hud.copied"));
            else
                root.copyText(String(root.result?.text ?? ""));
            break;
        case "share":
            root.copyText(String(root.result?.url ?? ""));
            break;
        default:
            if (root.outputPaths.length > 0)
                root.utilCall("copy_files", {
                    paths: root.outputPaths
                }, WT.tr(I18n, "hud.copied"));
        }
    }

    function stopShare(): void {
        const id = String(root.result?.share_id ?? "");
        if (id === "") {
            priv.shareStopped = true;
            return;
        }
        Backend.shareStop(id, (res, err) => {
            if (err) {
                root.flashFeedback(String(err.message ?? I18n.t("ui.error.unexpected")));
                return;
            }
            priv.shareStopped = true;
            root.flashFeedback(WT.tr(I18n, "hud.share_stopped"));
        });
    }

    implicitWidth: root.cardWidth
    implicitHeight: Math.ceil(card.height * priv.collapse)
    clip: priv.leaving

    Accessible.role: Accessible.Notification
    Accessible.name: root.title
    Accessible.description: root.subtitle

    // The card owns the removal of its row (Jobs.dismiss in leave()), so Jobs.prune leaves the row alone meanwhile.
    Component.onCompleted: {
        enterAnim.start();
        Jobs.hold(root.jobId, true);
    }
    Component.onDestruction: Jobs.hold(root.jobId, false)
    onDismissAfterChanged: priv.elapsed = 0

    QtObject {
        id: priv

        property bool leaving: false
        property bool showDetails: false
        property bool chipDragging: false
        property bool shareStopped: false
        property string feedback: ""
        property real elapsed: 0
        property real collapse: 1
        property real slide: 1
    }

    Timer {
        interval: 100
        repeat: true
        running: root.dismissAfter > 0 && !root.paused && !priv.leaving
        onTriggered: {
            priv.elapsed += interval;
            if (priv.elapsed >= root.dismissAfter)
                root.leave();
        }
    }

    // A held row is never pruned, so the card itself notices that the share server has stopped (expires: Unix seconds).
    Timer {
        interval: 1000
        repeat: true
        running: root.kind === "share" && !priv.shareStopped && Number(root.result?.expires) > 0
        onTriggered: {
            if (Date.now() / 1000 >= Number(root.result?.expires))
                priv.shareStopped = true;
        }
    }

    Timer {
        id: feedbackTimer

        interval: 1600
        onTriggered: priv.feedback = ""
    }

    ParallelAnimation {
        id: enterAnim

        Anim {
            target: priv
            property: "slide"
            from: 1
            to: 0
            type: Anim.FastSpatial
        }
        Anim {
            target: card
            property: "opacity"
            from: 0
            to: 1
            type: Anim.DefaultEffects
        }
    }

    SequentialAnimation {
        id: leaveAnim

        ParallelAnimation {
            Anim {
                target: priv
                property: "slide"
                to: 1
                type: Anim.EmphasizedSmall
            }
            Anim {
                target: card
                property: "opacity"
                to: 0
                type: Anim.FastEffects
            }
        }
        Anim {
            target: priv
            property: "collapse"
            to: 0
            type: Anim.StandardSmall
        }
        ScriptAction {
            script: Jobs.dismiss(root.jobId)
        }
    }

    StyledRect {
        id: card

        x: root.slideFrom * priv.slide * Math.round(48 * Theme.scale)
        width: root.cardWidth
        height: body.implicitHeight + Theme.padding.medium * 2
        radius: Theme.rounding.large
        opacity: 0
        color: root.failedState ? Theme.palette.m3errorContainer : Theme.tPalette.m3surfaceContainer
        border.width: Math.max(1, Math.round(Theme.scale))
        border.color: Qt.alpha(root.failedState ? Theme.palette.m3error : root.succeeded && !root.undone ? Theme.palette.m3success : Theme.palette.m3outlineVariant, 0.3)

        Elevation {
            anchors.fill: parent
            radius: parent.radius
            level: 3
            z: -1
        }

        HoverHandler {
            id: hover
        }

        ColumnLayout {
            id: body

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.padding.medium
            spacing: Theme.spacing.small

            // ---------------------------------------------------------- header
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacing.medium

                Item {
                    Layout.preferredWidth: Math.round(36 * Theme.scale)
                    Layout.preferredHeight: Math.round(36 * Theme.scale)
                    Layout.alignment: Qt.AlignTop

                    CircularProgress {
                        anchors.fill: parent
                        visible: root.running
                        running: visible
                        indeterminate: root.jobState === "queued" || typeof root.job?.progress !== "number"
                        value: typeof root.job?.progress === "number" ? root.job.progress : 0
                        strokeWidth: Math.max(2, Math.round(3 * Theme.scale))
                    }

                    StyledRect {
                        anchors.fill: parent
                        visible: !root.running
                        radius: Theme.rounding.medium
                        color: {
                            if (root.failedState)
                                return Theme.palette.m3error;
                            if (root.undone || root.cancelledState)
                                return Theme.palette.m3surfaceContainerHighest;
                            return Theme.palette.m3successContainer;
                        }

                        MaterialIcon {
                            anchors.centerIn: parent
                            anchors.verticalCenterOffset: 1
                            text: root.statusIcon
                            fill: 1
                            size: Theme.font.iconSize.medium * 1.1
                            color: {
                                if (root.failedState)
                                    return Theme.palette.m3onError;
                                if (root.undone || root.cancelledState)
                                    return Theme.palette.m3onSurfaceVariant;
                                return Theme.palette.m3onSuccessContainer;
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 0

                    StyledText {
                        Layout.fillWidth: true
                        text: root.title
                        color: root.fg
                        elide: Text.ElideRight
                        font.pointSize: Theme.font.size.normal
                        font.weight: Font.DemiBold
                    }

                    StyledText {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: root.subtitle
                        color: root.fgVariant
                        elide: Text.ElideMiddle
                        font.pointSize: Theme.font.size.small
                    }
                }

                IconButton {
                    Layout.alignment: Qt.AlignTop
                    type: IconButton.Text
                    icon: root.running ? "stop_circle" : "close"
                    iconSize: Theme.font.iconSize.medium
                    disabled: root.cancelling
                    tooltip: root.running ? WT.tr(I18n, "hud.cancel") : WT.tr(I18n, "hud.dismiss")
                    inactiveOnColour: root.fgVariant
                    onClicked: {
                        if (root.running)
                            Jobs.cancel(root.jobId);
                        else
                            root.leave();
                    }
                }
            }

            // ---------------------------------------------------------- progress
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.running
                spacing: Theme.spacing.extraSmall

                LinearProgress {
                    Layout.fillWidth: true
                    running: visible
                    indeterminate: root.jobState === "queued" || typeof root.job?.progress !== "number"
                    value: typeof root.job?.progress === "number" ? root.job.progress : 0
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacing.small

                    StyledText {
                        Layout.fillWidth: true
                        text: {
                            const m = String(root.job?.message ?? "");
                            if (root.cancelling)
                                return WT.tr(I18n, "hud.cancelling");
                            if (m !== "")
                                return m;
                            return root.jobState === "queued" ? WT.tr(I18n, "hud.queued") : WT.tr(I18n, "hud.running");
                        }
                        color: root.fgVariant
                        elide: Text.ElideRight
                        font.pointSize: Theme.font.size.small
                    }

                    StyledText {
                        visible: typeof root.job?.progress === "number" && root.jobState === "running"
                        text: I18n.formatPercent(root.job?.progress ?? 0)
                        color: root.fgVariant
                        font.pointSize: Theme.font.size.small
                        font.features: ({
                                "tnum": 1
                            })
                    }
                }
            }

            // ---------------------------------------------------------- warnings
            RowLayout {
                Layout.fillWidth: true
                visible: root.warnings.length > 0
                spacing: Theme.spacing.extraSmall

                MaterialIcon {
                    Layout.alignment: Qt.AlignTop
                    text: "warning"
                    fill: 1
                    color: root.failedState ? root.fg : Theme.palette.m3tertiary
                    size: Theme.font.iconSize.small
                }

                StyledText {
                    Layout.fillWidth: true
                    text: {
                        if (root.warnings.length === 0)
                            return "";
                        const first = String(root.warnings[0]);
                        if (root.warnings.length === 1)
                            return first;
                        return first + " " + WT.trn(I18n, "hud.warnings_more", root.warnings.length - 1);
                    }
                    color: root.failedState ? root.fg : Theme.palette.m3tertiary
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    font.pointSize: Theme.font.size.small
                }
            }

            // ---------------------------------------------------------- files result
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.succeeded && !root.undone && root.kind === "files"
                spacing: Theme.spacing.extraSmall

                Repeater {
                    model: root.outputs.slice(0, 3)

                    delegate: ResultChip {
                        required property var modelData

                        output: modelData
                        maxWidth: body.width
                        Layout.maximumWidth: body.width
                        onClicked: root.openPaths([path])
                        onDraggingChanged: priv.chipDragging = dragging
                    }
                }

                StyledText {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: {
                        if (root.outputs.length > 3)
                            return WT.trn(I18n, "hud.outputs_more", root.outputs.length - 3);
                        if (root.outputs.length === 0 && String(root.result?.message ?? "") === "")
                            return WT.tr(I18n, "hud.no_output");
                        return "";
                    }
                    color: root.fgVariant
                    font.pointSize: Theme.font.size.small
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: root.sizeDelta !== ""
                    spacing: Theme.spacing.extraSmall

                    MaterialIcon {
                        text: "data_usage"
                        color: Theme.palette.m3primary
                        size: Theme.font.iconSize.small
                    }

                    StyledText {
                        Layout.fillWidth: true
                        text: root.sizeDelta
                        color: root.fgVariant
                        elide: Text.ElideRight
                        font.pointSize: Theme.font.size.small
                        font.features: ({
                                "tnum": 1
                            })
                    }
                }
            }

            // ---------------------------------------------------------- text / qr / share preview
            StyledRect {
                Layout.fillWidth: true
                Layout.preferredHeight: previewRow.implicitHeight + Theme.padding.small * 2
                visible: root.succeeded && !root.undone && (root.kind === "text" || root.kind === "qr" || root.kind === "share")
                radius: Theme.rounding.medium
                color: Theme.tPalette.m3surfaceContainerHigh

                RowLayout {
                    id: previewRow

                    anchors.fill: parent
                    anchors.margins: Theme.padding.small
                    spacing: Theme.spacing.small

                    Image {
                        readonly property string src: String(root.result?.qr ?? "")

                        Layout.preferredWidth: Math.round(64 * Theme.scale)
                        Layout.preferredHeight: Math.round(64 * Theme.scale)
                        Layout.alignment: Qt.AlignTop
                        visible: src !== "" && (root.kind === "qr" || root.kind === "share")
                        source: src !== "" ? Backend.fileUrl(src) : ""
                        sourceSize.width: Math.round(128 * Theme.scale)
                        sourceSize.height: Math.round(128 * Theme.scale)
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        smooth: false
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: Theme.spacing.extraSmall

                        StyledText {
                            Layout.fillWidth: true
                            visible: root.kind === "share"
                            text: priv.shareStopped ? WT.tr(I18n, "hud.share_stopped") : WT.tr(I18n, "hud.sharing")
                            color: root.fgVariant
                            font.pointSize: Theme.font.size.small
                            font.weight: Font.Medium
                        }

                        StyledText {
                            Layout.fillWidth: true
                            text: {
                                if (root.kind === "share")
                                    return String(root.result?.url ?? "");
                                const t = String(root.result?.text ?? "");
                                if (t !== "")
                                    return t;
                                return String(root.result?.url ?? root.result?.message ?? "");
                            }
                            color: root.fg
                            wrapMode: root.kind === "share" ? Text.WrapAnywhere : Text.Wrap
                            maximumLineCount: root.kind === "text" ? 4 : 3
                            elide: Text.ElideRight
                            font.family: root.kind === "share" ? Theme.font.family.mono : Theme.font.family.sans
                            font.variableAxes: root.kind === "share" ? ({}) : Theme.font.axes
                            font.pointSize: Theme.font.size.smaller
                        }
                    }
                }
            }

            // ---------------------------------------------------------- info preview
            StyledRect {
                Layout.fillWidth: true
                Layout.preferredHeight: infoColumn.implicitHeight + Theme.padding.small * 2
                visible: root.succeeded && !root.undone && root.kind === "info"
                radius: Theme.rounding.medium
                color: Theme.tPalette.m3surfaceContainerHigh

                ColumnLayout {
                    id: infoColumn

                    readonly property var rows: root.list(root.result?.info).filter(r => r && typeof r === "object")

                    anchors.fill: parent
                    anchors.margins: Theme.padding.small
                    spacing: Math.round(2 * Theme.scale)

                    Repeater {
                        model: infoColumn.rows.slice(0, 4)

                        delegate: RowLayout {
                            id: infoRow

                            required property var modelData

                            Layout.fillWidth: true
                            spacing: Theme.spacing.small

                            StyledText {
                                Layout.preferredWidth: Math.round(infoColumn.width * 0.38)
                                text: String(infoRow.modelData.key ?? "")
                                color: root.fgVariant
                                elide: Text.ElideRight
                                font.pointSize: Theme.font.size.small
                            }

                            StyledText {
                                Layout.fillWidth: true
                                text: String(infoRow.modelData.value ?? "")
                                color: root.fg
                                elide: Text.ElideMiddle
                                font.pointSize: Theme.font.size.small
                            }
                        }
                    }

                    StyledText {
                        Layout.fillWidth: true
                        visible: infoColumn.rows.length > 4
                        text: WT.trn(I18n, "hud.info_more", infoColumn.rows.length - 4)
                        color: root.fgVariant
                        font.pointSize: Theme.font.size.small
                    }
                }
            }

            // ---------------------------------------------------------- error
            StyledText {
                Layout.fillWidth: true
                visible: root.failedState && text !== ""
                text: String(root.error?.message ?? I18n.t("ui.error.unexpected"))
                color: root.fg
                wrapMode: Text.Wrap
                maximumLineCount: 5
                elide: Text.ElideRight
                font.pointSize: Theme.font.size.smaller
            }

            StyledRect {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(detailsText.implicitHeight, Math.round(160 * Theme.scale)) + Theme.padding.small * 2
                visible: root.failedState && priv.showDetails && String(root.error?.details ?? "") !== ""
                radius: Theme.rounding.small
                color: Qt.alpha(Theme.palette.m3onErrorContainer, 0.08)
                clip: true

                StyledText {
                    id: detailsText

                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: Theme.padding.small
                    // Show the end of long tracebacks (most useful part) when it does not fit.
                    text: String(root.error?.details ?? "")
                    color: root.fg
                    wrapMode: Text.WrapAnywhere
                    font.family: Theme.font.family.mono
                    font.variableAxes: ({})
                    font.pointSize: Theme.font.size.small * 0.92
                }
            }

            StyledText {
                Layout.fillWidth: true
                visible: root.job?.undoError !== null && root.job?.undoError !== undefined
                text: String(root.job?.undoError?.message ?? "")
                color: Theme.palette.m3error
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.pointSize: Theme.font.size.small
            }

            // ---------------------------------------------------------- actions
            Flow {
                Layout.fillWidth: true
                visible: actionsRepeater.count > 0
                spacing: Theme.spacing.extraSmall

                Repeater {
                    id: actionsRepeater

                    model: {
                        const list = [];
                        if (root.succeeded && !root.undone) {
                            if (root.kind === "files") {
                                if (root.outputPaths.length > 0) {
                                    list.push({
                                        id: "open",
                                        icon: "open_in_new",
                                        label: WT.tr(I18n, "hud.open"),
                                        primary: true
                                    });
                                    list.push({
                                        id: "reveal",
                                        icon: "folder_open",
                                        label: WT.tr(I18n, "hud.reveal"),
                                        iconOnly: true
                                    });
                                    list.push({
                                        id: "copy",
                                        icon: "content_copy",
                                        label: WT.tr(I18n, "hud.copy_files"),
                                        iconOnly: true
                                    });
                                }
                            } else if (root.kind === "text" || root.kind === "info" || root.kind === "qr" || root.kind === "share") {
                                list.push({
                                    id: "copy",
                                    icon: "content_copy",
                                    label: root.kind === "share" ? WT.tr(I18n, "hud.copy_link") : root.kind === "qr" ? WT.tr(I18n, "hud.copy_image") : root.kind === "text" ? WT.tr(I18n, "hud.copy_text") : WT.tr(I18n, "hud.copy"),
                                    primary: true
                                });
                                list.push({
                                    id: "expand",
                                    icon: "open_in_full",
                                    label: WT.tr(I18n, "hud.expand")
                                });
                                if (root.kind === "share" && !priv.shareStopped && String(root.result?.share_id ?? "") !== "")
                                    list.push({
                                        id: "stopShare",
                                        icon: "stop_circle",
                                        label: WT.tr(I18n, "hud.stop_share"),
                                        danger: true
                                    });
                            }
                            if (root.undoable)
                                list.push({
                                    id: "undo",
                                    icon: "undo",
                                    label: root.undoing ? WT.tr(I18n, "hud.undoing") : WT.tr(I18n, "hud.undo"),
                                    disabled: root.undoing
                                });
                        } else if (root.failedState) {
                            if (String(root.error?.install ?? "") !== "")
                                list.push({
                                    id: "copyInstall",
                                    icon: "download",
                                    label: WT.tr(I18n, "hud.copy_install"),
                                    primary: true
                                });
                            if (String(root.error?.details ?? "") !== "") {
                                list.push({
                                    id: "toggleDetails",
                                    icon: priv.showDetails ? "expand_less" : "expand_more",
                                    label: priv.showDetails ? WT.tr(I18n, "hud.hide_details") : WT.tr(I18n, "hud.details")
                                });
                                list.push({
                                    id: "copyDetails",
                                    icon: "content_copy",
                                    label: WT.tr(I18n, "hud.copy_details"),
                                    iconOnly: true
                                });
                            }
                        }
                        return list;
                    }

                    delegate: Item {
                        id: actionItem

                        required property var modelData

                        implicitWidth: actionItem.modelData.iconOnly ? iconBtn.implicitWidth : textBtn.implicitWidth
                        implicitHeight: actionItem.modelData.iconOnly ? iconBtn.implicitHeight : textBtn.implicitHeight
                        width: implicitWidth
                        height: implicitHeight

                        function trigger(): void {
                            switch (actionItem.modelData.id) {
                            case "open":
                                root.openPaths(root.outputPaths);
                                break;
                            case "reveal":
                                root.utilCall("reveal", {
                                    paths: root.outputPaths
                                }, "");
                                break;
                            case "copy":
                                root.copyResult();
                                break;
                            case "expand":
                                root.expandRequested(Jobs.get(root.jobId) ?? root.job);
                                break;
                            case "stopShare":
                                root.stopShare();
                                break;
                            case "undo":
                                Jobs.undo(root.jobId, null);
                                break;
                            case "copyInstall":
                                root.utilCall("copy_text", {
                                    text: String(root.error?.install ?? "")
                                }, WT.tr(I18n, "hud.install_copied"));
                                break;
                            case "toggleDetails":
                                priv.showDetails = !priv.showDetails;
                                break;
                            case "copyDetails":
                                root.copyText(String(root.error?.details ?? ""));
                                break;
                            }
                        }

                        TextButton {
                            id: textBtn

                            visible: !actionItem.modelData.iconOnly
                            text: String(actionItem.modelData.label ?? "")
                            icon: String(actionItem.modelData.icon ?? "")
                            type: actionItem.modelData.primary ? TextButton.Tonal : TextButton.Text
                            danger: actionItem.modelData.danger === true
                            disabled: actionItem.modelData.disabled === true
                            fontSize: Theme.font.size.smaller
                            horizontalPadding: Theme.padding.medium
                            verticalPadding: Theme.padding.extraSmall + Math.round(2 * Theme.scale)
                            inactiveOnColour: {
                                if (root.failedState && !actionItem.modelData.primary)
                                    return Theme.palette.m3onErrorContainer;
                                if (actionItem.modelData.danger === true)
                                    return Theme.palette.m3error;
                                return actionItem.modelData.primary ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3primary;
                            }
                            onClicked: actionItem.trigger()
                        }

                        IconButton {
                            id: iconBtn

                            visible: actionItem.modelData.iconOnly === true
                            type: IconButton.Text
                            icon: String(actionItem.modelData.icon ?? "")
                            iconSize: Theme.font.iconSize.medium
                            tooltip: String(actionItem.modelData.label ?? "")
                            inactiveOnColour: root.failedState ? Theme.palette.m3onErrorContainer : Theme.palette.m3onSurfaceVariant
                            onClicked: actionItem.trigger()
                        }
                    }
                }

            }
        }
    }
}
