pragma ComponentBehavior: Bound

// HudWindow — the job cards column (SPEC §2.4): an Overlay layer surface (namespace "dropwheel-hud", keyboard focus
// None) anchored to config hud.position (default bottom-right) with a small margin, respecting other exclusive zones
// (Caelestia's bar). Its input mask is the cards column only, so the transparent shadow padding is click-through.
// Cards stack newest at the bottom; at most `maxVisible` (5) are shown, older ones are summarised in a chip with
// "Clear finished". The surface exists only while there are jobs (visible: false unmaps it).
//
//   property var targetScreen     ShellScreen to show the cards on (null = compositor default)
//   property string position      "bottom-right" | "bottom-left" | "top-right" | "top-left" | "top" | "bottom" |
//                                 "left" | "right"   (default: config hud.position)
//   property int timeoutMs        auto-dismiss of finished cards (default: config hud.timeout_ms, 7000; 0 = never)
//   property int maxVisible       default 5
//   readonly property real cardWidth
//   readonly property point centre    global logical centre of the cards column (the wheel shrinks towards it)
//   signal expandRequested(var job)   a card asked for its full result view (DialogWindow.showResult)

import QtQuick
import Quickshell
import Quickshell.Wayland
import "../services"
import "../components"
import "../wheel/WheelText.js" as WT

PanelWindow {
    id: root

    property var targetScreen: null
    property string position: {
        const p = String(Backend.config?.hud?.position ?? "bottom-right");
        return ["bottom-right", "bottom-left", "top-right", "top-left", "top", "bottom", "left", "right"].indexOf(p) !== -1 ? p : "bottom-right";
    }
    property int timeoutMs: {
        const t = Number(Backend.config?.hud?.timeout_ms ?? 7000);
        return isFinite(t) && t >= 0 ? Math.round(t) : 7000;
    }
    property int maxVisible: 5

    readonly property real cardWidth: Math.round(360 * Theme.scale)
    readonly property real pad: Math.round(20 * Theme.scale)
    readonly property bool atTop: root.position.startsWith("top")
    readonly property bool atBottom: root.position.startsWith("bottom")
    readonly property bool atLeft: root.position.endsWith("left")
    readonly property bool atRight: root.position.endsWith("right")
    readonly property int hiddenCount: Math.max(0, Jobs.model.count - root.maxVisible)
    readonly property int finishedCount: Jobs.count - Jobs.activeCount

    readonly property point centre: {
        const s = root.targetScreen;
        const sx = Number(s?.x ?? 0) || 0;
        const sy = Number(s?.y ?? 0) || 0;
        const sw = Number(s?.width ?? 0) || 0;
        const sh = Number(s?.height ?? 0) || 0;
        const w = root.implicitWidth;
        const h = Math.max(root.implicitHeight, Math.round(120 * Theme.scale));
        const m = Theme.padding.small;
        const x = root.atLeft ? sx + m + w / 2 : root.atRight ? sx + sw - m - w / 2 : sx + sw / 2;
        const y = root.atTop ? sy + m + h / 2 : root.atBottom ? sy + sh - m - h / 2 : sy + sh / 2;
        return Qt.point(x, y);
    }

    signal expandRequested(var job)

    screen: root.targetScreen
    visible: Jobs.model.count > 0
    color: "transparent"
    implicitWidth: Math.ceil(root.cardWidth + root.pad * 2)
    implicitHeight: Math.ceil(Math.max(1, column.implicitHeight) + root.pad * 2)
    anchors.top: root.atTop
    anchors.bottom: root.atBottom
    anchors.left: root.atLeft
    anchors.right: root.atRight
    margins.top: Theme.padding.small
    margins.bottom: Theme.padding.small
    margins.left: Theme.padding.small
    margins.right: Theme.padding.small
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dropwheel-hud"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region {
        item: column
    }

    Column {
        id: column

        x: root.pad
        y: root.pad
        width: root.cardWidth
        spacing: Theme.spacing.small

        Accessible.role: Accessible.List
        Accessible.name: WT.tr(I18n, "hud.a11y.name")

        // Older cards beyond maxVisible.
        StyledRect {
            anchors.right: root.atLeft ? undefined : parent.right
            anchors.left: root.atLeft ? parent.left : undefined
            visible: root.hiddenCount > 0
            width: hiddenRow.implicitWidth + Theme.padding.medium * 2
            height: hiddenRow.implicitHeight + Theme.padding.extraSmall * 2
            radius: height / 2
            color: Theme.tPalette.m3surfaceContainer
            border.width: Math.max(1, Math.round(Theme.scale))
            border.color: Qt.alpha(Theme.palette.m3outlineVariant, 0.3)

            Elevation {
                anchors.fill: parent
                radius: parent.radius
                level: 2
                z: -1
            }

            Row {
                id: hiddenRow

                anchors.centerIn: parent
                spacing: Theme.spacing.small

                StyledText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: WT.trn(I18n, "hud.hidden_jobs", root.hiddenCount)
                    color: Theme.palette.m3onSurfaceVariant
                    font.pointSize: Theme.font.size.small
                }

                TextButton {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.finishedCount > 0
                    type: TextButton.Text
                    text: WT.tr(I18n, "hud.clear_finished")
                    fontSize: Theme.font.size.small
                    horizontalPadding: Theme.padding.small
                    verticalPadding: Theme.padding.extraSmall
                    onClicked: Jobs.clearFinished()
                }
            }
        }

        Repeater {
            model: Jobs.model

            delegate: JobCard {
                required property int index

                visible: index >= Jobs.model.count - root.maxVisible
                cardWidth: root.cardWidth
                timeoutMs: root.timeoutMs
                slideFrom: root.atLeft ? -1 : 1
                onExpandRequested: job => root.expandRequested(job)
            }
        }
    }
}
