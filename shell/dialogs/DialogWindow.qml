pragma ComponentBehavior: Bound

// DialogWindow — the Dropwheel dialog: a full-screen Overlay layer (namespace "dropwheel-dialog", exclusion Ignore,
// exclusive keyboard focus, transparent) on the screen under the cursor, hosting DialogSurface (scrim + card +
// editor). Hidden (no surface) while closed; the surface keeps its state while an external file picker temporarily
// hides the window.
//
//   readonly property bool isOpen        a dialog is shown (or closing)
//   readonly property bool closing
//   readonly property DialogSurface surface
//   signal closed()                      after the close animation (cancel, Esc, Apply, result closed)
//   signal jobStarted(string jobId)      Apply started a job through Jobs.run
//   function open(node, files, options)  editor for node.dialog (null / "form" → ParamForm); files = FileInfo JSON
//                                        objects or paths; options {screen?: ShellScreen, x?, y?: global logical px}
//                                        (default: the screen under the cursor, via hyprctl cursorpos)
//   function showResult(job, options)    text / info / qr / share result of a finished job (Jobs snapshot)
//   function close()                     cancel / close with the animation
//
//   shell.qml:  DialogWindow { id: dialogs }   …   dialogs.open(node, menu.files)

import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Hyprland
import "../services"

PanelWindow {
    id: root

    readonly property bool isOpen: dialogSurface.active
    readonly property bool closing: dialogSurface.closing
    readonly property DialogSurface surface: dialogSurface

    signal closed
    signal jobStarted(string jobId)

    function open(node: var, files: var, options: var): void {
        dialogSurface.open(node, files);
        priv.present(options);
    }

    function showResult(job: var, options: var): void {
        dialogSurface.showResult(job);
        priv.present(options);
    }

    function close(): void {
        dialogSurface.close();
    }

    visible: false
    color: "transparent"
    anchors.top: true
    anchors.bottom: true
    anchors.left: true
    anchors.right: true
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dropwheel-dialog"
    WlrLayershell.keyboardFocus: dialogSurface.active && !dialogSurface.closing ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    mask: dialogSurface.closing ? emptyRegion : null

    Region {
        id: emptyRegion
    }

    QtObject {
        id: priv

        property int ticket: 0
        property bool suspended: false

        function present(options: var): void {
            const o = (options && typeof options === "object") ? options : {};
            const t = ++priv.ticket;
            if (root.visible) {
                // Already mapped (e.g. a result replacing a dialog): keep the screen.
                dialogSurface.reveal();
                return;
            }
            if (o.screen) {
                priv.show(o.screen);
                return;
            }
            if (typeof o.x === "number" && typeof o.y === "number" && isFinite(o.x) && isFinite(o.y)) {
                priv.show(Hypr.screenAt(o.x, o.y));
                return;
            }
            Hypr.cursorPos(pos => {
                if (t !== priv.ticket || !dialogSurface.active || dialogSurface.closing)
                    return;
                let s = pos ? Hypr.screenAt(pos.x, pos.y) : null;
                if (!s)
                    s = Hypr.screenByName(String(Hyprland.focusedMonitor?.name ?? ""));
                if (!s && Quickshell.screens.length > 0)
                    s = Quickshell.screens[0];
                priv.show(s);
            });
        }

        function show(screen: var): void {
            if (screen && root.screen !== screen)
                root.screen = screen;
            root.visible = true;
            dialogSurface.forceActiveFocus();
            dialogSurface.reveal();
        }
    }

    DialogSurface {
        id: dialogSurface

        anchors.fill: parent
        focus: true

        onCloseFinished: {
            priv.ticket++;
            priv.suspended = false;
            root.visible = false;
            root.closed();
        }
        onJobStarted: jobId => root.jobStarted(jobId)
        onSuspendRequested: suspended => {
            if (!dialogSurface.active)
                return;
            priv.suspended = suspended;
            root.visible = !suspended;
            if (!suspended)
                dialogSurface.forceActiveFocus();
        }
    }
}
