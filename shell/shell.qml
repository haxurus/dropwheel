//@ pragma ShellId dropwheel
//@ pragma Env QS_NO_RELOAD_POPUP=1
//@ pragma DefaultEnv QSG_RENDER_LOOP=threaded
//@ pragma DefaultEnv QT_QUICK_FLICKABLE_WHEEL_DECELERATION=10000
pragma ComponentBehavior: Bound

// Dropwheel shell — run with `qs -p <shelldir> -n -d` (see `dropwheel start`). It hosts the WheelController:
//
// TRIGGERS (SPEC §2.1)
//   GlobalShortcut dropwheel:shift  Shift during a drag: `hyprctl -j monitors` + `cursorpos` (Hypr.queryDrag); when
//                                   some monitor's solitaryBlockedBy contains "DND" the wheel opens in drag mode
//                                   centred on the cursor. With a drag-mode wheel open, Shift switches the page.
//                                   trigger.dnd_check = false skips the check (the wheel closes after 1200 ms if no
//                                   drag enters it, or 1.5 s after the drag left it); trigger.shift = false disables
//                                   the trigger.
//   GlobalShortcut dropwheel:open   click mode at the cursor with the files in the clipboard (or an empty drop
//                                   target); pressing it again closes a click-mode wheel.
//   HotZone (per screen)            drag enter → drag-mode wheel next to the hot zone; drop on it → click mode.
//   IpcHandler "dropwheel"          open(payload) openAtCursor() close() setHotzone(enabled) reload() ping()
// Hyprland may deliver a bind's `global` on press, on release (chord resolver) or both: the first signal of a
// press/release pair acts, the other is ignored, and triggers closer than 250 ms are debounced.
//
// CONTROLLER FUNCTIONS (SPEC §6.3)
//   openWheel({files, menu, mode: "drag" | "click", x, y, screen})   x/y global logical px (default: screen centre)
//   closeWheel(toHud)        close (toHud: shrink towards the HUD after a run)
//   openDialog(node, files)  DialogWindow.open(node, files) on the wheel's screen
//   showResult(job)          DialogWindow.showResult(job) (HUD "Expand")
//   runNode(node, options)   instant / quick → Jobs.run (wheel shrinks into the HUD); dialog, options, or a param
//                            that needs input (required without default, or `locked`) → dialog / parameter form
// The menu for the files on the wheel is requested with Backend.menu (spinner while pending, error state with retry)
// and cached for 15 s. While a drag-mode wheel is open, Hypr.watchDrag polls every 300 ms (paused while the drag is
// over the wheel) and closes the wheel when the drag ends (drop elsewhere, Escape).

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import "services"
import "wheel"
import "hud"
import "dialogs"
import "wheel/WheelText.js" as WT

