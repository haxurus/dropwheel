pragma ComponentBehavior: Bound

// ColorPicker — colour chooser: preview + hex field (#rgb, #rrggbb, #rrggbbaa when `alpha`), eyedropper through
// Hypr.pickColor() (hyprpicker; button disabled with an install hint when hyprpicker is missing), swatches from the
// current Caelestia scheme and a basic palette.
//
//   property string value          "#rrggbb" (or "#rrggbbaa" when alpha and not opaque); lower case, CSS order
//   property bool alpha: false     accept/keep an alpha channel (adds a "transparent" swatch)
//   property bool showTheme: true  scheme swatches row
//   property bool showBasic: true  basic palette row
//   property bool eyedropper: true show the eyedropper button
//   property var extraColors: []   more swatches (hex strings)
//   property string label          hex field label (default I18n "Hex colour")
//   readonly property color color  `value` as a QML colour (Qt "#aarrggbb" order handled for you)
//   readonly property bool picking this picker's eyedropper is active (Hypr.colorPicking covers every picker; a host
//                                  that dims the screen must clear the dimming while that one is true)
//   signal picked(string value)    the user chose a colour (swatch, valid hex input, eyedropper)
//   function normalise(text) → "#rrggbb" | "#rrggbbaa" | "" (invalid)
//   function pickFromScreen()

import QtQuick
import "../services"

