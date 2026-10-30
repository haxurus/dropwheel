pragma ComponentBehavior: Bound

// DialogSurface — window-independent content of the Dropwheel dialog (DialogWindow wraps it in an Overlay
// PanelWindow): 45 % scrim, centred card (surfaceContainer, rounding.extraLarge, Elevation 3, open animation
// scale 0.92 → 1 + fade on the expressiveDefaultSpatial curve), header (× close at the left, centred title = node
// title / label, file name / count subtitle), the body chosen by node.dialog and a footer (Reset, hint, primary Apply with the
// action's label). Result dialogs (text / info / qr / share) bring their own actions.
//
//   readonly property string mode     "" (closed) | "editor" | "result"
//   readonly property bool active     a dialog is open (also while the close animation runs)
//   readonly property bool closing
//   readonly property bool shown      open animation target
//   readonly property var node, files, job
//   readonly property string kind     "form" | "crop" | "redact" | "trim" | "pages" | "metadata" | "adjust" |
//                                     "background" | "text" | "info" | "share"
//   readonly property Item editor     the loaded body (EditorBase or result view), null while closed
//   readonly property EditorBase editorItem   the loaded editor (null for result views)
//   readonly property bool pickerAvailable   zenity or kdialog is installed
//   signal closeFinished()            the close animation ended (the window can be hidden)
//   signal suspendRequested(bool suspended)   hide / show the window while an external file picker runs
//   signal jobStarted(string jobId)   Apply started a job (Jobs id, may be "local-N")
//
//   function open(node, files)        show the editor for node.dialog (null / "form" / unknown → ParamForm)
//   function showResult(job)          show a finished job's text / info / qr / share result (Jobs snapshot)
//   function reveal()                 start the open animation (call once the window is mapped)
//   function close()                  cancel with the close animation
//   function applyNow()               same as the Apply button
//   function pickFile(options, callback)   options {directory, title, start, filters: ["*.png"], filterName};
//                                     callback(path) — "" when cancelled or no picker is installed
//
// Keys: Esc = cancel / close; Enter (not in a multi-line field, nor in the editors' value / search fields, where it
// only commits) = Apply (closes a result); Ctrl+Enter = Apply from anywhere; Ctrl+Z = editor undo. Apply first
// commits the text field being typed in, or stops there when its text is invalid. Clicking the scrim cancels unless
// the editor has unsaved edits (the card shakes). The title is node.title ("<group> · <action>") or node.label,
// without a trailing "…"; the job runs on the editor's targetFiles().

import QtQuick
import Quickshell
import Quickshell.Io
import "../services"
import "../components"
import "DialogUtils.js" as DU