ShellRoot {
    id: root

    // ------------------------------------------------------------------ configuration
    readonly property var cfg: Backend.config ?? ({})
    readonly property bool shiftEnabled: root.cfg?.trigger?.shift !== false
    readonly property bool dndCheck: root.cfg?.trigger?.dnd_check !== false
    property var hotzoneOverride: null
    readonly property bool hotzoneEnabled: root.hotzoneOverride !== null ? root.hotzoneOverride === true : root.cfg?.hotzone?.enabled !== false
    readonly property string hotzoneCorner: String(root.cfg?.hotzone?.corner ?? "top-right")
    readonly property int hotzoneSize: root.clampInt(root.cfg?.hotzone?.size, 6, 1, 64)
    readonly property int springMs: root.clampInt(root.cfg?.wheel?.spring_ms, 450, 100, 3000)

    // ------------------------------------------------------------------ wheel state
    property bool wheelOpen: false
    property string wheelMode: "drag"
    property var wheelFiles: []
    property var wheelMenu: null
    property bool menuLoading: false
    property string menuError: ""
    property string menuErrorDetails: ""
    property string menuKey: ""
    property string menuContext: "drag"
    property real menuAt: 0
    property int menuTicket: 0
    property bool emptyTarget: false
    property var wheelScreen: null
    property var hudScreen: null
    property int session: 0
    property int openTicket: 0
    property bool queryPending: false
    property var dragWatch: null
    property var shortcutState: ({
            shift: {
                lastAt: 0,
                pressAt: 0
            },
            open: {
                lastAt: 0,
                pressAt: 0
            }
        })

    readonly property var focusedScreen: {
        const byName = Hypr.screenByName(String(Hyprland.focusedMonitor?.name ?? ""));
        if (byName)
            return byName;
        const list = Quickshell.screens;
        return (list?.length ?? 0) > 0 ? list[0] : null;
    }

    settings.watchFiles: Quickshell.env("DROPWHEEL_DEV") === "1"

    function clampInt(value: var, fallback: int, min: int, max: int): int {
        const n = Number(value);
        if (value === undefined || value === null || value === "" || !isFinite(n))
            return fallback;
        return Math.max(min, Math.min(max, Math.round(n)));
    }

    function wheelWindow(): WheelWindow {
        return wheelLoader.item as WheelWindow;
    }

    function dialogWindow(): DialogWindow {
        return dialogLoader.item as DialogWindow;
    }

    // ------------------------------------------------------------------ triggers
    // Returns true when this press/release signal should act (see the header).
    function shortcutShouldFire(kind: string, pressed: bool): bool {
        const st = root.shortcutState[kind];
        const now = Date.now();
        if (pressed) {
            st.pressAt = now;
        } else if (st.pressAt > 0) {
            // The release of a press that was already handled, however long the key was held. Only a release with
            // no recorded press (a bind Hyprland deferred to the key release) acts.
            st.pressAt = 0;
            return false;
        }
        if (now - st.lastAt < 250)
            return false;
        st.lastAt = now;
        return true;
    }

    function onShift(): void {
        if (!root.shiftEnabled)
            return;
        const w = root.wheelWindow();
        if (root.wheelOpen && w && !w.closing) {
            // In click mode the wheel has the keyboard and handles Shift (page switch) itself.
            if (root.wheelMode === "drag")
                w.wheel.switchPage(1);
            return;
        }
        if (root.queryPending)
            return;
        root.queryPending = true;
        if (!root.dndCheck) {
            Hypr.cursorPos(pos => {
                root.queryPending = false;
                if (!pos)
                    return;
                root.openWheel({
                    mode: "drag",
                    x: pos.x,
                    y: pos.y
                });
                probeTimer.restart();
            });
            return;
        }
        Hypr.queryDrag((dragging, cursor) => {
            root.queryPending = false;
            if (!dragging || !cursor)
                return;
            if (root.wheelOpen && root.wheelWindow() && !root.wheelWindow().closing)
                return;
            root.openWheel({
                mode: "drag",
                x: cursor.x,
                y: cursor.y
            });
        });
    }

    function openClipboardAtCursor(toggle: bool): void {
        const w = root.wheelWindow();
        if (toggle && root.wheelOpen && w && !w.closing && root.wheelMode === "click") {
            root.closeWheel(false);
            return;
        }
        const ticket = ++root.openTicket;
        Hypr.cursorPos(pos => {
            if (ticket !== root.openTicket)
                return;
            Backend.clipboardFiles((result, error) => {
                if (ticket !== root.openTicket)
                    return;
                const list = error ? [] : Backend.toPaths(result?.files ?? []);
                const opts = {
                    mode: "click",
                    files: list
                };
                if (pos) {
                    opts.x = pos.x;
                    opts.y = pos.y;
                }
                root.openWheel(opts);
            });
        });
    }

    function openFromPayload(payload: string): void {
        const text = String(payload ?? "").trim();
        let data = null;
        if (text !== "") {
            try {
                data = JSON.parse(text);
            } catch (e) {
                data = null;
            }
        }
        let files = [];
        let x = null;
        let y = null;
        if (data && typeof data === "object" && !Array.isArray(data)) {
            files = Array.isArray(data.files) ? data.files : (typeof data.files === "string" ? [data.files] : []);
            if (typeof data.x === "number" && typeof data.y === "number" && isFinite(data.x) && isFinite(data.y)) {
                x = data.x;
                y = data.y;
            }
        } else if (Array.isArray(data)) {
            files = data;
        } else if (text !== "") {
            // Not JSON: newline-separated paths / URIs.
            files = text.split("\n");
        }
        files = files.map(f => String(f ?? "").trim()).filter(f => f !== "");
        const ticket = ++root.openTicket;
        if (x !== null) {
            root.openWheel({
                mode: "click",
                files: files,
                x: x,
                y: y
            });
            return;
        }
        Hypr.cursorPos(pos => {
            if (ticket !== root.openTicket)
                return;
            const opts = {
                mode: "click",
                files: files
            };
            if (pos) {
                opts.x = pos.x;
                opts.y = pos.y;
            }
            root.openWheel(opts);
        });
    }

    function hotzoneEntered(files: var, at: point, screen: var): void {
        if (root.wheelOpen)
            return;
        root.openWheel({
            mode: "drag",
            files: files,
            x: at.x,
            y: at.y,
            screen: screen,
            cursorKnown: false
        });
    }

    function hotzoneDropped(files: var, at: point, screen: var): void {
        const w = root.wheelWindow();
        if (root.wheelOpen && w && !w.closing && root.wheelMode === "drag") {
            // The wheel opened by the drag enter is already next to the hot zone: keep it, now in click mode.
            root.setFiles(files, "click");
            root.switchToClick();
            return;
        }
        root.openWheel({
            mode: "click",
            files: files,
            x: at.x,
            y: at.y,
            screen: screen,
            cursorKnown: false
        });
    }

    // ------------------------------------------------------------------ wheel controller
    function openWheel(opts: var): void {
        const o = (opts && typeof opts === "object") ? opts : {};
        const w = root.wheelWindow();
        if (!w) {
            console.warn("dropwheel: the wheel window is not available");
            return;
        }
        const mode = o.mode === "click" ? "click" : "drag";
        const files = Backend.toPaths(o.files ?? []);
        const hasPoint = typeof o.x === "number" && typeof o.y === "number" && isFinite(o.x) && isFinite(o.y);
        let screen = o.screen ?? null;
        if (!screen)
            screen = hasPoint ? Hypr.screenAt(o.x, o.y) : root.focusedScreen;
        let x = hasPoint ? o.x : 0;
        let y = hasPoint ? o.y : 0;
        if (!hasPoint && screen) {
            x = Number(screen.x ?? 0) + Number(screen.width ?? 0) / 2;
            y = Number(screen.y ?? 0) + Number(screen.height ?? 0) / 2;
        }

        root.session++;
        // This open supersedes any request still waiting for a cursor or clipboard reply.
        root.openTicket++;
        probeTimer.stop();
        root.stopDragWatch();
        root.wheelMode = mode;
        root.wheelScreen = screen;
        root.hudScreen = screen;
        root.wheelFiles = files;
        root.emptyTarget = mode === "click" && files.length === 0 && !o.menu;
        if (o.menu && typeof o.menu === "object") {
            root.menuTicket++;
            root.menuLoading = false;
            root.menuError = "";
            root.menuErrorDetails = "";
            root.menuKey = mode + "\n" + files.join("\n");
            root.menuContext = mode;
            root.menuAt = Date.now();
            root.wheelMenu = o.menu;
        } else if (files.length > 0) {
            root.loadMenu(files, mode);
        } else {
            root.clearMenu();
        }
        root.wheelOpen = true;
        w.present(screen, x, y, hasPoint && o.cursorKnown !== false);
        // Without the DnD check Hyprland's DND state is not trusted: the probe / exit timers close the wheel instead.
        if (mode === "drag" && root.dndCheck)
            root.startDragWatch();
    }

    function closeWheel(toHud: bool): void {
        probeTimer.stop();
        exitTimer.stop();
        root.stopDragWatch();
        root.openTicket++;
        const w = root.wheelWindow();
        if (!w || !root.wheelOpen) {
            root.wheelOpen = false;
            return;
        }
        let dx = 0;
        let dy = 0;
        if (toHud) {
            const c = hud.centre;
            dx = c.x - w.cursorX;
            dy = c.y - w.cursorY;
        }
        w.dismiss(toHud, dx, dy);
    }

    function openDialog(node: var, files: var): void {
        const d = root.dialogWindow();
        if (!d) {
            console.warn("dropwheel: the dialog window is not available");
            return;
        }
        const screen = root.wheelScreen ?? root.focusedScreen;
        d.open(node, files, screen ? {
            screen: screen
        } : {});
    }

    function showResult(job: var): void {
        const d = root.dialogWindow();
        if (!d || !job)
            return;
        const screen = root.hudScreen ?? root.focusedScreen;
        d.showResult(job, screen ? {
            screen: screen
        } : {});
    }

    function runFiles(): var {
        const infos = root.wheelMenu?.files;
        if (Array.isArray(infos) && infos.length > 0) {
            const out = infos.map(f => String(f?.path || f?.uri || "")).filter(p => p !== "");
            if (out.length > 0)
                return out;
        }
        return root.wheelFiles.slice();
    }

    // An instant / quick action that cannot run with its defaults: a required param without a default, or a param
    // the backend marked `locked` (e.g. the password of a protected PDF) → the parameter form opens instead.
    function needsInput(node: var): bool {
        if (node?.locked === true && Array.isArray(node?.params) && node.params.length > 0)
            return true;
        const params = Array.isArray(node?.params) ? node.params : [];
        return params.some(p => {
            if (!p || typeof p !== "object")
                return false;
            if (p.locked === true)
                return true;
            const d = p.default;
            return p.required === true && (d === null || d === undefined || String(d) === "");
        });
    }

    function runNode(node: var, options: bool): void {
        if (!node || node.type !== "action" || String(node.id ?? "") === "")
            return;
        const files = root.runFiles();
        if (files.length === 0)
            return;
        if (options || node.mode === "dialog" || root.needsInput(node)) {
            const infos = Array.isArray(root.wheelMenu?.files) && root.wheelMenu.files.length > 0 ? root.wheelMenu.files : files;
            const target = node.mode === "dialog" || options ? node : Object.assign({}, node, {
                dialog: "form"
            });
            root.closeWheel(false);
            root.openDialog(target, infos);
            return;
        }
        Jobs.run(String(node.id), files, {}, String(node.label ?? ""));
        root.closeWheel(true);
    }

    function setFiles(files: var, context: string): void {
        const paths = Backend.toPaths(files);
        const same = paths.length === root.wheelFiles.length && paths.every((p, i) => p === root.wheelFiles[i]);
        root.emptyTarget = false;
        if (same && paths.length > 0 && (root.wheelMenu !== null || root.menuLoading))
            return; // already shown (e.g. the drop that follows the drag enter)
        root.wheelFiles = paths;
        if (paths.length > 0)
            root.loadMenu(paths, context);
        else
            root.clearMenu();
    }

    function switchToClick(): void {
        probeTimer.stop();
        exitTimer.stop();
        root.stopDragWatch();
        root.wheelMode = "click";
        const w = root.wheelWindow();
        if (w)
            Qt.callLater(w.focusWheel);
    }

    function clearMenu(): void {
        root.menuTicket++;
        root.menuKey = "";
        root.wheelMenu = null;
        root.menuLoading = false;
        root.menuError = "";
        root.menuErrorDetails = "";
    }

    function loadMenu(files: var, context: string): void {
        const key = context + "\n" + files.join("\n");
        if (key === root.menuKey) {
            if (root.menuLoading)
                return;
            if (root.wheelMenu !== null && root.menuError === "" && Date.now() - root.menuAt < 15000)
                return;
        }
        root.menuKey = key;
        root.menuContext = context;
        root.wheelMenu = null;
        root.menuError = "";
        root.menuErrorDetails = "";
        root.menuLoading = true;
        const ticket = ++root.menuTicket;
        Backend.menu(files, context, (result, error) => {
            if (ticket !== root.menuTicket)
                return;
            root.menuLoading = false;
            if (error || !result || typeof result !== "object" || Array.isArray(result)) {
                const msg = String(error?.message ?? "");
                root.menuError = msg !== "" ? msg : WT.tr(I18n, "wheel.menu_error");
                root.menuErrorDetails = String(error?.details ?? "");
                return;
            }
            root.menuAt = Date.now();
            root.wheelMenu = result;
        });
    }

    function retryMenu(): void {
        if (root.wheelFiles.length === 0)
            return;
        root.menuKey = "";
        root.loadMenu(root.wheelFiles, root.menuContext);
    }

    function startDragWatch(): void {
        root.stopDragWatch();
        const s = root.session;
        const handle = Hypr.watchDrag(300, () => {
            if (s !== root.session)
                return;
            root.dragWatch = null;
            if (root.wheelOpen && root.wheelMode === "drag")
                root.closeWheel(false);
        });
        const w = root.wheelWindow();
        handle.paused = w ? w.dragInside : false;
        root.dragWatch = handle;
    }

    function stopDragWatch(): void {
        if (root.dragWatch) {
            root.dragWatch.stop();
            root.dragWatch = null;
        }
    }

    function closeAll(): void {
        root.closeWheel(false);
        const d = root.dialogWindow();
        if (d && d.isOpen)
            d.close();
    }

    function reloadAll(): void {
        Theme.reload();
        root.menuKey = "";
        Backend.reloadConfig(null);
        Backend.hello(null);
    }

    Component.onCompleted: {
        // Touch the services now: the daemon starts and job events are tracked from the very first one.
        console.info("dropwheel: shell started (backend " + Backend.status + ", " + Jobs.count + " jobs, scale " + Theme.scale + ")");
    }

    Connections {
        target: Backend

        function onReady(): void {
            // Strings / config may have changed with a daemon restart: do not reuse cached menus.
            root.menuKey = root.menuLoading ? root.menuKey : "";
        }
    }

    // trigger.dnd_check = false: a drag that left the wheel and did not come back within 1.5 s ended elsewhere.
    Timer {
        id: exitTimer

        interval: 1500
        onTriggered: {
            const w = root.wheelWindow();
            if (root.wheelOpen && root.wheelMode === "drag" && w && !w.dragInside)
                root.closeWheel(false);
        }
    }

    // A wheel opened without the DnD check closes again when no drag shows up.
    Timer {
        id: probeTimer

        interval: 1200
        onTriggered: {
            const w = root.wheelWindow();
            if (root.wheelOpen && root.wheelMode === "drag" && w && !w.dragSeen)
                root.closeWheel(false);
        }
    }

    GlobalShortcut {
        appid: "dropwheel"
        name: "shift"
        description: "Dropwheel: open the file-action wheel while dragging files"
        onPressed: {
            if (root.shortcutShouldFire("shift", true))
                root.onShift();
        }
        onReleased: {
            if (root.shortcutShouldFire("shift", false))
                root.onShift();
        }
    }

    GlobalShortcut {
        appid: "dropwheel"
        name: "open"
        description: "Dropwheel: open the wheel with the files in the clipboard"
        onPressed: {
            if (root.shortcutShouldFire("open", true))
                root.openClipboardAtCursor(true);
        }
        onReleased: {
            if (root.shortcutShouldFire("open", false))
                root.openClipboardAtCursor(true);
        }
    }

    IpcHandler {
        target: "dropwheel"

        function open(payload: string): void {
            root.openFromPayload(payload);
        }

        function openAtCursor(): void {
            root.openClipboardAtCursor(false);
        }

        function close(): void {
            root.closeAll();
        }

        function setHotzone(enabled: bool): void {
            root.hotzoneOverride = enabled;
        }

        function reload(): void {
            root.reloadAll();
        }

        function ping(): string {
            return "pong";
        }
    }

    // ------------------------------------------------------------------ windows
    LazyLoader {
        id: wheelLoader

        loading: true

        WheelWindow {
            id: wheelWin

            mode: root.wheelMode
            files: root.wheelFiles
            menu: root.wheelMenu
            menuLoading: root.menuLoading
            menuError: root.menuError
            menuErrorDetails: root.menuErrorDetails
            emptyTarget: root.emptyTarget
            springMs: root.springMs

            onDragInsideChanged: {
                if (root.dragWatch)
                    root.dragWatch.paused = wheelWin.dragInside;
                if (wheelWin.dragInside)
                    exitTimer.stop();
            }
            onFilesHovered: files => {
                if (root.wheelMode === "drag")
                    root.setFiles(files, "drag");
            }
            onFilesDropped: files => {
                root.setFiles(files, "click");
                root.switchToClick();
            }
            onActionChosen: (node, options) => root.runNode(node, options)
            onCancelled: root.closeWheel(false)
            onClickModeRequested: root.switchToClick()
            onRetryRequested: root.retryMenu()
            onDragExited: {
                if (root.wheelMode !== "drag" || wheelWin.closing)
                    return;
                if (!root.dndCheck) {
                    exitTimer.restart();
                    return;
                }
                // Escape aborts the DnD in Hyprland and only `exited` reaches us: check right away.
                const s = root.session;
                Hypr.queryMonitors(mons => {
                    if (s !== root.session || mons === null || root.wheelMode !== "drag" || wheelWin.dragInside)
                        return;
                    if (!Hypr.isDragging(mons))
                        root.closeWheel(false);
                });
            }
            onClosed: {
                root.wheelOpen = false;
                root.emptyTarget = false;
                root.wheelFiles = [];
                if (root.menuLoading)
                    root.clearMenu();
            }
        }
    }

    LazyLoader {
        id: dialogLoader

        loading: true

        DialogWindow {
            onJobStarted: jobId => {
                if (root.wheelScreen)
                    root.hudScreen = root.wheelScreen;
            }
        }
    }

    HudWindow {
        id: hud

        targetScreen: root.hudScreen ?? root.focusedScreen
        onExpandRequested: job => root.showResult(job)
    }

    Variants {
        model: root.hotzoneEnabled ? Quickshell.screens : []

        HotZone {
            required property var modelData

            targetScreen: modelData
            corner: root.hotzoneCorner
            size: root.hotzoneSize
            onDragEntered: (files, at) => root.hotzoneEntered(files, at, modelData)
            onFilesDropped: (files, at) => root.hotzoneDropped(files, at, modelData)
        }
    }
}
