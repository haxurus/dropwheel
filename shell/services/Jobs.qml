pragma Singleton
pragma ComponentBehavior: Bound

// Jobs — client-side list of backend jobs, fed by Backend.jobEvent (SPEC §5.2).
//
// Reference `Jobs` once at shell start-up (e.g. in shell.qml Component.onCompleted) so the singleton exists before
// the first job event arrives.
//
// JOB OBJECT (plain JS snapshot; a new object is produced on every change)
//   id            "j12" (daemon id) or "local-N" while the run request has not been answered yet
//   localId       "local-N" when the job was started from this shell with run(), else ""
//   state         "queued" | "running" | "done" | "failed" | "cancelled"
//   action        action id ("image.compress.balanced")
//   label         translated action label ("Comprimi · Bilanciata")
//   files         [names]
//   paths         [full input paths / URLs] (job event "paths"; TextResult/InfoResult save next to the source)
//   progress     0..1 or null (indeterminate)
//   message       progress / result message
//   warnings      [strings]
//   result        Result JSON when done: {kind, message, outputs:[{path, uri, name, size, size_h, category, mime,
//                 thumb}], size_before, size_after, undoable, text?, info?, url?, qr?, share_id?} — else null
//   error         {code, message, details, install} when failed — else null
//   createdAt, updatedAt, finishedAt   ms timestamps (finishedAt 0 while active)
//   cancelling    cancel requested, waiting for the daemon
//   undoing / undone / undoMessage / undoError    undo state
//
// PROPERTIES
//   model         ListModel, one row per job in arrival order (newest last). Roles: jobId (string), job (snapshot).
//                 Rows are updated in place, so delegates keep their state:
//                   Repeater { model: Jobs.model; delegate: JobCard { required property string jobId; required property var job } }
//   jobs          array of snapshots (same order) — reassigned on every change, handy for JS and bindings
//   count, activeCount (queued + running)
//   pruneAfterMs  safety net for finished rows that no view removes (default 120000): a cancelled row goes this long
//                 after finishing, a done row this long after it would have auto-dismissed (config hud.timeout_ms,
//                 default 7000). Rows meant to stay are never pruned: rows held by a view (hold()), every done row
//                 while hud.timeout_ms is 0 ("never"), done rows with an undo running or failed, and share results
//                 until the share expired. Failed jobs stay until dismissed (the most recent `maxFailedKept`,
//                 default 20)
//
// SIGNALS
//   jobAdded(var job)  jobUpdated(var job)  jobFinished(var job) (once, on done/failed/cancelled)  jobRemoved(string jobId)
//
// FUNCTIONS
//   run(action, files, params, label) → id   starts a job; a placeholder row appears immediately (state "queued",
//                                           id "local-N") and is re-keyed to the daemon id; if the request fails the
//                                           row turns "failed" with the error (so the HUD can show it)
//   cancel(id)            asks the daemon to stop the job (works on placeholders too)
//   undo(id, cb)          undo a finished job; cb(result, error); returns false when not undoable
//   dismiss(id)           removes the row (a running job keeps running; later events for it are ignored)
//   clearFinished()       removes every finished row
//   hold(id, on)          a view shows this row and removes it itself (e.g. a HUD card: hold(jobId, true) when
//                         created, hold(jobId, false) when destroyed): prune() leaves held rows alone. `id` is the
//                         row's current jobId (the model role; a renamed row keeps its hold); unknown ids are ignored
//   prune()               removes the finished rows that are due now (see pruneAfterMs); runs every 10 s anyway
//   get(id) → snapshot | null      indexOf(id) → row | -1      resolveId(id) → daemon id for a "local-N" id
//   isTerminal(state) → bool

import QtQuick
import Quickshell

