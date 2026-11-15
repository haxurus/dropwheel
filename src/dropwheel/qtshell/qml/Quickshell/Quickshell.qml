pragma Singleton
import QtQuick
import DropwheelHost

// Quickshell singleton: the screens (global logical px, stable objects) and the environment.
QtObject {
    readonly property var screens: DesktopHost.screens

    function env(name: string): string {
        return DesktopHost.env(name);
    }
}
