pragma Singleton
pragma ComponentBehavior: Bound

// Hypr — Hyprland queries needed by the wheel (no Quickshell API exposes the global cursor or DnD state), and the
// screen colour picker (hyprpicker) shared by every ColorPicker.
//
// PROPERTIES (read-only)
//   available    bool   false once `hyprctl` failed to start (not installed / not in PATH)
//   dragActive   bool   last known DnD state (some monitor's solitaryBlockedBy contains "DND")
//   lastCursor   var    last known global cursor {x, y} in logical px, or null
//   monitors     var    last parsed `hyprctl -j monitors` array ([] until known)
//   queryTimeoutMs int  per-query timeout (default 2000 ms, writable)
//   colorPickerAvailable bool   `hyprpicker` is installed (false until checkColorPicker() found it)
//   colorPicking bool   from pickColor() until hyprpicker has exited. hyprpicker samples the composited screen, so
//                       a full-screen dimming layer (the dialog scrim) must turn fully transparent while this is
//                       true, or every colour picked outside the dialog card comes out darkened
//   pickColorDelayMs int   pause between colorPicking turning true and the start of hyprpicker, so the compositor
//                       shows a frame without the scrim before hyprpicker captures the screen (default 120, writable)
//
// FUNCTIONS (callbacks are invoked asynchronously)
//   queryDrag(cb)            runs `hyprctl -j monitors` and `hyprctl -j cursorpos` in parallel, then
//                            cb(isDragging: bool, cursor: {x, y} | null). Missing hyprctl / invalid JSON / timeout
//                            → cb(false, null) (cursor stays null when only cursorpos failed).
//   cursorPos(cb)            cb({x, y} | null)
//   queryMonitors(cb)        cb(array | null)
//   isDragging(monitors)     → bool, pure helper over a monitors array
//   screenAt(x, y)           → the ShellScreen (Quickshell.screens) whose logical rect contains the global point,
//                              else the nearest screen, else null
//   screenByName(name)       → ShellScreen | null
//   watchDrag(intervalMs, onEnd) / watchDrag(onEnd)
//                            polls monitors every intervalMs (default 300, min 50) until "DND" disappears, then calls
//                            onEnd() once and stops. Three consecutive hyprctl failures also count as "ended".
//                            Returns a handle {active (bool), paused (bool, set it to skip polls, e.g. while the drag
//                            is over the wheel), stop()}.
//   checkColorPicker()       looks for hyprpicker (`command -v` through sh) unless it is already known to be installed
//   pickColor(cb) → bool     runs `hyprpicker -f hex` after pickColorDelayMs and calls cb("#rrggbb"), or cb("") when
//                            the user pressed Esc, hyprpicker is missing or another pick is running (then it returns
//                            false). No --autocopy: the user's clipboard (e.g. files copied for the clipboard wheel)
//                            is left alone; the colour is read from stdout.

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    readonly property bool available: priv.available
    readonly property bool dragActive: priv.dragActive
    readonly property var lastCursor: priv.lastCursor
    readonly property var monitors: priv.monitors
    property int queryTimeoutMs: 2000
    readonly property bool colorPickerAvailable: priv.colorPickerAvailable
    readonly property bool colorPicking: priv.colorPicking
    property int pickColorDelayMs: 120

    function checkColorPicker(): void {
        if (!priv.colorPickerAvailable && !colorCheckProc.running)
            colorCheckProc.running = true;
    }

    function pickColor(callback: var): bool {
        const cb = typeof callback === "function" ? callback : null;
        if (!priv.colorPickerAvailable || priv.colorPicking) {
            if (cb !== null)
                Qt.callLater(() => priv.flush([cb], ""));
            return false;
        }
        priv.colorCallback = cb;
        priv.colorOutput = "";
        priv.colorStarted = false;
        priv.colorPicking = true;
        colorPickDelay.restart();
        return true;
    }

    function queryMonitors(callback: var): void {
        priv.monitorsWaiters.push(typeof callback === "function" ? callback : null);
        if (!monitorsProc.running && !priv.monitorsBusy) {
            priv.monitorsBusy = true;
            priv.monitorsGot = false;
            priv.monitorsStarted = false;
            priv.monitorsText = "";
            // Arm the timeout first: a missing binary may be reported synchronously by `running = true`.
            monitorsTimeout.restart();
            monitorsProc.running = true;
        }
    }

    function cursorPos(callback: var): void {
        priv.cursorWaiters.push(typeof callback === "function" ? callback : null);
        if (!cursorProc.running && !priv.cursorBusy) {
            priv.cursorBusy = true;
            priv.cursorGot = false;
            priv.cursorStarted = false;
            priv.cursorText = "";
            cursorTimeout.restart();
            cursorProc.running = true;
        }
    }

    function queryDrag(callback: var): void {
        const state = {
            mons: undefined,
            cursor: undefined
        };
        const finish = () => {
            if (state.mons === undefined || state.cursor === undefined)
                return;
            const dragging = state.mons !== null && root.isDragging(state.mons);
            if (typeof callback === "function") {
                try {
                    callback(dragging, state.cursor);
                } catch (e) {
                    console.warn("Hypr: queryDrag callback threw:", e);
                }
            }
        };
        queryMonitors(mons => {
            state.mons = mons;
            finish();
        });
        cursorPos(pos => {
            state.cursor = pos;
            finish();
        });
    }

    function isDragging(mons: var): bool {
        if (!Array.isArray(mons))
            return false;
        for (const m of mons) {
            const blocked = m?.solitaryBlockedBy;
            if (Array.isArray(blocked) && blocked.indexOf("DND") !== -1)
                return true;
        }
        return false;
    }

    function screenAt(x: real, y: real): var {
        const screens = Quickshell.screens;
        const n = screens?.length ?? 0;
        if (n === 0)
            return null;
        for (let i = 0; i < n; i++) {
            const s = screens[i];
            if (x >= s.x && x < s.x + s.width && y >= s.y && y < s.y + s.height)
                return s;
        }
        let best = null;
        let bestDist = Infinity;
        for (let i = 0; i < n; i++) {
            const s = screens[i];
            const dx = Math.max(s.x - x, 0, x - (s.x + s.width - 1));
            const dy = Math.max(s.y - y, 0, y - (s.y + s.height - 1));
            const d = dx * dx + dy * dy;
            if (d < bestDist) {
                bestDist = d;
                best = s;
            }
        }
        return best;
    }

    function screenByName(name: string): var {
        const screens = Quickshell.screens;
        const n = screens?.length ?? 0;
        for (let i = 0; i < n; i++) {
            if (screens[i].name === name)
                return screens[i];
        }
        return null;
    }

    function watchDrag(a: var, b: var): var {
        let interval = 300;
        let onEnd = null;
        if (typeof a === "function") {
            onEnd = a;
            if (typeof b === "number" && b > 0)
                interval = b;
        } else {
            if (typeof a === "number" && a > 0)
                interval = a;
            if (typeof b === "function")
                onEnd = b;
        }

        const timer = watchTimerComponent.createObject(root, {
            interval: Math.max(50, Math.round(interval))
        });
        const handle = {
            active: true,
            paused: false,
            busy: false,
            failures: 0,
            stop: null
        };
        handle.stop = () => {
            if (!handle.active)
                return;
            handle.active = false;
            if (timer) {
                timer.stop();
                timer.destroy();
            }
        };
        if (!timer) {
            console.warn("Hypr: could not create the drag watcher timer");
            handle.active = false;
            return handle;
        }
        timer.triggered.connect(() => {
            if (!handle.active || handle.paused || handle.busy)
                return;
            handle.busy = true;
            root.queryMonitors(mons => {
                handle.busy = false;
                if (!handle.active || handle.paused)
                    return;
                if (mons === null) {
                    handle.failures++;
                    if (handle.failures < 3)
                        return;
                } else {
                    handle.failures = 0;
                    if (root.isDragging(mons))
                        return;
                }
                handle.stop();
                if (typeof onEnd === "function") {
                    try {
                        onEnd();
                    } catch (e) {
                        console.warn("Hypr: watchDrag onEnd threw:", e);
                    }
                }
            });
        });
        timer.start();
        return handle;
    }

    QtObject {
        id: priv

        property bool available: true
        property bool dragActive: false
        property var lastCursor: null
        property var monitors: []

        property var monitorsWaiters: []
        property bool monitorsBusy: false
        property bool monitorsGot: false
        property string monitorsText: ""
        property bool monitorsStarted: false

        property var cursorWaiters: []
        property bool cursorBusy: false
        property bool cursorGot: false
        property string cursorText: ""
        property bool cursorStarted: false

        property bool colorPickerAvailable: false
        property bool colorPicking: false
        property bool colorStarted: false
        property string colorOutput: ""
        property var colorCallback: null

        function colorPickDone(): void {
            if (!colorPicking)
                return;
            if (!colorStarted)
                colorPickerAvailable = false; // the binary went away since the check
            // Output is "#RRGGBB" as the last thing printed (empty after Esc); earlier log lines may hold hex runs.
            const found = colorOutput.match(/#[0-9a-fA-F]{6}\b/g);
            const cb = colorCallback;
            colorCallback = null;
            colorPicking = false;
            flush([cb], found ? found[found.length - 1].toLowerCase() : "");
        }

        function parseJson(text: string): var {
            if (!text)
                return undefined;
            try {
                return JSON.parse(text);
            } catch (e) {
                return undefined;
            }
        }

        function flush(waiters: var, value: var): void {
            for (const cb of waiters) {
                if (typeof cb !== "function")
                    continue;
                try {
                    cb(value);
                } catch (e) {
                    console.warn("Hypr: callback threw:", e);
                }
            }
        }

        function monitorsDone(): void {
            if (!monitorsBusy)
                return;
            monitorsTimeout.stop();
            killMonitors.stop();
            monitorsBusy = false;
            if (!monitorsStarted)
                available = false;
            monitorsStarted = false;
            let result = null;
            if (monitorsGot) {
                const parsed = parseJson(monitorsText);
                if (Array.isArray(parsed)) {
                    result = parsed;
                    monitors = parsed;
                    dragActive = root.isDragging(parsed);
                } else {
                    console.warn("Hypr: unexpected `hyprctl -j monitors` output");
                }
            }
            const waiters = monitorsWaiters;
            monitorsWaiters = [];
            flush(waiters, result);
        }

        function cursorDone(): void {
            if (!cursorBusy)
                return;
            cursorTimeout.stop();
            killCursor.stop();
            cursorBusy = false;
            if (!cursorStarted)
                available = false;
            cursorStarted = false;
            let result = null;
            if (cursorGot) {
                const parsed = parseJson(cursorText);
                const x = Number(parsed?.x);
                const y = Number(parsed?.y);
                if (parsed && typeof parsed === "object" && isFinite(x) && isFinite(y)) {
                    result = {
                        x: x,
                        y: y
                    };
                    lastCursor = result;
                } else {
                    console.warn("Hypr: unexpected `hyprctl -j cursorpos` output");
                }
            }
            const waiters = cursorWaiters;
            cursorWaiters = [];
            flush(waiters, result);
        }
    }

    Process {
        id: monitorsProc

        command: ["hyprctl", "-j", "monitors"]
        stdout: StdioCollector {
            id: monitorsOut

            onStreamFinished: {
                priv.monitorsText = monitorsOut.text;
                priv.monitorsGot = true;
            }
        }
        onStarted: {
            priv.monitorsStarted = true;
            priv.available = true;
        }
        onRunningChanged: {
            if (!monitorsProc.running)
                priv.monitorsDone();
        }
    }

    Process {
        id: cursorProc

        command: ["hyprctl", "-j", "cursorpos"]
        stdout: StdioCollector {
            id: cursorOut

            onStreamFinished: {
                priv.cursorText = cursorOut.text;
                priv.cursorGot = true;
            }
        }
        onStarted: {
            priv.cursorStarted = true;
            priv.available = true;
        }
        onRunningChanged: {
            if (!cursorProc.running)
                priv.cursorDone();
        }
    }

    // A hung hyprctl (e.g. Hyprland's socket stuck) must not block the wheel forever.
    Timer {
        id: monitorsTimeout

        interval: root.queryTimeoutMs
        onTriggered: {
            if (monitorsProc.running) {
                monitorsProc.running = false; // SIGTERM; monitorsDone() runs when it exits
                killMonitors.restart();
            } else {
                priv.monitorsStarted = true; // not a missing binary, just no answer
                priv.monitorsDone();
            }
        }
    }

    Timer {
        id: killMonitors

        interval: 500
        onTriggered: {
            if (monitorsProc.running)
                monitorsProc.signal(9);
            priv.monitorsStarted = true;
            priv.monitorsDone();
        }
    }

    Timer {
        id: cursorTimeout

        interval: root.queryTimeoutMs
        onTriggered: {
            if (cursorProc.running) {
                cursorProc.running = false;
                killCursor.restart();
            } else {
                priv.cursorStarted = true;
                priv.cursorDone();
            }
        }
    }

    Timer {
        id: killCursor

        interval: 500
        onTriggered: {
            if (cursorProc.running)
                cursorProc.signal(9);
            priv.cursorStarted = true;
            priv.cursorDone();
        }
    }

    // `command -v` through sh: detects hyprpicker without spawning it.
    Process {
        id: colorCheckProc

        command: ["sh", "-c", "command -v hyprpicker >/dev/null 2>&1"]
        onExited: (exitCode, exitStatus) => priv.colorPickerAvailable = exitCode === 0
    }

    Timer {
        id: colorPickDelay

        interval: Math.max(0, root.pickColorDelayMs)
        onTriggered: colorPickProc.running = true
    }

    Process {
        id: colorPickProc

        command: ["hyprpicker", "-f", "hex"]
        stdout: StdioCollector {
            id: colorPickOut

            onStreamFinished: priv.colorOutput = colorPickOut.text
        }
        onStarted: priv.colorStarted = true
        onRunningChanged: {
            if (!colorPickProc.running)
                priv.colorPickDone();
        }
    }

    Component {
        id: watchTimerComponent

        Timer {
            repeat: true
            triggeredOnStart: false
        }
    }
}
