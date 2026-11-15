import QtQuick
import DropwheelHost

// While active: a mouse press outside the input region of every window in `windows`, or the host losing the
// foreground, emits cleared().
QtObject {
    id: grab

    property list<QtObject> windows
    property bool active: false

    signal cleared

    function inside(x: real, y: real): bool {
        for (let i = 0; i < grab.windows.length; i++) {
            const w = grab.windows[i];
            if (w && w.visible && typeof w.maskContains === "function" && w.maskContains(x, y))
                return true;
        }
        return false;
    }

    property Connections pressWatch: Connections {
        target: DesktopHost
        enabled: grab.active

        function onGlobalPress(x: real, y: real): void {
            if (!grab.inside(x, y))
                grab.cleared();
        }
    }

    property Connections appWatch: Connections {
        target: Qt.application
        enabled: grab.active

        function onStateChanged(): void {
            if (Qt.application.state !== Qt.ApplicationActive && grab.active)
                grab.cleared();
        }
    }
}
