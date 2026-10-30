pragma Singleton
pragma ComponentBehavior: Bound

// Backend — owns the Python daemon (`dropwheel daemon`) and speaks the JSON-lines protocol of SPEC §5.
//
// PROPERTIES (read-only)
//   command      list<string>  argv used to start the daemon: $DROPWHEEL_BACKEND split like a shell would
//                              (single/double quotes, backslash escapes), default ["dropwheel", "daemon"]
//   running      bool          the daemon process is alive
//   connected    bool          the daemon sent {"event":"ready"} and requests are being written to it
//   helloDone    bool          the hello handshake finished (strings/config are filled)
//   status       string        "starting" | "connected" | "restarting" | "failed" | "stopped"
//   lastError    string        human readable reason of the last failure ("" when healthy)
//   protocolVersion int        version from the ready event (0 until known)
//   version      var           daemon version from hello (null until known)
//   lang         string        UI language chosen by the daemon ("it" | "en", "" until hello)
//   strings      var           {key: text} — every locale fragment merged by the daemon (hello.strings)
//   config       var           config dict (hello.config / "config" RPC / {"event":"config"} / {"event":"config_changed"})
//   warnings     var           config warnings from hello (updated by {"event":"config_changed"}; a language change
//                              in the config re-runs hello so `strings` and `lang` follow)
//   pendingCount int           requests waiting for an answer (queued + in flight)
//   The implicit notify signals stringsChanged() and configChanged() fire when those are replaced.
//
// SIGNALS
//   ready()          after the hello handshake (also after every daemon restart)
//   event(var ev)    every unsolicited message {"event": ...} (including "ready" and "job")
//   jobEvent(var ev) job progress/state messages (SPEC §5.2)
//
// FUNCTIONS (callbacks are always invoked asynchronously as callback(result, error); exactly one of them is
//            non-null; error = {code, message, details, install})
//   call(method, params, callback, timeoutMs) → request id. Default timeout 30 s (120 s for "preview").
//            Requests made before the daemon is ready are queued and sent after the hello handshake starts.
//   menu(files, context, cb)         context "drag" | "click"; files are paths or URIs
//   run(action, files, params, cb)   result {job}
//   cancel(jobId, cb)   undo(jobId, cb)
//   preview(kind, args, cb)          args = {path, size, ...} (SPEC §5.5), 120 s timeout
//   util(op, args, cb)               op "open" | "reveal" | "copy_files" | "copy_text" | "open_with" | "terminal" | "save_text"
//   apps(files, cb)  clipboardFiles(cb)  shareStop(id, cb)  doctor(cb)
//   reloadConfig(cb)                 "config" RPC; updates `config`
//   hello(cb)                        re-run the handshake (refreshes strings/config/lang)
//   restart()                        restart the daemon now (resets the back-off)
//   stop()                           stop the daemon and disable auto-restart until start()/restart()
//   start()
//   fileUrl(path) → "file:///…" URL with every path segment percent-encoded (URLs are returned unchanged)
//   pathFromUrl(url) → local path for file:// URLs (percent-decoded); other strings are returned unchanged
//   toPaths(list) → list of strings (file:// URLs converted to paths, http(s) URLs kept)
//   NON-UTF-8 FILE NAMES use the daemon's representation (Python surrogateescape): byte 0xXX that is not part of a
//   UTF-8 sequence is the lone surrogate U+DCXX. pathFromUrl("file:///a/caf%E9") = "/a/caf\uDCE9", fileUrl() maps it
//   back to %E9, and call() writes lone surrogates as JSON \u escapes (V4's JSON.stringify leaves them raw and
//   Process.write's UTF-8 conversion would replace them), so such paths round-trip between shell and daemon.
//
// RESTART POLICY: back-off 1 s, 2 s, 5 s, 5 s…; at most 5 starts per minute, then status "failed", queued requests
// are rejected and a new attempt is made after 60 s (or immediately on the next call()).

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    readonly property var command: parseCommand(Quickshell.env("DROPWHEEL_BACKEND"))
    readonly property bool running: proc.running
    readonly property bool connected: priv.connected
    readonly property bool helloDone: priv.helloDone
    readonly property string status: priv.status
    readonly property string lastError: priv.lastError
    readonly property int protocolVersion: priv.protocolVersion
    readonly property var version: priv.version
    readonly property string lang: priv.lang
    readonly property var strings: priv.strings
    readonly property var config: priv.config
    readonly property var warnings: priv.warnings
    readonly property int pendingCount: priv.pendingCount

    signal ready
    signal event(var ev)
    signal jobEvent(var ev)

    // ------------------------------------------------------------------ public API
    function call(method: string, params: var, callback: var, timeoutMs: var): int {
        const id = ++priv.nextId;
        const m = String(method ?? "");
        const timeout = (typeof timeoutMs === "number" && timeoutMs > 0) ? timeoutMs : (m === "preview" ? 120000 : 30000);
        let line = "";
        try {
            line = priv.escapeLoneSurrogates(JSON.stringify({
                id: id,
                method: m,
                params: (params !== undefined && params !== null) ? params : {}
            }));
        } catch (e) {
            priv.deliverLater(callback, null, priv.makeError("invalid_request", "ui.error.invalid_request", {}, String(e)));
            return id;
        }

        priv.pending[id] = {
            cb: typeof callback === "function" ? callback : null,
            method: m,
            deadline: Date.now() + timeout,
            sent: false,
            line: line
        };
        priv.pendingCount++;

        if (priv.gaveUp) {
            // The daemon is known to be down: answer now and try to bring it back for the next request.
            priv.fail(id, priv.makeError("unavailable", "ui.backend.unavailable", {}, priv.lastError));
            root.restart();
        } else if (priv.connected) {
            priv.send(id);
        } else {
            priv.queue.push(id);
            if (!priv.startPending && !proc.running && !retryTimer.running)
                root.start();
        }
        return id;
    }

    function menu(files: var, context: var, callback: var): int {
        return call("menu", {
            files: priv.stringList(files),
            context: context === "drag" ? "drag" : "click"
        }, callback);
    }

    function run(action: string, files: var, params: var, callback: var): int {
        return call("run", {
            action: String(action ?? ""),
            files: root.toPaths(files),
            params: (params && typeof params === "object") ? params : {}
        }, callback);
    }

    function cancel(jobId: string, callback: var): int {
        return call("cancel", {
            job: String(jobId ?? "")
        }, callback);
    }

    function undo(jobId: string, callback: var): int {
        return call("undo", {
            job: String(jobId ?? "")
        }, callback);
    }

    function preview(kind: string, args: var, callback: var): int {
        const params = Object.assign({}, (args && typeof args === "object") ? args : {}, {
            kind: String(kind ?? "")
        });
        if (typeof params.path === "string")
            params.path = root.pathFromUrl(params.path);
        return call("preview", params, callback, 120000);
    }

    function util(op: string, args: var, callback: var): int {
        const params = Object.assign({}, (args && typeof args === "object") ? args : {}, {
            op: String(op ?? "")
        });
        if (params.paths !== undefined)
            params.paths = root.toPaths(params.paths);
        return call("util", params, callback);
    }

    function apps(files: var, callback: var): int {
        return call("apps", {
            files: root.toPaths(files)
        }, callback);
    }

    function clipboardFiles(callback: var): int {
        return call("clipboard_files", {}, callback);
    }

    function shareStop(shareId: string, callback: var): int {
        return call("share_stop", {
            id: String(shareId ?? "")
        }, callback);
    }

    function doctor(callback: var): int {
        return call("doctor", {}, callback, 60000);
    }

    function reloadConfig(callback: var): int {
        return call("config", {}, (result, error) => {
            if (!error && result && typeof result === "object" && !Array.isArray(result))
                priv.config = result;
            if (typeof callback === "function")
                callback(result, error);
        });
    }

    function hello(callback: var): int {
        return priv.sendHello(callback);
    }

    function start(): void {
        priv.stopping = false;
        if (proc.running || priv.startPending)
            return;
        retryTimer.stop();
        if (!root.command || root.command.length === 0) {
            priv.lastError = "empty DROPWHEEL_BACKEND command";
            priv.status = "failed";
            priv.gaveUp = true;
            priv.rejectAll(priv.makeError("unavailable", "ui.backend.unavailable", {}, priv.lastError), false);
            return;
        }
        priv.startedThisRun = false;
        priv.startPending = true;
        priv.status = priv.restartTimes.length > 0 ? "restarting" : "starting";
        priv.restartTimes = priv.restartTimes.concat([Date.now()]);
        proc.running = true;
    }

    function restart(): void {
        priv.attempt = 0;
        priv.restartTimes = [];
        priv.gaveUp = false;
        if (proc.running) {
            // handleStopped() will schedule the new start once the old process is gone.
            priv.restartNow = true;
            proc.running = false;
        } else {
            root.start();
        }
    }

    function stop(): void {
        priv.stopping = true;
        retryTimer.stop();
        if (proc.running)
            proc.running = false;
        priv.status = "stopped";
    }

    function fileUrl(path: var): string {
        if (path === undefined || path === null)
            return "";
        const s = String(path);
        if (s === "")
            return "";
        if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(s))
            return s;
        return "file://" + s.split("/").map(seg => priv.encodeSegment(seg)).join("/");
    }

    function pathFromUrl(url: var): string {
        if (url === undefined || url === null)
            return "";
        let s = String(url);
        if (!s.startsWith("file://"))
            return s;
        s = s.slice(7);
        if (!s.startsWith("/")) {
            // file://host/path → /path
            const slash = s.indexOf("/");
            s = slash >= 0 ? s.slice(slash) : "";
        }
        // Not decodeURIComponent: it throws on names that are not UTF-8 (a Latin-1 name from an old archive or a Samba
        // share) and turns CESU-8 bytes into surrogates the daemon cannot encode. This decodes like the daemon does
        // (Python os.fsdecode, surrogateescape), so the path names the same bytes on both sides.
        return priv.decodePercent(s);
    }

    function toPaths(list: var): var {
        return priv.stringList(list).map(f => root.pathFromUrl(f));
    }

    // Splits a command line like a POSIX shell (without expansions): 'single quotes', "double quotes"
    // (with \" \\ \$ \` escapes) and backslash escapes outside quotes.
    function parseCommand(value: var): var {
        const fallback = ["dropwheel", "daemon"];
        if (typeof value !== "string" || value.trim() === "")
            return fallback;
        const args = [];
        let cur = "";
        let inArg = false;
        let quote = "";
        for (let i = 0; i < value.length; i++) {
            const ch = value[i];
            if (quote === "'") {
                if (ch === "'")
                    quote = "";
                else
                    cur += ch;
            } else if (quote === "\"") {
                if (ch === "\"") {
                    quote = "";
                } else if (ch === "\\" && i + 1 < value.length && "\"\\$`".indexOf(value[i + 1]) !== -1) {
                    cur += value[++i];
                } else {
                    cur += ch;
                }
            } else if (ch === "'" || ch === "\"") {
                quote = ch;
                inArg = true;
            } else if (ch === "\\" && i + 1 < value.length) {
                cur += value[++i];
                inArg = true;
            } else if (ch === " " || ch === "\t" || ch === "\n") {
                if (inArg) {
                    args.push(cur);
                    cur = "";
                    inArg = false;
                }
            } else {
                cur += ch;
                inArg = true;
            }
        }
        if (inArg)
            args.push(cur);
        return args.length > 0 ? args : fallback;
    }

    // ------------------------------------------------------------------ internals
    function handleLine(data: string): void {
        const line = String(data ?? "").trim();
        if (line === "")
            return;
        let msg;
        try {
            msg = JSON.parse(line);
        } catch (e) {
            console.warn("Backend: ignoring non-JSON line from daemon:", line.length > 300 ? line.slice(0, 300) + "…" : line);
            return;
        }
        if (msg === null || typeof msg !== "object" || Array.isArray(msg)) {
            console.warn("Backend: ignoring unexpected message:", line.slice(0, 300));
            return;
        }
        if (msg.id !== undefined && msg.id !== null && (Object.prototype.hasOwnProperty.call(msg, "result") || Object.prototype.hasOwnProperty.call(msg, "error"))) {
            priv.resolve(msg);
        } else if (typeof msg.event === "string") {
            priv.handleEvent(msg);
        } else {
            console.warn("Backend: ignoring message without id/event:", line.slice(0, 300));
        }
    }

    function handleStopped(): void {
        priv.startPending = false;
        priv.connected = false;
        priv.helloDone = false;
        if (!priv.startedThisRun) {
            priv.lastError = "could not start " + JSON.stringify(root.command) + " (command not found?)";
            console.warn("Backend: failed to start the daemon:", JSON.stringify(root.command));
        } else if (!priv.stopping && !priv.restartNow) {
            priv.lastError = "daemon exited with code " + priv.lastExitCode;
            console.warn("Backend: daemon exited with code", priv.lastExitCode);
        }

        // Requests already written to the dead process will never be answered.
        priv.rejectAll(priv.makeError("backend_exited", "ui.error.backend_exited", {}, priv.lastError), true);

        if (priv.stopping) {
            priv.status = "stopped";
            return;
        }
        if (priv.restartNow) {
            priv.restartNow = false;
            retryTimer.interval = 0;
            retryTimer.restart();
            return;
        }

        const now = Date.now();
        priv.restartTimes = priv.restartTimes.filter(t => now - t < 60000);
        if (priv.restartTimes.length >= 5) {
            priv.gaveUp = true;
            priv.status = "failed";
            console.warn("Backend: daemon keeps failing, giving up for 60 s:", priv.lastError);
            priv.rejectAll(priv.makeError("unavailable", "ui.backend.unavailable", {}, priv.lastError), false);
            retryTimer.interval = 60000;
            retryTimer.restart();
            return;
        }
        const delays = [1000, 2000, 5000];
        retryTimer.interval = delays[Math.min(priv.attempt, delays.length - 1)];
        priv.attempt++;
        priv.status = "restarting";
        retryTimer.restart();
    }

    Component.onCompleted: start()

    QtObject {
        id: priv

        property bool connected: false
        property bool helloDone: false
        property string status: "starting"
        property string lastError: ""
        property int protocolVersion: 0
        property var version: null
        property string lang: ""
        property var strings: ({})
        property var config: ({})
        property var warnings: []

        property int nextId: 0
        property int pendingCount: 0
        property var pending: ({})
        property var queue: []

        property bool startedThisRun: false
        property bool startPending: false
        property bool stopping: false
        property bool restartNow: false
        property bool gaveUp: false
        property int attempt: 0
        property int lastExitCode: 0
        property var restartTimes: []

        function stringList(list: var): var {
            if (list === undefined || list === null)
                return [];
            if (typeof list === "string")
                return list === "" ? [] : [list];
            const out = [];
            const n = list.length ?? 0;
            for (let i = 0; i < n; i++) {
                const v = list[i];
                if (v === undefined || v === null)
                    continue;
                const s = String(v);
                if (s !== "")
                    out.push(s);
            }
            return out;
        }

        function hex2(n: int): string {
            return (n < 16 ? "0" : "") + n.toString(16).toUpperCase();
        }

        // JSON text with every unpaired UTF-16 surrogate written as a \uXXXX escape (ES2019 well-formed
        // JSON.stringify; V4 emits them raw). JSON.stringify only produces surrogates inside string literals.
        function escapeLoneSurrogates(text: string): string {
            if (!/[\uD800-\uDFFF]/.test(text))
                return text;
            let out = "";
            for (let i = 0; i < text.length; i++) {
                const c = text.charCodeAt(i);
                if (c >= 0xD800 && c <= 0xDBFF && i + 1 < text.length) {
                    const d = text.charCodeAt(i + 1);
                    if (d >= 0xDC00 && d <= 0xDFFF) {
                        out += text[i] + text[i + 1];
                        i++;
                        continue;
                    }
                }
                out += (c >= 0xD800 && c <= 0xDFFF) ? "\\u" + c.toString(16) : text[i];
            }
            return out;
        }

        // encodeURIComponent for one path segment; a surrogateescape'd byte (U+DC80–U+DCFF) becomes %XX again
        // (encodeURIComponent throws URIError on any lone surrogate).
        function encodeSegment(seg: string): string {
            if (!/[\uD800-\uDFFF]/.test(seg))
                return encodeURIComponent(seg);
            let out = "";
            let run = "";
            for (let i = 0; i < seg.length; i++) {
                const c = seg.charCodeAt(i);
                if (c >= 0xD800 && c <= 0xDBFF && i + 1 < seg.length) {
                    const d = seg.charCodeAt(i + 1);
                    if (d >= 0xDC00 && d <= 0xDFFF) {
                        run += seg[i] + seg[i + 1];
                        i++;
                        continue;
                    }
                }
                if (c < 0xD800 || c > 0xDFFF) {
                    run += seg[i];
                    continue;
                }
                out += encodeURIComponent(run);
                run = "";
                out += (c >= 0xDC80 && c <= 0xDCFF) ? "%" + hex2(c - 0xDC00) : "%EF%BF%BD";
            }
            return out + encodeURIComponent(run);
        }

        // Percent-decoding that never throws: each run of %XX escapes is decoded as UTF-8 bytes; a byte that does not
        // start a valid sequence becomes U+DC00 + byte (Python's surrogateescape, as os.fsdecode() in the daemon).
        // A "%" without two hex digits stays literal (urllib.parse.unquote_to_bytes does the same).
        function decodePercent(text: string): string {
            let out = "";
            let i = 0;
            while (i < text.length) {
                const bytes = [];
                while (i + 2 < text.length && text[i] === "%" && /^[0-9a-fA-F]{2}$/.test(text.substr(i + 1, 2))) {
                    bytes.push(parseInt(text.substr(i + 1, 2), 16));
                    i += 3;
                }
                if (bytes.length > 0) {
                    out += decodeUtf8(bytes);
                    continue;
                }
                out += text[i];
                i++;
            }
            return out;
        }

        function decodeUtf8(bytes: var): string {
            let out = "";
            let i = 0;
            while (i < bytes.length) {
                const b = bytes[i];
                let need = 0;
                let cp = 0;
                let min = 0;
                if (b < 0x80) {
                    out += String.fromCharCode(b);
                    i++;
                    continue;
                } else if (b >= 0xC2 && b <= 0xDF) {
                    need = 1;
                    cp = b & 0x1F;
                    min = 0x80;
                } else if (b >= 0xE0 && b <= 0xEF) {
                    need = 2;
                    cp = b & 0x0F;
                    min = 0x800;
                } else if (b >= 0xF0 && b <= 0xF4) {
                    need = 3;
                    cp = b & 0x07;
                    min = 0x10000;
                }
                let ok = need > 0 && i + need < bytes.length;
                for (let k = 1; ok && k <= need; k++) {
                    const c = bytes[i + k];
                    if ((c & 0xC0) !== 0x80)
                        ok = false;
                    else
                        cp = (cp << 6) | (c & 0x3F);
                }
                // Strict UTF-8 like Python: no overlong forms, no encoded surrogates, nothing above U+10FFFF.
                if (!ok || cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
                    out += String.fromCharCode(0xDC00 + b);
                    i++;
                    continue;
                }
                if (cp >= 0x10000) {
                    const v = cp - 0x10000;
                    out += String.fromCharCode(0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF));
                } else {
                    out += String.fromCharCode(cp);
                }
                i += need + 1;
            }
            return out;
        }

        function makeError(code: string, key: string, args: var, details: var): var {
            return {
                code: code,
                message: I18n.t(key, args),
                details: details ? String(details) : "",
                install: ""
            };
        }

        function normaliseError(e: var): var {
            if (e === null || e === undefined)
                return {
                    code: "error",
                    message: I18n.t("ui.error.unexpected"),
                    details: "",
                    install: ""
                };
            if (typeof e !== "object")
                return {
                    code: "error",
                    message: String(e),
                    details: "",
                    install: ""
                };
            return {
                code: String(e.code ?? "error"),
                message: (e.message !== undefined && e.message !== null && String(e.message) !== "") ? String(e.message) : I18n.t("ui.error.unexpected"),
                details: (e.details !== undefined && e.details !== null) ? String(e.details) : "",
                install: (e.install !== undefined && e.install !== null) ? String(e.install) : ""
            };
        }

        function invoke(cb: var, result: var, error: var, method: string): void {
            if (typeof cb !== "function")
                return;
            try {
                cb(result, error);
            } catch (e) {
                console.warn("Backend: callback for", method, "threw:", e, e?.stack ?? "");
            }
        }

        function deliverLater(cb: var, result: var, error: var): void {
            if (typeof cb !== "function")
                return;
            Qt.callLater(() => priv.invoke(cb, result, error, "?"));
        }

        function take(id: var): var {
            const entry = pending[id];
            if (entry === undefined)
                return null;
            delete pending[id];
            pendingCount = Math.max(0, pendingCount - 1);
            const qi = queue.indexOf(Number(id));
            if (qi !== -1)
                queue.splice(qi, 1);
            return entry;
        }

        function fail(id: var, error: var): void {
            const entry = take(id);
            if (entry !== null && entry.cb !== null) {
                const cb = entry.cb;
                Qt.callLater(() => priv.invoke(cb, null, error, entry.method));
            }
        }

        function send(id: var): void {
            const entry = pending[id];
            if (entry === undefined)
                return;
            entry.sent = true;
            proc.write(entry.line + "\n");
        }

        function resolve(msg: var): void {
            const entry = take(msg.id);
            if (entry === null)
                return; // timed out earlier or unknown id
            const hasError = msg.error !== undefined && msg.error !== null;
            if (hasError)
                invoke(entry.cb, null, normaliseError(msg.error), entry.method);
            else
                invoke(entry.cb, msg.result === undefined ? null : msg.result, null, entry.method);
        }

        function rejectAll(error: var, onlySent: bool): void {
            const ids = Object.keys(pending);
            for (const id of ids) {
                const entry = pending[id];
                if (onlySent && !entry.sent)
                    continue;
                fail(id, error);
            }
        }

        function checkTimeouts(): void {
            const now = Date.now();
            for (const id of Object.keys(pending)) {
                const entry = pending[id];
                if (entry.deadline <= now)
                    fail(id, makeError("timeout", "ui.error.timeout", {
                        method: entry.method
                    }, ""));
            }
        }

        function sendHello(callback: var): int {
            const id = ++nextId;
            pending[id] = {
                cb: (result, error) => {
                    if (!error && result && typeof result === "object") {
                        priv.strings = (result.strings && typeof result.strings === "object") ? result.strings : {};
                        priv.config = (result.config && typeof result.config === "object") ? result.config : {};
                        priv.lang = typeof result.lang === "string" ? result.lang : "";
                        priv.version = result.version ?? null;
                        priv.warnings = Array.isArray(result.warnings) ? result.warnings : [];
                    } else if (error) {
                        console.warn("Backend: hello failed:", error.message, error.details);
                    }
                    priv.helloDone = true;
                    if (typeof callback === "function")
                        callback(result, error);
                    root.ready();
                },
                method: "hello",
                deadline: Date.now() + 30000,
                sent: false,
                line: JSON.stringify({
                    id: id,
                    method: "hello",
                    params: {}
                })
            };
            pendingCount++;
            if (connected)
                send(id);
            else
                queue.unshift(id);
            return id;
        }

        function handleEvent(msg: var): void {
            if (msg.event === "ready") {
                protocolVersion = Number(msg.version ?? 0) || 0;
                connected = true;
                attempt = 0;
                gaveUp = false;
                lastError = "";
                status = "connected";
                sendHello(null);
                const q = queue;
                queue = [];
                for (const id of q)
                    send(id);
            } else if (msg.event === "job") {
                root.jobEvent(msg);
            } else if (msg.event === "config") {
                if (msg.config && typeof msg.config === "object" && !Array.isArray(msg.config))
                    config = msg.config;
            } else if (msg.event === "config_changed") {
                // {config, lang, lang_changed, warnings}: the config file changed on disk.
                if (msg.config && typeof msg.config === "object" && !Array.isArray(msg.config))
                    config = msg.config;
                if (Array.isArray(msg.warnings))
                    warnings = msg.warnings;
                // A new language needs the other strings table: re-run the handshake (emits ready() again).
                if (msg.lang_changed === true || (typeof msg.lang === "string" && msg.lang !== "" && msg.lang !== lang))
                    sendHello(null);
            }
            root.event(msg);
        }
    }

    Process {
        id: proc

        command: root.command
        stdinEnabled: true
        environment: ({
                PYTHONUNBUFFERED: "1",
                PYTHONIOENCODING: "utf-8"
            })

        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => root.handleLine(data)
        }

        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                const line = String(data ?? "").replace(/\s+$/, "");
                if (line !== "")
                    console.warn("[dropwheel-daemon]", line);
            }
        }

        onStarted: {
            priv.startedThisRun = true;
            priv.startPending = false;
        }
        onExited: (exitCode, exitStatus) => priv.lastExitCode = exitCode
        onRunningChanged: {
            if (!proc.running)
                root.handleStopped();
        }
    }

    Timer {
        id: retryTimer

        repeat: false
        onTriggered: root.start()
    }

    Timer {
        interval: 250
        repeat: true
        running: priv.pendingCount > 0
        onTriggered: priv.checkTimeouts()
    }
}
