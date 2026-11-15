// Dropwheel — KWin script (Plasma 6, Wayland and X11).
//
// On Wayland, KWin tells ordinary programs where the pointer is only while it is over their own
// windows. Dropwheel's window (dropwheel.qtshell.kde) owns a name on the session bus and this
// script tells it, while the pointer moves and at most every 20 ms:
//
//   io.github.haxurus.Dropwheel.SetPointer(x, y, width, height)
//
// x and y are relative to the top left corner of the virtual screen, width and height are its
// size, all in KWin's logical pixels. The script also registers Dropwheel's keyboard shortcut
// (System Settings → Keyboard → Shortcuts → KWin), which calls Open(). No key and no button is
// read here; nothing is sent when the pointer does not move.

const SERVICE = "io.github.haxurus.Dropwheel";
const PATH = "/io/github/haxurus/Dropwheel";
const IFACE = "io.github.haxurus.Dropwheel";

const timer = new QTimer();
timer.singleShot = true;
timer.interval = 20;
timer.timeout.connect(function () {
    const p = workspace.cursorPos;
    const g = workspace.virtualScreenGeometry;
    callDBus(SERVICE, PATH, IFACE, "SetPointer", p.x - g.x, p.y - g.y, g.width, g.height);
});

workspace.cursorPosChanged.connect(function () {
    if (!timer.active)
        timer.start();
});

// "dropwheel setup kde" writes trigger.shortcut to kwinrc, [Script-dropwheel] Shortcut
registerShortcut("Dropwheel", "Dropwheel: open the wheel with the copied files",
                 readConfig("Shortcut", "Ctrl+Alt+D"), function () {
    callDBus(SERVICE, PATH, IFACE, "Open");
});