Singleton {
    id: root

    readonly property ListModel model: jobsModel
    readonly property var jobs: priv.jobs
    readonly property int count: priv.jobs.length
    readonly property int activeCount: priv.jobs.filter(j => !root.isTerminal(j.state)).length
    property int pruneAfterMs: 120000
    property int maxFailedKept: 20

    signal jobAdded(var job)
    signal jobUpdated(var job)
    signal jobFinished(var job)
    signal jobRemoved(string jobId)

    function isTerminal(state: var): bool {
        return state === "done" || state === "failed" || state === "cancelled";
    }

    function resolveId(id: var): string {
        const s = String(id ?? "");
        return priv.localToReal[s] ?? s;
    }

    function get(id: var): var {
        return priv.byId[root.resolveId(id)] ?? null;
    }

    function indexOf(id: var): int {
        const real = root.resolveId(id);
        for (let i = 0; i < jobsModel.count; i++) {
            if (jobsModel.get(i).jobId === real)
                return i;
        }
        return -1;
    }

    function run(action: var, files: var, params: var, label: var): string {
        const localId = "local-" + (++priv.localSeq);
        const paths = Backend.toPaths(files);
        priv.insert(priv.makeJob({
            id: localId,
            localId: localId,
            local: true,
            state: "queued",
            action: String(action ?? ""),
            label: (typeof label === "string") ? label : "",
            files: paths.map(p => priv.displayName(p)),
            paths: paths.slice()
        }));

        Backend.run(String(action ?? ""), paths, (params && typeof params === "object") ? params : {}, (result, error) => {
            const placeholder = priv.byId[localId];
            if (error || !result || typeof result.job !== "string" || result.job === "") {
                if (placeholder === undefined)
                    return;
                const err = error ?? {
                    code: "no_job",
                    message: I18n.t("ui.error.no_job"),
                    details: "",
                    install: ""
                };
                priv.update(localId, placeholder.cancelling ? {
                    state: "cancelled",
                    cancelling: false,
                    local: false
                } : {
                    state: "failed",
                    error: err,
                    local: false
                });
                return;
            }
            const realId = result.job;
            priv.localToReal[localId] = realId;
            if (placeholder === undefined)
                return; // already adopted by an early job event
            if (priv.byId[realId] !== undefined) {
                // Events for this job arrived first and created their own row: drop the placeholder.
                priv.remove(localId, false);
                if (placeholder.cancelling)
                    root.cancel(realId);
                return;
            }
            priv.rename(localId, realId);
            if (placeholder.cancelling)
                Backend.cancel(realId, (res, err) => {
                    if (err)
                        console.warn("Jobs: cancel failed:", err.message);
                });
        });
        return localId;
    }

    function cancel(id: var): void {
        const realId = root.resolveId(id);
        const job = priv.byId[realId];
        if (job !== undefined) {
            if (root.isTerminal(job.state))
                return;
            priv.update(realId, {
                cancelling: true
            });
            if (job.local)
                return; // sent once the daemon id is known
        }
        if (realId.startsWith("local-"))
            return;
        Backend.cancel(realId, (result, error) => {
            if (error) {
                console.warn("Jobs: cancel failed:", error.message);
                const j = priv.byId[realId];
                if (j !== undefined && !root.isTerminal(j.state))
                    priv.update(realId, {
                        cancelling: false
                    });
            }
        });
    }

    function undo(id: var, callback: var): bool {
        const realId = root.resolveId(id);
        const job = priv.byId[realId];
        if (job !== undefined && (job.local || job.state !== "done" || job.undone || job.undoing || job.result?.undoable === false)) {
            if (typeof callback === "function")
                Qt.callLater(() => callback(null, {
                        code: "not_undoable",
                        message: I18n.t("ui.job.undo_failed"),
                        details: "",
                        install: ""
                    }));
            return false;
        }
        if (job !== undefined)
            priv.update(realId, {
                undoing: true,
                undoError: null
            });
        Backend.undo(realId, (result, error) => {
            const ok = !error && result && result.ok !== false;
            if (priv.byId[realId] !== undefined) {
                if (ok)
                    priv.update(realId, {
                        undoing: false,
                        undone: true,
                        undoMessage: String(result?.message ?? "")
                    });
                else
                    priv.update(realId, {
                        undoing: false,
                        undoError: error ?? {
                            code: "undo_failed",
                            message: (result?.message ? String(result.message) : I18n.t("ui.job.undo_failed")),
                            details: "",
                            install: ""
                        }
                    });
            }
            if (typeof callback === "function") {
                try {
                    callback(result, error);
                } catch (e) {
                    console.warn("Jobs: undo callback threw:", e);
                }
            }
        });
        return true;
    }

    function dismiss(id: var): void {
        const realId = root.resolveId(id);
        if (priv.byId[realId] === undefined)
            return;
        const job = priv.byId[realId];
        if (!root.isTerminal(job.state))
            priv.dismissed[realId] = Date.now();
        priv.remove(realId, true);
    }

    function clearFinished(): void {
        for (const job of priv.jobs.slice()) {
            if (root.isTerminal(job.state))
                priv.remove(job.id, true);
        }
    }

    function hold(id: var, on: var): void {
        // Exact row id, never resolved: a removed placeholder's "local-N" maps to another row's daemon id.
        const key = String(id ?? "");
        if (!on) {
            delete priv.held[key];
        } else if (priv.byId[key] !== undefined) {
            priv.held[key] = true;
        }
    }

    function prune(): void {
        priv.prune();
    }

    function handleEvent(ev: var): void {
        if (!ev || typeof ev !== "object")
            return;
        const id = String(ev.job ?? "");
        if (id === "")
            return;

        const patch = {};
        if (["queued", "running", "done", "failed", "cancelled"].indexOf(ev.state) !== -1)
            patch.state = ev.state;
        if (typeof ev.action === "string" && ev.action !== "")
            patch.action = ev.action;
        if (typeof ev.label === "string" && ev.label !== "")
            patch.label = ev.label;
        if (Array.isArray(ev.files))
            patch.files = ev.files.map(f => String(f));
        if (Array.isArray(ev.paths))
            patch.paths = ev.paths.map(p => String(p));
        if (Object.prototype.hasOwnProperty.call(ev, "progress"))
            patch.progress = (typeof ev.progress === "number" && isFinite(ev.progress)) ? Math.max(0, Math.min(1, ev.progress)) : null;
        if (typeof ev.message === "string")
            patch.message = ev.message;
        if (Array.isArray(ev.warnings))
            patch.warnings = ev.warnings.map(w => String(w));
        if (ev.result && typeof ev.result === "object")
            patch.result = ev.result;
        if (ev.error !== undefined && ev.error !== null)
            patch.error = priv.normaliseError(ev.error);
        if (patch.state !== undefined && root.isTerminal(patch.state))
            patch.cancelling = false;
        if (patch.state === "done")
            patch.progress = 1;

        if (priv.dismissed[id] !== undefined) {
            if (patch.state !== undefined && root.isTerminal(patch.state)) {
                delete priv.dismissed[id];
                root.jobFinished(Object.assign(priv.makeJob({
                    id: id,
                    finishedAt: Date.now()
                }), patch));
            }
            return;
        }

        if (priv.byId[id] === undefined) {
            const placeholder = priv.findPlaceholder(patch.action ?? "", patch.files ?? null);
            if (placeholder !== "") {
                priv.localToReal[placeholder] = id;
                const wasCancelling = priv.byId[placeholder].cancelling;
                priv.rename(placeholder, id);
                if (wasCancelling)
                    Backend.cancel(id, null);
            } else {
                priv.insert(priv.makeJob({
                    id: id,
                    action: patch.action ?? "",
                    label: patch.label ?? "",
                    files: patch.files ?? []
                }));
            }
        }
        priv.update(id, patch);
    }

    Connections {
        target: Backend

        function onJobEvent(ev: var): void {
            root.handleEvent(ev);
        }

        function onConnectedChanged(): void {
            if (Backend.connected)
                return;
            // The daemon died: its running jobs are gone.
            for (const job of priv.jobs.slice()) {
                if (!job.local && !root.isTerminal(job.state))
                    priv.update(job.id, {
                        state: "failed",
                        error: {
                            code: "backend_exited",
                            message: I18n.t("ui.error.backend_exited"),
                            details: Backend.lastError,
                            install: ""
                        }
                    });
            }
        }
    }

    ListModel {
        id: jobsModel
    }

    Timer {
        interval: 10000
        repeat: true
        running: root.count > 0 || Object.keys(priv.dismissed).length > 0
        onTriggered: priv.prune()
    }

    QtObject {
        id: priv

        property var jobs: []
        property var byId: ({})
        property var localToReal: ({})
        property var dismissed: ({})
        property var held: ({})
        property int localSeq: 0

        function makeJob(fields: var): var {
            const now = Date.now();
            return Object.assign({
                id: "",
                localId: "",
                local: false,
                state: "queued",
                action: "",
                label: "",
                files: [],
                paths: [],
                progress: null,
                message: "",
                warnings: [],
                result: null,
                error: null,
                createdAt: now,
                updatedAt: now,
                finishedAt: 0,
                cancelling: false,
                undoing: false,
                undone: false,
                undoMessage: "",
                undoError: null
            }, fields);
        }

        function displayName(path: string): string {
            if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(path) && !path.startsWith("file://"))
                return path;
            const p = path.replace(/\/+$/, "");
            const i = p.lastIndexOf("/");
            return i >= 0 ? p.slice(i + 1) : p;
        }

        function normaliseError(e: var): var {
            if (typeof e !== "object" || e === null)
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

        function rebuild(): void {
            const out = [];
            for (let i = 0; i < jobsModel.count; i++) {
                const j = byId[jobsModel.get(i).jobId];
                if (j !== undefined)
                    out.push(j);
            }
            jobs = out;
        }

        function insert(job: var): void {
            byId[job.id] = job;
            jobsModel.append({
                jobId: job.id,
                job: job
            });
            rebuild();
            root.jobAdded(job);
        }

        function update(id: string, patch: var): var {
            const old = byId[id];
            if (old === undefined)
                return null;
            const now = Date.now();
            const next = Object.assign({}, old, patch, {
                updatedAt: now
            });
            const finishing = !root.isTerminal(old.state) && root.isTerminal(next.state);
            if (finishing)
                next.finishedAt = now;
            // Whatever ended the job (its own event, a daemon crash, a failed run request), a pending cancel is over:
            // a finished card must show its outcome, not "Stopping…".
            if (root.isTerminal(next.state))
                next.cancelling = false;
            byId[id] = next;
            const idx = root.indexOf(id);
            if (idx >= 0)
                jobsModel.setProperty(idx, "job", next);
            rebuild();
            root.jobUpdated(next);
            if (finishing)
                root.jobFinished(next);
            return next;
        }

        function rename(oldId: string, newId: string): void {
            const old = byId[oldId];
            if (old === undefined)
                return;
            const next = Object.assign({}, old, {
                id: newId,
                local: false,
                updatedAt: Date.now()
            });
            let idx = -1;
            for (let i = 0; i < jobsModel.count; i++) {
                if (jobsModel.get(i).jobId === oldId) {
                    idx = i;
                    break;
                }
            }
            delete byId[oldId];
            byId[newId] = next;
            if (held[oldId] !== undefined) {
                delete held[oldId];
                held[newId] = true;
            }
            if (idx >= 0) {
                jobsModel.setProperty(idx, "jobId", newId);
                jobsModel.setProperty(idx, "job", next);
            }
            rebuild();
            root.jobUpdated(next);
        }

        function remove(id: string, notify: bool): void {
            let idx = -1;
            for (let i = 0; i < jobsModel.count; i++) {
                if (jobsModel.get(i).jobId === id) {
                    idx = i;
                    break;
                }
            }
            if (idx >= 0)
                jobsModel.remove(idx, 1);
            const existed = byId[id] !== undefined;
            delete byId[id];
            delete held[id];
            // Forget "local-N" → id mappings of a removed daemon job (a removed placeholder keeps its mapping so
            // callers holding the local id still reach the daemon row).
            for (const local of Object.keys(localToReal)) {
                if (localToReal[local] === id)
                    delete localToReal[local];
            }
            rebuild();
            if (existed && notify)
                root.jobRemoved(id);
        }

        function findPlaceholder(action: string, files: var): string {
            const wanted = Array.isArray(files) ? files.join("\n") : null;
            for (const job of jobs) {
                if (!job.local || job.action !== action)
                    continue;
                if (wanted === null || job.files.join("\n") === wanted)
                    return job.id;
            }
            return "";
        }

        // Config hud.timeout_ms as the HUD reads it: auto-dismiss delay of finished cards, 0 = never.
        function hudTimeoutMs(): int {
            const t = Number(Backend.config?.hud?.timeout_ms ?? 7000);
            return isFinite(t) && t >= 0 ? Math.round(t) : 7000;
        }

        // A finished row that no view holds is due once its HUD card would have gone, plus pruneAfterMs of slack
        // (a hovered card pauses its countdown). Rows whose card stays until × are never due.
        function pruneDue(job: var, now: real, timeout: int): bool {
            if (held[job.id] !== undefined || job.finishedAt <= 0)
                return false;
            if (job.state === "cancelled")
                return now - job.finishedAt > root.pruneAfterMs;
            if (job.state !== "done" || job.undoing || job.undoError)
                return false;
            if (job.undone)
                return now - job.updatedAt > root.pruneAfterMs;
            if (timeout === 0)
                return false;
            if (job.result?.kind === "share") {
                // The card keeps its Stop sharing button until the share server stops (expires: Unix seconds).
                const expires = Number(job.result?.expires);
                return isFinite(expires) && expires > 0 && now - expires * 1000 > root.pruneAfterMs;
            }
            return now - job.finishedAt > timeout + root.pruneAfterMs;
        }

        function prune(): void {
            const now = Date.now();
            const timeout = hudTimeoutMs();
            const failed = [];
            for (const job of jobs.slice()) {
                if (pruneDue(job, now, timeout))
                    remove(job.id, true);
                else if (job.state === "failed")
                    failed.push(job);
            }
            const excess = failed.length - Math.max(0, root.maxFailedKept);
            for (let i = 0; i < excess; i++)
                remove(failed[i].id, true);
            for (const id of Object.keys(dismissed)) {
                if (now - dismissed[id] > 30 * 60000)
                    delete dismissed[id];
            }
        }
    }
}
