pragma ComponentBehavior: Bound

// AdjustEditor — dialog "adjust" for images: live approximate preview (MultiEffect brightness / contrast /
// saturation, colorization for temperature + tint, a light blur for negative sharpness, radial vignette overlay) of
// Backend.preview "image", sliders grouped in Light / Colour / Detail (−100..100, 0 = neutral) each with its own reset,
// press-and-hold "Original" (or hold \) to compare, Ctrl+Z undo. The same adjustments apply to every file; the
// filmstrip only switches the previewed file.
// Params (SPEC §5.6): adjust {exposure, brightness, contrast, saturation, temperature, tint, sharpen, vignette},
// plus the simple params of the action (Options).

import QtQuick
import QtQuick.Effects
import QtQuick.Shapes
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    readonly property var keys: ["exposure", "brightness", "contrast", "temperature", "tint", "saturation", "sharpen", "vignette"]
    readonly property var sections: [
        {
            id: "light",
            icon: "light_mode",
            keys: ["exposure", "brightness", "contrast"]
        },
        {
            id: "color",
            icon: "palette",
            keys: ["temperature", "tint", "saturation"]
        },
        {
            id: "detail",
            icon: "deblur",
            keys: ["sharpen", "vignette"]
        }
    ]
    property var values: priv.neutral()
    property var infos: ({})
    property bool showOriginal: false

    readonly property var info: root.infos[root.currentFile?.path ?? ""] ?? null
    readonly property bool ready: root.info?.status === "ready"
    readonly property bool neutral: root.keys.every(k => (root.values[k] ?? 0) === 0)
    readonly property int changedCount: root.keys.filter(k => (root.values[k] ?? 0) !== 0).length

    // MultiEffect approximation of the backend's adjustments.
    readonly property real fxBrightness: DU.clamp(((root.values.brightness ?? 0) * 0.45 + (root.values.exposure ?? 0) * 0.6) / 100, -1, 1)
    readonly property real fxContrast: DU.clamp((root.values.contrast ?? 0) / 100 * 0.8, -1, 1)
    readonly property real fxSaturation: DU.clamp((root.values.saturation ?? 0) / 100, -1, 1)
    readonly property real fxColorization: DU.clamp(Math.max(Math.abs(root.values.temperature ?? 0), Math.abs(root.values.tint ?? 0)) / 100 * 0.32, 0, 1)
    readonly property color fxColor: {
        const t = root.values.temperature ?? 0;
        const n = root.values.tint ?? 0;
        const warm = t >= 0 ? Qt.color("#ff8a1f") : Qt.color("#2f8bff");
        const tint = n >= 0 ? Qt.color("#ff2fd0") : Qt.color("#1fd65a");
        const at = Math.abs(t);
        const an = Math.abs(n);
        if (at + an === 0)
            return warm;
        return Theme.mix(warm, tint, an / (at + an));
    }
    readonly property real fxBlur: (root.values.sharpen ?? 0) < 0 ? -(root.values.sharpen ?? 0) / 100 * 0.35 : 0
    readonly property real fxVignette: (root.values.vignette ?? 0) / 100

    function label(key: string): string {
        return I18n.t("dialog.adjust." + key);
    }

    function setValue(key: string, v: real): void {
        const n = Math.round(DU.clamp(v, -100, 100));
        if ((root.values[key] ?? 0) === n)
            return;
        priv.keyHistory();
        const next = Object.assign({}, root.values);
        next[key] = n;
        root.values = next;
        root.dirty = !root.neutral;
    }

    function resetKey(key: string): void {
        if ((root.values[key] ?? 0) === 0)
            return;
        root.pushHistory();
        const next = Object.assign({}, root.values);
        next[key] = 0;
        root.values = next;
        root.dirty = !root.neutral;
    }

    function pushHistory(): void {
        const h = priv.history.slice(-79);
        h.push(Object.assign({}, root.values));
        priv.history = h;
        priv.lastKeyHistory = Date.now();
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        root.values = h.pop();
        priv.history = h;
        priv.lastKeyHistory = 0;
        root.dirty = !root.neutral;
        return true;
    }

    function reset(): void {
        if (root.neutral)
            return;
        root.pushHistory();
        root.values = priv.neutral();
        root.dirty = false;
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        const adjust = {};
        for (const k of root.keys)
            adjust[k] = Math.round(root.values[k] ?? 0);
        root.apply(Object.assign({}, extra, {
            adjust: adjust
        }));
    }

    applyLabel: I18n.t("dialog.adjust.apply")
    applyIcon: "tune"
    canApply: !root.neutral
    footerHint: root.neutral ? I18n.t("dialog.adjust.hint") : I18n.tn("dialog.adjust.changed", root.changedCount)

    Component.onCompleted: {
        const d = root.paramDefault("adjust", null);
        if (d && typeof d === "object") {
            const v = priv.neutral();
            for (const k of root.keys) {
                const n = DU.num(d[k]);
                if (n !== null)
                    v[k] = Math.round(DU.clamp(n, -100, 100));
            }
            root.values = v;
        }
        priv.ensure(root.currentFile);
    }
    Component.onDestruction: priv.life.alive = false
    // Deferred: the first evaluation of currentFile can happen inside another binding (binding loop).
    onCurrentFileChanged: Qt.callLater(() => priv.ensure(root.currentFile))

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Backslash && !event.isAutoRepeat) {
            root.showOriginal = true;
            event.accepted = true;
        }
    }
    Keys.onReleased: event => {
        if (event.key === Qt.Key_Backslash && !event.isAutoRepeat) {
            root.showOriginal = false;
            event.accepted = true;
        }
    }

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastKeyHistory: 0

        function neutral(): var {
            return {
                exposure: 0,
                brightness: 0,
                contrast: 0,
                saturation: 0,
                temperature: 0,
                tint: 0,
                sharpen: 0,
                vignette: 0
            };
        }

        // One undo step per gesture: changes closer than 800 ms are grouped.
        function keyHistory(): void {
            const now = Date.now();
            if (now - priv.lastKeyHistory > 800) {
                const h = priv.history.slice(-79);
                h.push(Object.assign({}, root.values));
                priv.history = h;
            }
            priv.lastKeyHistory = now;
        }

        function ensure(file: var): void {
            const path = file?.path ?? "";
            if (path === "")
                return;
            const existing = root.infos[path];
            if (existing && existing.status !== "error")
                return;
            priv.setInfo(path, {
                status: "loading"
            });
            const life = priv.life;
            Backend.preview("image", {
                path: path,
                max: 1600
            }, (r, e) => {
                if (!life.alive)
                    return;
                const ok = !e && r && typeof r.png === "string" && r.png !== "" && DU.num(r.width) > 0 && DU.num(r.height) > 0;
                priv.setInfo(path, ok ? {
                    status: "ready",
                    png: String(r.png),
                    ow: Number(r.width),
                    oh: Number(r.height)
                } : {
                    status: "error",
                    error: String(e?.message ?? I18n.t("dialog.preview.failed"))
                });
            });
        }

        function setInfo(path: string, value: var): void {
            const next = Object.assign({}, root.infos);
            next[path] = value;
            root.infos = next;
        }
    }

    // ------------------------------------------------------------------ preview
    Item {
        id: main

        anchors.left: parent.left
        anchors.right: side.left
        anchors.rightMargin: Theme.spacing.large
        anchors.top: parent.top
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.medium : 0

        StyledRect {
            anchors.fill: parent
            radius: Theme.rounding.large
            color: Theme.palette.m3surfaceContainerLow
        }

        PreviewCanvas {
            id: canvas

            anchors.fill: parent
            padding: Theme.s(20)
            source: root.ready ? String(root.info?.png ?? "") : ""
            sourceWidth: DU.num(root.info?.ow) ?? 0
            sourceHeight: DU.num(root.info?.oh) ?? 0
            imageVisible: root.showOriginal || root.neutral
            visible: root.ready

            MultiEffect {
                anchors.fill: parent
                visible: !root.showOriginal && !root.neutral
                source: canvas.image
                autoPaddingEnabled: false
                brightness: root.fxBrightness
                contrast: root.fxContrast
                saturation: root.fxSaturation
                colorization: root.fxColorization
                colorizationColor: root.fxColor
                blurEnabled: root.fxBlur > 0
                blur: root.fxBlur
                blurMax: 16
            }

            // Vignette (darkens, or lightens for negative values, towards the corners).
            Shape {
                id: vignette

                readonly property real w: parent.width
                readonly property real h: parent.height
                readonly property real a: Math.min(0.85, Math.abs(root.fxVignette) * 0.85)
                readonly property real c: root.fxVignette >= 0 ? 0 : 1

                anchors.fill: parent
                visible: !root.showOriginal && root.fxVignette !== 0

                ShapePath {
                    strokeWidth: -1
                    strokeColor: "transparent"
                    startX: 0
                    startY: 0
                    fillGradient: RadialGradient {
                        centerX: vignette.w / 2
                        centerY: vignette.h / 2
                        centerRadius: Math.max(1, Math.sqrt(vignette.w * vignette.w + vignette.h * vignette.h) / 2)
                        focalX: vignette.w / 2
                        focalY: vignette.h / 2
                        focalRadius: 0

                        GradientStop {
                            position: 0.0
                            color: Qt.rgba(vignette.c, vignette.c, vignette.c, 0)
                        }
                        GradientStop {
                            position: 0.5
                            color: Qt.rgba(vignette.c, vignette.c, vignette.c, 0)
                        }
                        GradientStop {
                            position: 1.0
                            color: Qt.rgba(vignette.c, vignette.c, vignette.c, vignette.a)
                        }
                    }

                    PathLine {
                        x: vignette.w
                        y: 0
                    }
                    PathLine {
                        x: vignette.w
                        y: vignette.h
                    }
                    PathLine {
                        x: 0
                        y: vignette.h
                    }
                    PathLine {
                        x: 0
                        y: 0
                    }
                }
            }
        }

        // Press-and-hold to compare with the original.
        StyledRect {
            id: compare

            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Theme.spacing.medium
            visible: root.ready && !root.neutral
            implicitWidth: compareRow.implicitWidth + Theme.padding.large * 2
            implicitHeight: Theme.s(36)
            width: implicitWidth
            height: implicitHeight
            radius: height / 2
            color: root.showOriginal ? Theme.palette.m3primary : Qt.alpha(Theme.palette.m3inverseSurface, 0.85)

            Row {
                id: compareRow

                anchors.centerIn: parent
                spacing: Theme.spacing.small

                MaterialIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "compare"
                    size: Theme.font.iconSize.small
                    color: root.showOriginal ? Theme.palette.m3onPrimary : Theme.palette.m3inverseOnSurface
                }

                StyledText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: I18n.t("dialog.adjust.compare")
                    font.pointSize: Theme.font.size.small
                    font.weight: Font.Medium
                    color: root.showOriginal ? Theme.palette.m3onPrimary : Theme.palette.m3inverseOnSurface
                }
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onPressed: root.showOriginal = true
                onReleased: root.showOriginal = false
                onCanceled: root.showOriginal = false
            }
        }

        StyledText {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: Theme.padding.large
            visible: root.ready
            text: root.showOriginal ? I18n.t("dialog.adjust.original") : I18n.t("dialog.adjust.approximate")
            font.pointSize: Theme.font.size.small
            color: Theme.palette.m3onSurfaceVariant
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading"
            error: root.info?.status === "error" ? (String(root.info?.error ?? "") || I18n.t("dialog.preview.failed")) : ""
            onRetry: priv.ensure(root.currentFile)
        }
    }

    // ------------------------------------------------------------------ sliders
    ScrollArea {
        id: side

        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.medium : 0
        width: Theme.s(320)

        Column {
            width: side.width - Theme.s(10)
            spacing: Theme.spacing.large

            Repeater {
                model: root.sections

                Column {
                    id: section

                    required property var modelData

                    width: parent.width
                    spacing: Theme.spacing.extraSmall

                    Row {
                        spacing: Theme.spacing.small

                        MaterialIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            text: String(section.modelData.icon)
                            size: Theme.font.iconSize.small
                            color: Theme.palette.m3primary
                        }

                        StyledText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: I18n.t("dialog.adjust.section." + section.modelData.id)
                            font.pointSize: Theme.font.size.normal
                            font.weight: Font.Medium
                        }
                    }

                    Repeater {
                        model: section.modelData.keys

                        Column {
                            id: control

                            required property string modelData
                            readonly property int value: root.values[control.modelData] ?? 0

                            width: parent.width
                            spacing: 0

                            Item {
                                width: parent.width
                                height: Math.max(sliderLabel.implicitHeight, resetButton.height)

                                StyledText {
                                    id: sliderLabel

                                    anchors.left: parent.left
                                    anchors.right: valueLabel.left
                                    anchors.rightMargin: Theme.spacing.small
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: root.label(control.modelData) + (control.modelData === "sharpen" && control.value > 0 ? " " + I18n.t("dialog.adjust.not_previewed") : "")
                                    elide: Text.ElideRight
                                    font.pointSize: Theme.font.size.normal

                                    MouseArea {
                                        anchors.fill: parent
                                        onDoubleClicked: root.resetKey(control.modelData)
                                    }
                                }

                                StyledText {
                                    id: valueLabel

                                    anchors.right: resetButton.left
                                    anchors.rightMargin: Theme.spacing.extraSmall
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: (control.value > 0 ? "+" : "") + I18n.formatCount(control.value)
                                    font.pointSize: Theme.font.size.normal
                                    font.weight: Font.Medium
                                    font.features: ({
                                            "tnum": 1
                                        })
                                    color: control.value !== 0 ? Theme.palette.m3primary : Theme.palette.m3onSurfaceVariant
                                }

                                IconButton {
                                    id: resetButton

                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    type: ButtonBase.Text
                                    icon: "restart_alt"
                                    iconSize: Theme.font.iconSize.small
                                    opacity: control.value !== 0 ? 1 : 0
                                    disabled: control.value === 0
                                    tooltip: I18n.t("dialog.adjust.reset_one", {
                                        name: root.label(control.modelData)
                                    })
                                    onClicked: root.resetKey(control.modelData)
                                }
                            }

                            M3Slider {
                                id: slider

                                width: parent.width
                                from: -100
                                to: 100
                                stepSize: 1
                                disabled: !root.ready
                                showValue: false
                                value: control.value
                                onMoved: root.setValue(control.modelData, slider.value)

                                Connections {
                                    target: root

                                    function onValuesChanged(): void {
                                        if (!slider.pressed && slider.value !== control.value)
                                            slider.value = control.value;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: options.hasFields

                StyledText {
                    text: I18n.t("dialog.options")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                ParamForm {
                    id: options

                    width: parent.width
                    height: implicitHeight
                    node: root.node
                    files: root.files
                    host: root.host
                    embedded: true
                    focus: false
                    exclude: root.optionExcludes(["adjust", "per_file"])
                }
            }

            StyledText {
                width: parent.width
                text: I18n.t("dialog.adjust.keys")
                wrapMode: Text.Wrap
                font.pointSize: Theme.font.size.small
                color: Theme.palette.m3outline
            }
        }
    }

    Filmstrip {
        id: strip

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Theme.s(76)
        files: root.fileList
        currentIndex: root.currentIndex
        showSameForAll: false
        onSelected: index => root.currentIndex = index
    }
}