Item {
    id: root

    property string value: "#ffffff"
    property bool alpha: false
    property bool showTheme: true
    property bool showBasic: true
    property bool eyedropper: true
    property var extraColors: []
    property string label: I18n.t("ui.color.hex")

    readonly property color color: root.toQtColor(root.value)
    readonly property bool picking: priv.picking

    signal picked(string value)

    function normalise(text: var): string {
        let s = String(text ?? "").trim().toLowerCase();
        if (s.startsWith("#"))
            s = s.slice(1);
        if (/^[0-9a-f]{3}$/.test(s))
            s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
        else if (root.alpha && /^[0-9a-f]{4}$/.test(s))
            s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2] + s[3] + s[3];
        if (/^[0-9a-f]{6}$/.test(s))
            return "#" + s;
        if (/^[0-9a-f]{8}$/.test(s)) {
            if (!root.alpha)
                return "";
            return s.endsWith("ff") ? "#" + s.slice(0, 6) : "#" + s;
        }
        return "";
    }

    function toQtColor(hex: string): color {
        const n = root.normalise(hex);
        if (n === "")
            return "transparent";
        if (n.length === 9)
            return "#" + n.slice(7, 9) + n.slice(1, 7);
        return n;
    }

    function fromQtColor(c: color): string {
        const h = v => {
            const x = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16);
            return x.length < 2 ? "0" + x : x;
        };
        const rgb = "#" + h(c.r) + h(c.g) + h(c.b);
        return root.alpha && c.a < 0.999 ? rgb + h(c.a) : rgb;
    }

    function choose(hex: string): void {
        const n = root.normalise(hex);
        if (n === "")
            return;
        if (n !== root.value)
            root.value = n;
        // Also while the field has focus: a swatch click does not take it, and the old text must not stay behind.
        if (root.normalise(hexField.text) !== n)
            hexField.text = n;
        root.picked(n);
    }

    function pickFromScreen(): void {
        if (priv.picking)
            return;
        const pick = {
            live: true
        };
        const started = Hypr.pickColor(hex => {
            if (!pick.live) // the callback is also called (with "") when the pick could not start
                return;
            pick.live = false;
            priv.picking = false;
            if (hex !== "")
                root.choose(hex);
        });
        if (started) {
            priv.currentPick = pick;
            priv.picking = true;
        } else {
            pick.live = false;
        }
    }

    implicitWidth: Math.max(Math.round(300 * Theme.scale), column.implicitWidth)
    implicitHeight: column.implicitHeight

    // Typed text never differs here (onTextEdited sets the value it normalises to), so a half-typed "ff0" is not
    // rewritten; any other change of value (Reset, a host's binding) must reach the field even while it has focus.
    onValueChanged: {
        if (root.normalise(hexField.text) !== root.normalise(root.value))
            hexField.text = root.normalise(root.value) || root.value;
    }
    Component.onCompleted: {
        hexField.text = root.normalise(root.value) || root.value;
        if (root.eyedropper)
            Hypr.checkColorPicker();
    }
    Component.onDestruction: {
        if (priv.currentPick !== null)
            priv.currentPick.live = false; // a dialog closed during a pick: hyprpicker's answer has nowhere to go
    }

    QtObject {
        id: priv

        property bool picking: false
        property var currentPick: null
        readonly property real swatchSize: Math.round(28 * Theme.scale)
        readonly property real boxHeight: Math.round(48 * Theme.scale)

        readonly property var themeColours: [Theme.palette.m3primary, Theme.palette.m3secondary, Theme.palette.m3tertiary, Theme.palette.m3error, Theme.palette.m3success, Theme.palette.m3primaryContainer, Theme.palette.m3secondaryContainer, Theme.palette.m3tertiaryContainer, Theme.palette.m3surface, Theme.palette.m3surfaceContainerHighest, Theme.palette.m3onSurface, Theme.palette.m3outline].map(c => root.fromQtColor(c))

        readonly property var basicColours: {
            const base = ["#ffffff", "#9e9e9e", "#000000", "#f44336", "#ff9800", "#ffeb3b", "#4caf50", "#009688", "#2196f3", "#3f51b5", "#9c27b0", "#e91e63", "#795548"];
            return root.alpha ? base.concat(["#00000000"]) : base;
        }

        readonly property var extra: {
            const list = root.extraColors;
            const out = [];
            const n = list?.length ?? 0;
            for (let i = 0; i < n; i++) {
                const v = root.normalise(list[i]);
                if (v !== "")
                    out.push(v);
            }
            return out;
        }
    }

    Column {
        id: column

        width: root.width
        spacing: Theme.spacing.medium

        Row {
            width: parent.width
            spacing: Theme.spacing.medium

            // Current colour preview (checkerboard behind translucent colours).
            Item {
                id: preview

                width: priv.boxHeight
                height: priv.boxHeight

                Grid {
                    anchors.fill: parent
                    columns: 4
                    visible: root.color.a < 0.999
                    clip: true

                    Repeater {
                        model: 16

                        Rectangle {
                            required property int index

                            width: preview.width / 4
                            height: preview.height / 4
                            color: (Math.floor(index / 4) + index) % 2 === 0 ? "#cccccc" : "#ffffff"
                        }
                    }
                }

                StyledRect {
                    anchors.fill: parent
                    radius: Theme.rounding.medium
                    color: root.color
                    border.width: Math.max(1, Math.round(Theme.scale))
                    border.color: Theme.palette.m3outlineVariant
                }
            }

            M3TextField {
                id: hexField

                width: parent.width - preview.width - (pickButton.visible ? pickButton.width + parent.spacing : 0) - parent.spacing
                label: root.label
                mono: true
                prefixText: ""
                maximumLength: root.alpha ? 9 : 7
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase | Qt.ImhPreferLowercase
                validator: t => root.normalise(t) !== "" ? true : I18n.t("ui.color.invalid")
                onTextEdited: hexField.commit()
                onEditingFinished: {
                    if (hexField.commit())
                        hexField.text = root.normalise(hexField.text);
                }

                // Applies the typed text if it is a colour (a different one fires picked()); false when invalid.
                function commit(): bool {
                    const n = root.normalise(hexField.text);
                    if (n === "")
                        return false;
                    if (n !== root.normalise(root.value)) {
                        root.value = n;
                        root.picked(n);
                    }
                    return true;
                }
            }

            IconButton {
                id: pickButton

                y: Math.round((priv.boxHeight - height) / 2)
                visible: root.eyedropper
                type: ButtonBase.Tonal
                icon: root.picking ? "hourglass_top" : "colorize"
                disabled: !Hypr.colorPickerAvailable || Hypr.colorPicking
                tooltip: Hypr.colorPickerAvailable ? I18n.t("ui.color.eyedropper") : I18n.t("ui.color.eyedropper_missing")
                onClicked: root.pickFromScreen()
            }
        }

        SwatchRow {
            visible: root.showTheme
            title: I18n.t("ui.color.theme")
            colours: priv.themeColours
        }

        SwatchRow {
            visible: root.showBasic || priv.extra.length > 0
            title: I18n.t("ui.color.basic")
            colours: (root.showBasic ? priv.basicColours : []).concat(priv.extra)
        }
    }

    component SwatchRow: Column {
        id: swatchRow

        property string title
        property var colours: []

        width: column.width
        spacing: Theme.spacing.small

        StyledText {
            text: swatchRow.title
            color: Theme.palette.m3onSurfaceVariant
            font.pointSize: Theme.font.size.small
            font.weight: Font.Medium
        }

        Flow {
            width: parent.width
            spacing: Theme.spacing.small

            Repeater {
                model: swatchRow.colours

                Item {
                    id: swatch

                    required property var modelData
                    readonly property string hex: String(modelData)
                    readonly property color qtColour: root.toQtColor(hex)
                    readonly property bool selected: root.normalise(root.value) === root.normalise(hex)

                    width: priv.swatchSize
                    height: priv.swatchSize

                    Rectangle {
                        // checkerboard hint for transparent swatches
                        anchors.fill: parent
                        radius: width / 2
                        visible: swatch.qtColour.a < 0.999
                        color: "#ffffff"
                        border.width: Math.max(1, Math.round(Theme.scale))
                        border.color: "#cccccc"

                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width * 0.75
                            height: Math.max(1, Math.round(2 * Theme.scale))
                            rotation: -45
                            color: Theme.palette.m3error
                        }
                    }

                    StyledRect {
                        anchors.fill: parent
                        radius: width / 2
                        color: swatch.qtColour
                        border.width: Math.max(1, Math.round(Theme.scale))
                        border.color: Theme.palette.m3outlineVariant
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width + Math.round(6 * Theme.scale)
                        height: width
                        radius: width / 2
                        color: "transparent"
                        border.width: Math.max(1, Math.round(2 * Theme.scale))
                        border.color: Theme.palette.m3primary
                        visible: swatch.selected
                    }

                    MaterialIcon {
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        visible: swatch.selected
                        text: "check"
                        size: Theme.font.iconSize.small
                        color: swatch.qtColour.a < 0.5 ? Theme.palette.m3onSurface : Theme.on(swatch.qtColour)
                    }

                    StateLayer {
                        radius: width / 2
                        color: Theme.on(swatch.qtColour)
                        onClicked: root.choose(swatch.hex)
                    }

                    Tooltip {
                        text: swatch.hex
                        delay: 700
                    }
                }
            }
        }
    }

}
