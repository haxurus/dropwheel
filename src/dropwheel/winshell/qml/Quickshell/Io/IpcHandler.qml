import QtQuick
import DropwheelHost

// IpcHandler: the host's IPC server calls the functions declared on the instance (see winshell/host.py, which
// only forwards the names it knows). "true" / "false" arguments become booleans, as with `qs ipc call`.
IpcEndpoint {
    id: ipc

    onCallRequested: (ticket, fn, args) => {
        const f = ipc[fn];
        if (typeof f !== "function") {
            ipc.reply(ticket, null, "no function " + fn);
            return;
        }
        const list = Array.from(args ?? []).map(a => a === "true" ? true : a === "false" ? false : a);
        try {
            const result = f.apply(ipc, list);
            ipc.reply(ticket, result === undefined ? null : result, "");
        } catch (e) {
            ipc.reply(ticket, null, String(e));
        }
    }
}
