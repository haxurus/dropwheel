pragma Singleton
import QtQuick
import DropwheelHost

// focusedMonitor: the screen under the cursor (Windows has no focused monitor of its own).
QtObject {
    readonly property var focusedMonitor: ({
            name: DesktopHost.focusedScreenName
        })
}
