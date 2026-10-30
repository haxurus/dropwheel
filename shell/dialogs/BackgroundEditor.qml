pragma ComponentBehavior: Bound

// BackgroundEditor — dialog "background" (Tangerine "Add BG"): live preview of the image (Backend.preview "image")
// placed on a backdrop with padding, rounded corners and a drop shadow, inside an optional canvas ratio.
// Backdrops: gradient presets (from the Caelestia palette + classic ones, angle slider), solid colours (+ custom colour
// picker), a blurred copy of the image itself, or a custom image (file picker). Ctrl+Z undo. The same background is
// used for every file; the filmstrip only switches the previewed file.
// Params (SPEC §5.6): background {padding, radius, shadow (px of the output), ratio ("auto" | "1:1" | "16:9" | "4:5"),
// fill {type: "color" | "gradient" | "blur" | "image", colors: [hex…], angle (degrees, 0 = to top, clockwise), image}},
// plus the simple params of the action (Options).

import QtQuick
import QtQuick.Effects
import QtQuick.Shapes
import Quickshell.Widgets
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    property var bg: priv.defaults()
    property var infos: ({})
    property bool showCustomColor: false

    readonly property var info: root.infos[root.currentFile?.path ?? ""] ?? null
    readonly property bool ready: root.info?.status === "ready"
    readonly property real ow: DU.num(root.info?.ow) ?? 0
    readonly property real oh: DU.num(root.info?.oh) ?? 0
    readonly property var fill: root.bg.fill ?? ({})
    readonly property string fillType: String(root.fill.type ?? "gradient")
    readonly property var colors: Array.isArray(root.fill.colors) && root.fill.colors.length > 0 ? root.fill.colors : ["#ffffff"]
    // Output canvas (original pixels).
    readonly property var canvasSize: {
        const p = Math.max(0, Number(root.bg.padding ?? 0));
        let W = root.ow + 2 * p;
        let H = root.oh + 2 * p;
        const r = DU.ratioOf(String(root.bg.ratio ?? "auto"));
        if (r !== null && W > 0 && H > 0) {
            if (W / H < r)
                W = H * r;
            else
                H = W / r;
        }
        return {
            w: Math.round(W),
            h: Math.round(H)
        };
    }

    readonly property var themePresets: [
        {
            id: "theme-primary",
            colors: [String(Theme.palette.m3primary), String(Theme.palette.m3tertiary)]
        },
        {
            id: "theme-container",
            colors: [String(Theme.palette.m3primaryContainer), String(Theme.palette.m3secondaryContainer)]
        },
        {
            id: "theme-tertiary",
            colors: [String(Theme.palette.m3tertiaryContainer), String(Theme.palette.m3primaryContainer), String(Theme.palette.m3secondary)]
        },
        {
            id: "theme-surface",
            colors: [String(Theme.palette.m3surfaceContainerHighest), String(Theme.palette.m3surfaceContainerLow)]
        }
    ]
    readonly property var classicPresets: [
        {
            id: "ocean",
            colors: ["#2193b0", "#6dd5ed"]
        },
        {
            id: "sunset",
            colors: ["#ff7e5f", "#feb47b"]
        },
        {
            id: "grape",
            colors: ["#8e2de2", "#4a00e0"]
        },
        {
            id: "mint",
            colors: ["#00b09b", "#96c93d"]
        },
        {
            id: "peach",
            colors: ["#ffecd2", "#fcb69f"]
        },
        {
            id: "candy",
            colors: ["#f857a6", "#ff5858"]
        },
        {
            id: "sky",
            colors: ["#a1c4fd", "#c2e9fb"]
        },
        {
            id: "aurora",
            colors: ["#00c6ff", "#0072ff", "#7f00ff"]
        },
        {
            id: "midnight",
            colors: ["#232526", "#414345"]
        }
    ]
    readonly property var solidColors: ["#ffffff", "#f2f2f2", "#d4d4d8", "#71717a", "#18181b", "#000000", String(Theme.palette.m3primary), String(Theme.palette.m3surfaceContainer)]
    readonly property var ratios: ["auto", "1:1", "16:9", "4:5"]

    function setBg(patch: var, fillPatch: var): void {
        priv.history.push(DU.copy(root.bg));
        if (priv.history.length > 80)
            priv.history.shift();
        const next = Object.assign({}, root.bg, patch ?? {});
        if (fillPatch)
            next.fill = Object.assign({}, root.bg.fill ?? {}, fillPatch);
        root.bg = next;
        root.dirty = !DU.same(root.bg, priv.initial);
    }

    // Slider drags: one undo step per gesture.
    function setBgLive(key: string, value: real): void {
        const now = Date.now();
        if (now - priv.lastLive > 800)
            priv.history.push(DU.copy(root.bg));
        priv.lastLive = now;
        const next = Object.assign({}, root.bg);
        next[key] = Math.round(value);
        root.bg = next;
        root.dirty = !DU.same(root.bg, priv.initial);
    }

    function setAngleLive(value: real): void {
        const now = Date.now();
        if (now - priv.lastLive > 800)
            priv.history.push(DU.copy(root.bg));
        priv.lastLive = now;
        const next = Object.assign({}, root.bg);
        next.fill = Object.assign({}, root.bg.fill ?? {}, {
            angle: Math.round(value)
        });
        root.bg = next;
        root.dirty = !DU.same(root.bg, priv.initial);
    }

    function choosePreset(p: var): void {
        root.setBg({
            preset: String(p.id)
        }, {
            type: "gradient",
            colors: p.colors.slice(),
            image: ""
        });
    }

    function chooseColor(c: string): void {
        root.setBg({
            preset: ""
        }, {
            type: "color",
            colors: [c],
            image: ""
        });
    }

    function chooseBlur(): void {
        root.setBg({
            preset: "blur"
        }, {
            type: "blur",
            colors: [],
            image: ""
        });
    }

    function chooseImage(): void {
        if (!root.host || !root.host.pickerAvailable)
            return;
        root.host.pickFile({
            title: I18n.t("dialog.background.choose_image"),
            filters: ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.avif", "*.heic", "*.bmp", "*.tif", "*.tiff", "*.gif"],
            filterName: I18n.t("dialog.background.images")
        }, path => {
            if (typeof path === "string" && path !== "")
                root.setBg({
                    preset: ""
                }, {
                    type: "image",
                    colors: [],
                    image: path
                });
        });
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        root.bg = priv.history.pop();
        priv.lastLive = 0;
        root.dirty = !DU.same(root.bg, priv.initial);
        return true;
    }

    function reset(): void {
        if (DU.same(root.bg, priv.initial))
            return;
        priv.history.push(DU.copy(root.bg));
        root.bg = DU.copy(priv.initial);
        root.dirty = false;
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        const f = root.bg.fill ?? {};
        const type = String(f.type ?? "gradient");
        const background = {
            padding: Math.round(Number(root.bg.padding ?? 0)),
            radius: Math.round(Number(root.bg.radius ?? 0)),
            shadow: Math.round(Number(root.bg.shadow ?? 0)),
            ratio: String(root.bg.ratio ?? "auto"),
            fill: {
                type: type,
                colors: (type === "color" || type === "gradient") ? root.colors.map(c => String(c)) : [],
                angle: Math.round(Number(f.angle ?? 135)),
                image: type === "image" ? String(f.image ?? "") : ""
            }
        };
        root.apply(Object.assign({}, extra, {
            background: background
        }));
    }

    applyLabel: I18n.t("dialog.background.apply")
    applyIcon: "wallpaper"
    canApply: root.fillType !== "image" || String(root.fill.image ?? "") !== ""
    footerHint: root.ready ? I18n.t("dialog.background.size", {
        w: I18n.formatCount(root.canvasSize.w),
        h: I18n.formatCount(root.canvasSize.h)
    }) : ""

    Component.onCompleted: {
        priv.initial = priv.defaults();
        root.bg = DU.copy(priv.initial);
        priv.ensure(root.currentFile);
    }
    Component.onDestruction: priv.life.alive = false
    // Deferred: the first evaluation of currentFile can happen inside another binding (binding loop).
    onCurrentFileChanged: Qt.callLater(() => priv.ensure(root.currentFile))

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastLive: 0
        property var initial: ({})

        function defaults(): var {
            const base = {
                padding: 160,
                radius: 24,
                shadow: 24,
                ratio: "auto",
                preset: "ocean",
                fill: {
                    type: "gradient",
                    colors: ["#2193b0", "#6dd5ed"],
                    angle: 135,
                    image: ""
                }
            };
            const d = root.paramDefault("background", null);
            if (!d || typeof d !== "object")
                return base;
            for (const k of ["padding", "radius", "shadow"]) {
                const n = DU.num(d[k]);
                if (n !== null)
                    base[k] = Math.max(0, Math.round(n));
            }
            if (root.ratios.indexOf(String(d.ratio ?? "")) !== -1)
                base.ratio = String(d.ratio);
            if (d.fill && typeof d.fill === "object") {
                const t = String(d.fill.type ?? "");
                if (["color", "gradient", "blur", "image"].indexOf(t) !== -1) {
                    base.fill.type = t;
                    base.preset = "";
                }
                if (Array.isArray(d.fill.colors) && d.fill.colors.length > 0)
                    base.fill.colors = d.fill.colors.map(c => String(c));
                if (DU.num(d.fill.angle) !== null)
                    base.fill.angle = Number(d.fill.angle);
                if (typeof d.fill.image === "string")
                    base.fill.image = d.fill.image;
            }
            return base;
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

        // Gradient line for a CSS-style angle over a w × h box: {x1, y1, x2, y2}.
        function gradientLine(angle: real, w: real, h: real): var {
            const a = angle * Math.PI / 180;
            const dx = Math.sin(a);
            const dy = -Math.cos(a);
            const len = Math.abs(w * dx) + Math.abs(h * dy);
            return {
                x1: w / 2 - dx * len / 2,
                y1: h / 2 - dy * len / 2,
                x2: w / 2 + dx * len / 2,
                y2: h / 2 + dy * len / 2
            };
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

        Item {
            id: frame

            // Displayed pixels per output pixel.
            readonly property real k: {
                const W = Math.max(1, root.canvasSize.w);
                const H = Math.max(1, root.canvasSize.h);
                return Math.min((main.width - Theme.s(40)) / W, (main.height - Theme.s(40)) / H);
            }

            anchors.centerIn: parent
            width: Math.max(1, Math.round(root.canvasSize.w * frame.k))
            height: Math.max(1, Math.round(root.canvasSize.h * frame.k))
            visible: root.ready
            clip: true

            // --- backdrop: solid colour
            Rectangle {
                anchors.fill: parent
                visible: root.fillType === "color"
                color: String(root.colors[0])
            }

            // --- backdrop: gradient
            Shape {
                id: gradientShape

                readonly property var line: priv.gradientLine(Number(root.fill.angle ?? 135), width, height)
                readonly property color c0: String(root.colors[0])
                readonly property color c2: String(root.colors[root.colors.length - 1])
                readonly property color c1: root.colors.length >= 3 ? String(root.colors[1]) : Theme.mix(gradientShape.c0, gradientShape.c2, 0.5)

                anchors.fill: parent
                visible: root.fillType === "gradient"

                ShapePath {
                    strokeWidth: -1
                    strokeColor: "transparent"
                    startX: 0
                    startY: 0
                    fillGradient: LinearGradient {
                        x1: gradientShape.line.x1
                        y1: gradientShape.line.y1
                        x2: gradientShape.line.x2
                        y2: gradientShape.line.y2

                        GradientStop {
                            position: 0
                            color: gradientShape.c0
                        }
                        GradientStop {
                            position: 0.5
                            color: gradientShape.c1
                        }
                        GradientStop {
                            position: 1
                            color: gradientShape.c2
                        }
                    }

                    PathLine {
                        x: gradientShape.width
                        y: 0
                    }
                    PathLine {
                        x: gradientShape.width
                        y: gradientShape.height
                    }
                    PathLine {
                        x: 0
                        y: gradientShape.height
                    }
                    PathLine {
                        x: 0
                        y: 0
                    }
                }
            }

            // --- backdrop: blurred copy of the image
            Image {
                id: blurSource

                anchors.fill: parent
                visible: false
                source: root.fillType === "blur" && root.ready ? Backend.fileUrl(String(root.info?.png ?? "")) : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                sourceSize.width: 400
            }

            MultiEffect {
                anchors.fill: parent
                visible: root.fillType === "blur"
                source: blurSource
                autoPaddingEnabled: false
                blurEnabled: true
                blur: 1.0
                blurMax: 64
                brightness: -0.06
                saturation: 0.15
            }

            // --- backdrop: custom image
            Image {
                anchors.fill: parent
                visible: root.fillType === "image"
                source: root.fillType === "image" && String(root.fill.image ?? "") !== "" ? Backend.fileUrl(String(root.fill.image)) : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                sourceSize.width: 1200
            }

            StyledText {
                anchors.centerIn: parent
                width: parent.width - Theme.s(24)
                visible: root.fillType === "image" && String(root.fill.image ?? "") === ""
                text: I18n.t("dialog.background.no_image")
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                color: Theme.palette.m3onSurfaceVariant
            }

            // --- the image, its shadow and rounded corners
            Item {
                id: photo

                readonly property real r: Math.min(Number(root.bg.radius ?? 0) * frame.k, Math.min(width, height) / 2)

                width: Math.max(1, Math.round(root.ow * frame.k))
                height: Math.max(1, Math.round(root.oh * frame.k))
                anchors.centerIn: parent

                RectangularShadow {
                    anchors.fill: parent
                    visible: Number(root.bg.shadow ?? 0) > 0
                    radius: photo.r
                    blur: Number(root.bg.shadow ?? 0) * frame.k * 1.5
                    spread: 0
                    offset.x: 0
                    offset.y: Number(root.bg.shadow ?? 0) * frame.k * 0.35
                    color: Qt.rgba(0, 0, 0, 0.45)
                }

                ClippingRectangle {
                    anchors.fill: parent
                    radius: photo.r
                    color: "transparent"

                    Image {
                        anchors.fill: parent
                        source: root.ready ? Backend.fileUrl(String(root.info?.png ?? "")) : ""
                        fillMode: Image.Stretch
                        asynchronous: true
                        smooth: true
                        mipmap: true
                    }
                }
            }
        }

        // Outline of the output canvas.
        Rectangle {
            x: frame.x - 1
            y: frame.y - 1
            width: frame.width + 2
            height: frame.height + 2
            visible: root.ready
            color: "transparent"
            border.width: 1
            border.color: Theme.palette.m3outlineVariant
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading"
            error: root.info?.status === "error" ? (String(root.info?.error ?? "") || I18n.t("dialog.preview.failed")) : ""
            onRetry: priv.ensure(root.currentFile)
        }
    }

    // ------------------------------------------------------------------ controls
    ScrollArea {
        id: side

        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.medium : 0
        width: Theme.s(330)

        Column {
            width: side.width - Theme.s(10)
            spacing: Theme.spacing.large

            // Gradients
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.background.gradients")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: root.themePresets.concat(root.classicPresets)

                        Swatch {
                            required property var modelData

                            colors: modelData.colors
                            selected: root.fillType === "gradient" && root.bg.preset === modelData.id
                            tooltip: I18n.t("dialog.background.preset." + modelData.id)
                            onClicked: root.choosePreset(modelData)
                        }
                    }
                }

                Column {
                    width: parent.width
                    spacing: 0
                    visible: root.fillType === "gradient"

                    StyledText {
                        text: I18n.t("dialog.background.angle", {
                            angle: Math.round(Number(root.fill.angle ?? 135))
                        })
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3onSurfaceVariant
                    }

                    LiveSlider {
                        width: parent.width
                        from: 0
                        to: 360
                        stepSize: 5
                        unit: "°"
                        current: Number(root.fill.angle ?? 135)
                        onMoved: root.setAngleLive(value)
                    }
                }
            }

            // Solid colours, blur, image
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.background.colors")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: root.solidColors

                        Swatch {
                            required property string modelData

                            colors: [modelData]
                            selected: root.fillType === "color" && String(root.colors[0]).toLowerCase() === modelData.toLowerCase()
                            tooltip: modelData
                            onClicked: root.chooseColor(modelData)
                        }
                    }

                    Swatch {
                        icon: "colorize"
                        colors: root.fillType === "color" ? [String(root.colors[0])] : []
                        selected: root.showCustomColor
                        tooltip: I18n.t("dialog.background.custom_color")
                        onClicked: root.showCustomColor = !root.showCustomColor
                    }
                }

                ColorPicker {
                    id: customPicker

                    width: parent.width
                    visible: root.showCustomColor
                    alpha: false
                    value: root.fillType === "color" ? String(root.colors[0]) : "#ffffff"
                    onPicked: value => {
                        root.chooseColor(value);
                        // The picker assigned `value` itself: follow the background again (undo, presets).
                        customPicker.value = Qt.binding(() => root.fillType === "color" ? String(root.colors[0]) : "#ffffff");
                    }
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Chip {
                        type: Chip.Filter
                        activeFocusOnTab: true
                        icon: "blur_on"
                        text: I18n.t("dialog.background.blur")
                        selected: root.fillType === "blur"
                        onClicked: root.chooseBlur()
                    }

                    Chip {
                        type: Chip.Filter
                        activeFocusOnTab: true
                        icon: "image"
                        text: root.fillType === "image" && String(root.fill.image ?? "") !== "" ? DU.baseName(String(root.fill.image)) : I18n.t("dialog.background.custom_image")
                        selected: root.fillType === "image"
                        disabled: !(root.host?.pickerAvailable ?? false)
                        tooltip: (root.host?.pickerAvailable ?? false) ? I18n.t("dialog.background.choose_image") : I18n.t("dialog.background.no_picker")
                        onClicked: root.chooseImage()
                    }
                }
            }

            // Sliders
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                Repeater {
                    model: [
                        {
                            key: "padding",
                            to: 600,
                            step: 4
                        },
                        {
                            key: "radius",
                            to: 200,
                            step: 1
                        },
                        {
                            key: "shadow",
                            to: 100,
                            step: 1
                        }
                    ]

                    Column {
                        id: sliderBox

                        required property var modelData

                        width: parent.width
                        spacing: 0

                        Item {
                            width: parent.width
                            height: sliderTitle.implicitHeight

                            StyledText {
                                id: sliderTitle

                                anchors.left: parent.left
                                text: I18n.t("dialog.background." + sliderBox.modelData.key)
                                font.pointSize: Theme.font.size.normal
                            }

                            StyledText {
                                anchors.right: parent.right
                                text: I18n.formatCount(Number(root.bg[sliderBox.modelData.key] ?? 0)) + " px"
                                font.pointSize: Theme.font.size.normal
                                font.weight: Font.Medium
                                font.features: ({
                                        "tnum": 1
                                    })
                                color: Theme.palette.m3primary
                            }
                        }

                        LiveSlider {
                            width: parent.width
                            from: 0
                            to: Number(sliderBox.modelData.to)
                            stepSize: Number(sliderBox.modelData.step)
                            unit: " px"
                            current: Number(root.bg[sliderBox.modelData.key] ?? 0)
                            onMoved: root.setBgLive(String(sliderBox.modelData.key), value)
                        }
                    }
                }
            }

            // Ratio
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.background.ratio")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: root.ratios

                        Chip {
                            required property string modelData

                            type: Chip.Filter
                            activeFocusOnTab: true
                            text: modelData === "auto" ? I18n.t("dialog.background.ratio_auto") : modelData
                            selected: String(root.bg.ratio ?? "auto") === modelData
                            onClicked: {
                                if (String(root.bg.ratio ?? "auto") !== modelData)
                                    root.setBg({
                                        ratio: modelData
                                    }, null);
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
                    exclude: root.optionExcludes(["background", "per_file"])
                }
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

    // Round swatch: one colour, a gradient of 2–3 colours, or an icon.
    component Swatch: Item {
        id: sw

        property var colors: []
        property bool selected: false
        property string icon: ""
        property string tooltip: ""
        readonly property color c0: sw.colors.length > 0 ? String(sw.colors[0]) : Theme.palette.m3surfaceContainerHighest
        readonly property color cN: sw.colors.length > 0 ? String(sw.colors[sw.colors.length - 1]) : Theme.palette.m3surfaceContainerHighest
        readonly property color cM: sw.colors.length >= 3 ? String(sw.colors[1]) : Theme.mix(sw.c0, sw.cN, 0.5)

        signal clicked

        implicitWidth: Theme.s(34)
        implicitHeight: Theme.s(34)
        activeFocusOnTab: true

        Keys.onPressed: event => {
            if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                sw.clicked();
                event.accepted = true;
            }
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -Math.round(3 * Theme.scale)
            radius: width / 2
            color: "transparent"
            border.width: Math.max(2, Math.round(2 * Theme.scale))
            border.color: sw.activeFocus ? Theme.palette.m3secondary : Theme.palette.m3primary
            visible: sw.selected || sw.activeFocus
        }

        Rectangle {
            anchors.fill: parent
            radius: width / 2
            border.width: Math.max(1, Math.round(Theme.scale))
            border.color: Qt.alpha(Theme.palette.m3outline, 0.5)
            gradient: Gradient {
                orientation: Gradient.Horizontal

                GradientStop {
                    position: 0
                    color: sw.c0
                }
                GradientStop {
                    position: 0.5
                    color: sw.cM
                }
                GradientStop {
                    position: 1
                    color: sw.cN
                }
            }
        }

        MaterialIcon {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 1
            visible: sw.icon !== ""
            text: sw.icon
            size: Theme.font.iconSize.small
            color: Theme.on(sw.c0)
        }

        StateLayer {
            radius: width / 2
            color: Theme.on(sw.c0)
            onClicked: sw.clicked()
        }

        Tooltip {
            text: sw.tooltip
        }
    }

    // M3Slider that follows `current` unless the user is dragging it.
    component LiveSlider: M3Slider {
        id: ls

        property real current: 0

        showValue: true
        value: ls.current
        onCurrentChanged: {
            if (!ls.pressed && ls.value !== ls.current)
                ls.value = ls.current;
        }
    }
}