FocusScope {
    id: root

    readonly property string mode: priv.mode
    readonly property bool active: priv.mode !== ""
    readonly property bool closing: priv.closing
    readonly property bool shown: priv.shown
    readonly property var node: priv.node
    readonly property var files: priv.files
    readonly property var job: priv.job
    readonly property string kind: priv.kind
    readonly property Item editor: loader.item as Item
    readonly property EditorBase editorItem: loader.item as EditorBase
    readonly property bool pickerAvailable: priv.picker !== ""
    readonly property bool pickerRunning: pickerProc.running

    readonly property string title: {
        if (priv.mode === "result")
            return String(priv.job?.label ?? "") !== "" ? String(priv.job.label) : I18n.t("dialog.result.title");
        return priv.nodeTitle;
    }
    readonly property string subtitle: {
        if (priv.mode === "result") {
            const names = Array.isArray(priv.job?.files) ? priv.job.files : [];
            if (names.length === 1)
                return String(names[0]);
            return names.length > 1 ? I18n.tn("ui.files_count", names.length) : "";
        }
        const list = DU.normaliseFiles(priv.files);
        if (list.length === 1)
            return list[0].name;
        return list.length > 1 ? I18n.tn("ui.files_count", list.length) : "";
    }

    signal closeFinished
    signal suspendRequested(bool suspended)
    signal jobStarted(string jobId)

    function editorKind(n: var): string {
        const d = String(n?.dialog ?? "form");
        return ["crop", "redact", "trim", "pages", "metadata", "adjust", "background"].indexOf(d) !== -1 ? d : "form";
    }

    function resultKind(j: var): string {
        const k = String(j?.result?.kind ?? "text");
        if (k === "info")
            return "info";
        if (k === "share" || k === "qr")
            return "share";
        return "text";
    }

    function open(node: var, files: var): void {
        closeTimer.stop();
        loader.active = false;
        priv.closing = false;
        priv.node = (node && typeof node === "object") ? node : ({
                id: "",
                label: "",
                params: []
            });
        priv.files = files ?? [];
        priv.job = null;
        priv.kind = root.editorKind(priv.node);
        priv.mode = "editor";
        loader.active = true;
        loader.forceActiveFocus();
    }

    function showResult(job: var): void {
        closeTimer.stop();
        loader.active = false;
        priv.closing = false;
        priv.node = null;
        priv.files = [];
        priv.job = job ?? null;
        priv.kind = root.resultKind(priv.job);
        priv.mode = "result";
        loader.active = true;
        loader.forceActiveFocus();
    }

    function reveal(): void {
        if (priv.mode === "" || priv.closing)
            return;
        Qt.callLater(() => {
            if (priv.mode !== "" && !priv.closing)
                priv.shown = true;
        });
    }

    function close(): void {
        if (priv.mode === "" || priv.closing)
            return;
        priv.closing = true;
        priv.shown = false;
        closeTimer.interval = Math.max(1, Math.max(Theme.anim.durations.expressiveDefaultEffects, Theme.anim.durations.expressiveFastSpatial) + 20);
        closeTimer.restart();
    }

    function applyNow(): void {
        if (priv.mode !== "editor" || priv.closing)
            return;
        const ed = root.editorItem;
        if (!ed || ed.busy || priv.node?.available === false)
            return;
        // Text the field being typed in cannot accept stops here with its error: it must not be silently dropped.
        // (The form checks all its fields itself in requestApply.)
        const invalid = priv.kind === "form" ? null : priv.invalidTextField();
        if (invalid !== null) {
            invalid.validate();
            return;
        }
        // The field being typed in commits first: it may also be what makes the editor applicable.
        const field = priv.releaseTextFocus();
        if (ed.canApply)
            ed.requestApply();
        if (field !== null && priv.mode === "editor" && !priv.closing)
            field.forceActiveFocus();
    }

    function pickFile(options: var, callback: var): void {
        const cb = typeof callback === "function" ? callback : null;
        if (priv.picker === "" || pickerProc.running) {
            if (cb)
                Qt.callLater(() => cb(""));
            return;
        }
        const o = (options && typeof options === "object") ? options : {};
        const directory = o.directory === true;
        const title = String(o.title ?? "");
        const filters = Array.isArray(o.filters) ? o.filters.map(f => String(f)) : [];
        let start = String(o.start ?? "").trim();
        if (start === "") {
            const first = DU.normaliseFiles(priv.files)[0];
            const p = first?.path ?? "";
            start = p.lastIndexOf("/") > 0 ? p.slice(0, p.lastIndexOf("/") + 1) : String(Quickshell.env("HOME") ?? "/") + "/";
        }
        let argv;
        if (priv.picker === "zenity") {
            argv = ["zenity", "--file-selection"];
            if (title !== "")
                argv.push("--title=" + title);
            if (directory)
                argv.push("--directory");
            argv.push("--filename=" + start);
            if (!directory && filters.length > 0)
                argv.push("--file-filter=" + (String(o.filterName ?? "") !== "" ? String(o.filterName) : I18n.t("dialog.picker.files")) + " | " + filters.join(" "));
        } else {
            argv = ["kdialog"];
            if (title !== "")
                argv.push("--title", title);
            argv.push(directory ? "--getexistingdirectory" : "--getopenfilename");
            argv.push(start);
            if (!directory && filters.length > 0)
                argv.push(filters.join(" "));
        }
        priv.pickCallback = cb;
        priv.pickExit = -1;
        priv.pickStarted = false;
        priv.picking = true;
        pickerProc.command = argv;
        root.suspendRequested(true);
        pickerProc.running = true;
        // A picker that cannot even be started reports no exit: pickDone() settles it after a grace period.
        Qt.callLater(priv.pickDone);
    }

    focus: true

    Keys.onPressed: event => {
        if (priv.mode === "" || priv.closing)
            return;
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
        if (event.key === Qt.Key_Escape) {
            root.close();
            event.accepted = true;
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (priv.mode === "editor")
                root.applyNow();
            else
                root.close();
            event.accepted = true;
        } else if (event.key === Qt.Key_Z && ctrl && !(event.modifiers & Qt.ShiftModifier)) {
            const ed = root.editorItem;
            if (ed)
                ed.undo();
            event.accepted = true;
        }
    }

    Shortcut {
        sequences: ["Ctrl+Return", "Ctrl+Enter"]
        enabled: priv.mode === "editor" && !priv.closing
        onActivated: root.applyNow()
    }

    // Takes the focus for a moment so the text field being edited commits (priv.releaseTextFocus).
    Item {
        id: focusSink
    }

    Component.onCompleted: probe.running = true

    QtObject {
        id: priv

        property string mode: ""
        property bool closing: false
        property bool shown: false
        property var node: null
        property var files: []
        property var job: null
        property string kind: ""
        property string picker: ""
        property var pickCallback: null
        property int pickExit: -1
        property bool pickStarted: false
        property bool picking: false
        property real shake: 0

        readonly property real margin: Theme.s(32)
        readonly property real headerHeight: Theme.s(64)
        readonly property real footerHeight: footer.visible ? Theme.s(68) : Theme.padding.large
        readonly property real bodyMargin: Theme.padding.large
        readonly property real cardMaxW: Math.max(Theme.s(320), root.width - margin * 2)
        readonly property real cardMaxH: Math.max(Theme.s(240), root.height - margin * 2)
        readonly property real wantW: (root.editor ? root.editor.implicitWidth : Theme.s(480)) + bodyMargin * 2
        readonly property real wantH: headerHeight + (root.editor ? root.editor.implicitHeight : Theme.s(200)) + footerHeight
        readonly property bool unavailable: priv.mode === "editor" && priv.node?.available === false
        // Menu labels may end with "…" ("Rinomina…"): not in the title of the dialog they open. `title` (the
        // backend's "<group> · <action>" label) gives the leaf label its context ("Converti · JPG").
        readonly property string nodeTitle: {
            const t = String(priv.node?.title ?? "");
            return DU.stripEllipsis(t !== "" ? t : String(priv.node?.label ?? ""));
        }

        function runJob(params: var): void {
            if (priv.mode !== "editor" || priv.closing)
                return;
            const id = String(priv.node?.id ?? "");
            if (id === "") {
                root.close();
                return;
            }
            const ed = root.editorItem;
            const chosen = ed ? ed.targetFiles() : null;
            const list = Array.isArray(chosen) ? chosen : DU.normaliseFiles(priv.files);
            const targets = list.map(f => DU.jobTarget(f)).filter(p => p !== "");
            const jobId = Jobs.run(id, targets, (params && typeof params === "object") ? params : {}, priv.nodeTitle);
            root.jobStarted(jobId);
            root.close();
        }

        // Clicking Apply never moves the focus and Ctrl+Enter is a shortcut, so a text field still being typed in has
        // not emitted editingFinished (crop / trim fields commit on it). Moving the focus away commits it now; the
        // field is returned so it gets the focus back when the dialog stays open.
        function releaseTextFocus(): var {
            const item = priv.focusItem();
            const ed = root.editor;
            if (!item || !ed || item.cursorPosition === undefined || typeof item.editingFinished !== "function")
                return null;
            let p = item.parent;
            while (p && p !== ed)
                p = p.parent;
            if (p !== ed)
                return null;
            focusSink.forceActiveFocus();
            return item;
        }

        function focusItem(): var {
            return root.Window.activeFocusItem;
        }

        // A text field still being typed in commits before the reset: it would keep its text and commit it after.
        // (The form's fields commit as you type and are created again by its reset.)
        function resetEditor(): void {
            const ed = root.editorItem;
            if (!ed)
                return;
            const field = priv.kind === "form" ? null : priv.releaseTextFocus();
            ed.reset();
            if (field !== null)
                field.forceActiveFocus();
        }

        // The M3TextField being typed in when its text does not pass its validation, else null.
        function invalidTextField(): var {
            const ed = root.editor;
            let p = priv.focusItem();
            while (p && p !== ed) {
                if (p.valid === false && typeof p.validate === "function")
                    return p;
                p = p.parent;
            }
            return null;
        }

        function scrimClicked(): void {
            if (priv.closing)
                return;
            if (priv.mode === "editor" && root.editorItem?.dirty === true) {
                shakeAnim.restart();
                return;
            }
            root.close();
        }

        // Runs (deferred) after the picker process changed state, whatever the order of exited / runningChanged /
        // streamFinished: by then the exit code and the collected stdout are final. A process that is not running
        // and never started is either still pending (Process may defer the start) or failed to start: the grace
        // timer decides, so the hidden dialog window always comes back.
        function pickDone(): void {
            if (!priv.picking || pickerProc.running)
                return;
            if (!priv.pickStarted && priv.pickExit < 0) {
                if (!pickGrace.running)
                    pickGrace.restart();
                return;
            }
            pickGrace.stop();
            priv.picking = false;
            priv.finishPick();
        }

        function finishPick(): void {
            const cb = priv.pickCallback;
            priv.pickCallback = null;
            let path = "";
            if (priv.pickStarted && priv.pickExit === 0) {
                const lines = String(pickerOut.text ?? "").split("\n").map(l => l.trim()).filter(l => l !== "");
                path = lines.length > 0 ? lines[0] : "";
            }
            root.suspendRequested(false);
            if (cb) {
                try {
                    cb(path);
                } catch (e) {
                    console.warn("DialogSurface: picker callback threw:", e);
                }
            }
        }
    }

    Timer {
        id: closeTimer

        onTriggered: {
            loader.active = false;
            priv.mode = "";
            priv.kind = "";
            priv.node = null;
            priv.files = [];
            priv.job = null;
            priv.closing = false;
            root.closeFinished();
        }
    }

    // ------------------------------------------------------------------ scrim
    Rectangle {
        id: scrim

        anchors.fill: parent
        // hyprpicker samples the composited screen: no scrim while it runs, or every colour outside the card comes out
        // darkened. No Behavior: hyprpicker starts Hypr.pickColorDelayMs later and must find the scrim already gone.
        color: Qt.alpha(Theme.palette.m3scrim, Hypr.colorPicking ? 0 : 0.45)
        opacity: priv.shown ? 1 : 0
        visible: opacity > 0

        Behavior on opacity {
            Anim {
                type: Anim.DefaultEffects
            }
        }

        MouseArea {
            anchors.fill: parent
            enabled: priv.mode !== "" && !priv.closing
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onClicked: priv.scrimClicked()
            onWheel: event => event.accepted = true
        }
    }

    // ------------------------------------------------------------------ card
    Elevation {
        x: card.x
        y: card.y
        width: card.width
        height: card.height
        radius: card.radius
        level: 3
        opacity: card.opacity
        scale: card.scale
        visible: card.visible
        transform: Translate {
            x: priv.shake
        }
    }

    StyledRect {
        id: card

        width: Math.round(Math.min(priv.cardMaxW, Math.max(Theme.s(360), priv.wantW)))
        height: Math.round(Math.min(priv.cardMaxH, Math.max(Theme.s(200), priv.wantH)))
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        radius: Theme.rounding.extraLarge
        color: Theme.palette.m3surfaceContainer
        // Visible as soon as a dialog is open (not only once faded in): invisible items cannot hold active focus.
        visible: priv.mode !== ""
        opacity: priv.shown ? 1 : 0
        scale: priv.shown ? 1 : 0.92
        transformOrigin: Item.Center
        transform: Translate {
            x: priv.shake
        }

        Behavior on opacity {
            Anim {
                type: Anim.DefaultEffects
            }
        }
        Behavior on scale {
            Anim {
                type: Anim.DefaultSpatial
            }
        }
        Behavior on height {
            enabled: priv.shown && !priv.closing
            Anim {
                type: Anim.DefaultEffects
            }
        }
        Behavior on width {
            enabled: priv.shown && !priv.closing
            Anim {
                type: Anim.DefaultEffects
            }
        }

        // Swallow clicks on the card background so they never reach the scrim.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onWheel: event => event.accepted = true
        }

        // ---------------------------------------------------------- header
        Item {
            id: header

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: priv.headerHeight

            IconButton {
                id: closeButton

                anchors.left: parent.left
                anchors.leftMargin: Theme.padding.medium
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Text
                icon: "close"
                iconSize: Theme.font.iconSize.large
                tooltip: priv.mode === "editor" ? I18n.t("ui.cancel") : I18n.t("ui.close")
                onClicked: root.close()
            }

            Column {
                anchors.centerIn: parent
                width: Math.max(0, parent.width - (closeButton.width + Theme.padding.medium + Theme.spacing.medium) * 2)
                spacing: Math.round(2 * Theme.scale)

                StyledText {
                    width: parent.width
                    text: root.title
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    font.pointSize: Theme.font.size.larger
                    font.weight: Font.Medium
                }

                StyledText {
                    width: parent.width
                    visible: text !== ""
                    text: root.subtitle
                    elide: Text.ElideMiddle
                    horizontalAlignment: Text.AlignHCenter
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }
            }

            MaterialIcon {
                id: infoIcon

                anchors.right: parent.right
                anchors.rightMargin: Theme.padding.large
                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: 1
                visible: priv.mode === "editor" && priv.kind !== "form" && String(priv.node?.description ?? "") !== ""
                text: "info"
                size: Theme.font.iconSize.medium
                color: Theme.palette.m3onSurfaceVariant

                Tooltip {
                    text: String(priv.node?.description ?? "")
                    position: Tooltip.Bottom
                }
            }
        }

        // ---------------------------------------------------------- body
        Item {
            id: body

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: header.bottom
            anchors.bottom: footer.visible ? footer.top : parent.bottom
            anchors.leftMargin: priv.bodyMargin
            anchors.rightMargin: priv.bodyMargin
            anchors.bottomMargin: footer.visible ? 0 : Theme.padding.large
            clip: true

            Loader {
                id: loader

                anchors.fill: parent
                active: false
                focus: true
                sourceComponent: {
                    switch (priv.kind) {
                    case "crop":
                        return cropComp;
                    case "redact":
                        return redactComp;
                    case "trim":
                        return trimComp;
                    case "pages":
                        return pagesComp;
                    case "metadata":
                        return metadataComp;
                    case "adjust":
                        return adjustComp;
                    case "background":
                        return backgroundComp;
                    case "text":
                        return textResultComp;
                    case "info":
                        return infoResultComp;
                    case "share":
                        return shareResultComp;
                    case "form":
                        return formComp;
                    default:
                        return null;
                    }
                }
            }

            Connections {
                target: loader.item
                ignoreUnknownSignals: true

                function onApply(params: var): void {
                    priv.runJob(params);
                }

                function onCancel(): void {
                    root.close();
                }

                function onCloseRequested(): void {
                    root.close();
                }
            }
        }

        // ---------------------------------------------------------- footer
        Item {
            id: footer

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: Theme.s(68)
            visible: priv.mode === "editor" && (root.editorItem?.showFooter ?? true)

            TextButton {
                id: resetButton

                anchors.left: parent.left
                anchors.leftMargin: Theme.padding.large
                anchors.verticalCenter: parent.verticalCenter
                visible: root.editorItem?.resettable ?? false
                type: ButtonBase.Tonal
                icon: "restart_alt"
                text: I18n.t("ui.reset")
                disabled: root.editorItem?.busy === true
                onClicked: priv.resetEditor()
            }

            StyledText {
                anchors.left: resetButton.visible ? resetButton.right : parent.left
                anchors.right: applyButton.left
                anchors.leftMargin: resetButton.visible ? Theme.spacing.medium : Theme.padding.large
                anchors.rightMargin: Theme.spacing.medium
                anchors.verticalCenter: parent.verticalCenter
                text: priv.unavailable ? String(priv.node?.hint ?? "") || I18n.t("ui.unavailable") : String(root.editorItem?.footerHint ?? "")
                elide: Text.ElideRight
                maximumLineCount: 2
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                font.pointSize: Theme.font.size.small
                color: priv.unavailable || (root.editorItem?.footerHintError === true) ? Theme.palette.m3error : Theme.palette.m3onSurfaceVariant
            }

            TextButton {
                id: applyButton

                anchors.right: parent.right
                anchors.rightMargin: Theme.padding.large
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Filled
                icon: root.editorItem?.busy === true ? "hourglass_top" : String(root.editorItem?.applyIcon ?? "check")
                text: {
                    const own = String(root.editorItem?.applyLabel ?? "");
                    if (own !== "")
                        return own;
                    const lbl = DU.stripEllipsis(String(priv.node?.label ?? ""));
                    return lbl !== "" ? lbl : I18n.t("ui.apply");
                }
                disabled: priv.unavailable || root.editorItem === null || root.editorItem?.canApply === false || root.editorItem?.busy === true
                onClicked: root.applyNow()
            }
        }
    }

    SequentialAnimation {
        id: shakeAnim

        Anim {
            target: priv
            property: "shake"
            to: Theme.s(10)
            type: Anim.FastEffects
            duration: Math.max(1, Theme.anim.durations.expressiveFastEffects / 2)
        }
        Anim {
            target: priv
            property: "shake"
            to: -Theme.s(8)
            type: Anim.FastEffects
            duration: Math.max(1, Theme.anim.durations.expressiveFastEffects / 2)
        }
        Anim {
            target: priv
            property: "shake"
            to: Theme.s(5)
            type: Anim.FastEffects
            duration: Math.max(1, Theme.anim.durations.expressiveFastEffects / 2)
        }
        Anim {
            target: priv
            property: "shake"
            to: 0
            type: Anim.FastEffects
            duration: Math.max(1, Theme.anim.durations.expressiveFastEffects / 2)
        }
    }

    // ------------------------------------------------------------------ bodies
    Component {
        id: formComp

        ParamForm {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: cropComp

        CropEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: redactComp

        RedactEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: trimComp

        TrimEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: pagesComp

        PageOrganizer {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: metadataComp

        MetadataEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: adjustComp

        AdjustEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: backgroundComp

        BackgroundEditor {
            node: priv.node
            files: priv.files
            host: root
        }
    }

    Component {
        id: textResultComp

        TextResult {
            job: priv.job
            host: root
        }
    }

    Component {
        id: infoResultComp

        InfoResult {
            job: priv.job
            host: root
        }
    }

    Component {
        id: shareResultComp

        ShareResult {
            job: priv.job
            host: root
        }
    }

    // ------------------------------------------------------------------ external file picker (zenity / kdialog)
    Process {
        id: probe

        command: ["sh", "-c", "command -v zenity >/dev/null 2>&1 && echo zenity; command -v kdialog >/dev/null 2>&1 && echo kdialog; true"]
        stdout: StdioCollector {
            id: probeOut

            onStreamFinished: {
                const found = String(probeOut.text ?? "").split("\n").map(l => l.trim()).filter(l => l === "zenity" || l === "kdialog");
                const desktop = String(Quickshell.env("XDG_CURRENT_DESKTOP") ?? "").toUpperCase();
                if (found.length === 0)
                    priv.picker = "";
                else if (desktop.indexOf("KDE") !== -1 && found.indexOf("kdialog") !== -1)
                    priv.picker = "kdialog";
                else
                    priv.picker = found[0];
            }
        }
    }

    Timer {
        id: pickGrace

        interval: 600
        onTriggered: {
            if (priv.picking && !pickerProc.running && !priv.pickStarted) {
                priv.picking = false;
                priv.finishPick();
            }
        }
    }

    Process {
        id: pickerProc

        stdout: StdioCollector {
            id: pickerOut
        }
        onStarted: priv.pickStarted = true
        onExited: (exitCode, exitStatus) => {
            priv.pickExit = exitCode;
            Qt.callLater(priv.pickDone);
        }
        onRunningChanged: {
            if (!pickerProc.running)
                Qt.callLater(priv.pickDone);
        }
    }
}
