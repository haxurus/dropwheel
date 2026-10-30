pragma Singleton
pragma ComponentBehavior: Bound

// Theme — Caelestia-compatible colours, tokens, fonts and motion for the Dropwheel shell.
//
// Import with `import "../services"` (or `import "services"` from shell.qml).
//
// COLOURS
//   palette.m3<role>        opaque M3 colours (all Caelestia roles: m3primary, m3onPrimary, m3surface,
//                           m3surfaceContainer{Lowest,Low,,High,Highest}, m3outline, m3error, m3success,
//                           m3successContainer, m3primaryFixed..., m3*_paletteKeyColor, ...). Read from
//                           ${XDG_STATE_HOME:-~/.local/state}/caelestia/scheme.json (watched, live reload);
//                           built-in Caelestia dark fallback when the file is missing or invalid.
//   tPalette.m3<role>       the same roles passed through layer(): translucent when Caelestia transparency is
//                           enabled (layer 0 for background/surface/surfaceDim/surfaceBright/surfaceVariant/
//                           inverseSurface, layer 1 otherwise). Use it for panel backgrounds; text uses palette.
//   light                   true when the scheme mode is "light".
//   schemeName, schemeFlavour, schemeVariant, schemeLoaded (false = fallback palette in use)
//   transparency.{enabled, base, layers}   effective values from ~/.config/caelestia/shell.json
//   wallLuminance           wallpaper luminance used by alterColour (0.5 when unknown; writable)
//   layer(c, layer)         Caelestia Colours.layer(): layer 0 → alpha(base); other → alterColour(c, layers, layer)
//   alterColour(c, a, layer), getLuminance(c), on(c) (readable foreground for c), alpha(c, a), mix(a, b, t)
//
// TOKENS (all multiplied by `scale` and by Caelestia's appearance.<group>.scale from shell.json)
//   rounding.{extraSmall 4, small 8, medium 12, large 16, largeIncreased 20, extraLarge 28,
//             extraLargeIncreased 32, extraExtraLarge 48, full (very large: clamp to size/2)}
//   spacing.{extraSmall 4, small 8, medium 12, large 16, largeIncreased 20, extraLarge 28, extraLargeIncreased 32,
//            extraExtraLarge 48}
//   padding.{same keys as spacing}
//   font.family.{sans, mono, icon, clock}   sans = GoogleSansFlex (FontLoader from the Caelestia install, system or
//                           user path) → Rubik → Qt default; mono = "CaskaydiaCove NF" (or another installed mono);
//                           icon = "Material Symbols Rounded"
//   font.size.{small 11, smaller 12, normal 13, larger 15, large 18, extraLarge 28}  (point sizes, scaled)
//   font.iconSize.{small 15, medium 18, large 24, extraLarge 36}                  (point sizes, scaled)
//   font.axes               variable-font axes for the sans family ({ROND: 25} for GoogleSansFlex, else {})
//   font.sansIsVariable     true when GoogleSansFlex was loaded
//   anim.durations.{small 200, normal 400, large 600, extraLarge 1000, expressiveFastSpatial 350,
//                   expressiveDefaultSpatial 500, expressiveSlowSpatial 650, expressiveFastEffects 150,
//                   expressiveDefaultEffects 200, expressiveSlowEffects 300}   (ms × appearance.anim.durations.scale)
//   anim.curves.{emphasized, emphasizedAccel, emphasizedDecel, standard, standardAccel, standardDecel,
//                expressiveFastSpatial, expressiveDefaultSpatial, expressiveSlowSpatial, expressiveFastEffects,
//                expressiveDefaultEffects, expressiveSlowEffects}
//                           BezierSpline arrays: `easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.anim.curves.standard`
//   scale                   Backend.config.wheel.scale (clamped 0.5–3, default 1)
//   reducedMotion           true when Caelestia's anim duration scale is 0
//
// FUNCTIONS
//   s(px)                   px × scale, rounded (helper for literal sizes)
//   reload()                re-read scheme.json and shell.json

import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root

    // ------------------------------------------------------------------ public colour state
    readonly property bool light: priv.light
    readonly property string schemeName: priv.schemeName
    readonly property string schemeFlavour: priv.schemeFlavour
    readonly property string schemeVariant: priv.schemeVariant
    readonly property bool schemeLoaded: priv.schemeLoaded

    readonly property M3Palette palette: M3Palette {}
    readonly property M3TPalette tPalette: M3TPalette {}
    readonly property Transparency transparency: Transparency {}
    property real wallLuminance: 0.5

    // ------------------------------------------------------------------ scale
    readonly property real scale: {
        const raw = Number(Backend.config?.wheel?.scale ?? 1);
        if (!isFinite(raw) || raw <= 0)
            return 1;
        return Math.max(0.5, Math.min(3, raw));
    }
    readonly property bool reducedMotion: priv.animScale <= 0

    // ------------------------------------------------------------------ tokens
    readonly property RoundingTokens rounding: RoundingTokens {
        k: root.scale * priv.roundingScale
    }
    readonly property SpaceTokens spacing: SpaceTokens {
        k: root.scale * priv.spacingScale
    }
    readonly property SpaceTokens padding: SpaceTokens {
        k: root.scale * priv.paddingScale
    }
    readonly property FontTokens font: FontTokens {}
    readonly property AnimTokens anim: AnimTokens {}

    component RoundingTokens: QtObject {
        property real k: 1
        readonly property int extraSmall: Math.round(4 * k)
        readonly property int small: Math.round(8 * k)
        readonly property int medium: Math.round(12 * k)
        readonly property int large: Math.round(16 * k)
        readonly property int largeIncreased: Math.round(20 * k)
        readonly property int extraLarge: Math.round(28 * k)
        readonly property int extraLargeIncreased: Math.round(32 * k)
        readonly property int extraExtraLarge: Math.round(48 * k)
        readonly property int full: k > 0 ? 100000 : 0
    }

    component SpaceTokens: QtObject {
        property real k: 1
        readonly property int extraSmall: Math.round(4 * k)
        readonly property int small: Math.round(8 * k)
        readonly property int medium: Math.round(12 * k)
        readonly property int large: Math.round(16 * k)
        readonly property int largeIncreased: Math.round(20 * k)
        readonly property int extraLarge: Math.round(28 * k)
        readonly property int extraLargeIncreased: Math.round(32 * k)
        readonly property int extraExtraLarge: Math.round(48 * k)
    }

    component FontFamilies: QtObject {
        readonly property string sans: priv.sansFamily
        readonly property string mono: priv.monoFamily
        readonly property string icon: "Material Symbols Rounded"
        readonly property string clock: priv.hasFamily("Rubik") ? "Rubik" : priv.sansFamily
    }

    component FontSizes: QtObject {
        readonly property real k: root.scale * priv.fontScale
        readonly property real small: 11 * k
        readonly property real smaller: 12 * k
        readonly property real normal: 13 * k
        readonly property real larger: 15 * k
        readonly property real large: 18 * k
        readonly property real extraLarge: 28 * k
    }

    component IconSizes: QtObject {
        readonly property real k: root.scale * priv.fontScale
        readonly property real small: 15 * k
        readonly property real medium: 18 * k
        readonly property real large: 24 * k
        readonly property real extraLarge: 36 * k
    }

    component FontTokens: QtObject {
        readonly property FontFamilies family: FontFamilies {}
        readonly property FontSizes size: FontSizes {}
        readonly property IconSizes iconSize: IconSizes {}
        readonly property bool sansIsVariable: priv.sansIsFlex
        readonly property var axes: priv.sansIsFlex ? ({
                "ROND": 25
            }) : ({})
    }

    component AnimDurations: QtObject {
        readonly property real k: priv.animScale
        readonly property int small: Math.round(200 * k)
        readonly property int normal: Math.round(400 * k)
        readonly property int large: Math.round(600 * k)
        readonly property int extraLarge: Math.round(1000 * k)
        readonly property int expressiveFastSpatial: Math.round(350 * k)
        readonly property int expressiveDefaultSpatial: Math.round(500 * k)
        readonly property int expressiveSlowSpatial: Math.round(650 * k)
        readonly property int expressiveFastEffects: Math.round(150 * k)
        readonly property int expressiveDefaultEffects: Math.round(200 * k)
        readonly property int expressiveSlowEffects: Math.round(300 * k)
    }

    component AnimCurves: QtObject {
        readonly property list<real> emphasized: [0.05, 0, 2 / 15, 0.06, 1 / 6, 0.4, 5 / 24, 0.82, 0.25, 1, 1, 1]
        readonly property list<real> emphasizedAccel: [0.3, 0, 0.8, 0.15, 1, 1]
        readonly property list<real> emphasizedDecel: [0.05, 0.7, 0.1, 1, 1, 1]
        readonly property list<real> standard: [0.2, 0, 0, 1, 1, 1]
        readonly property list<real> standardAccel: [0.3, 0, 1, 1, 1, 1]
        readonly property list<real> standardDecel: [0, 0, 0, 1, 1, 1]
        readonly property list<real> expressiveFastSpatial: [0.42, 1.67, 0.21, 0.9, 1, 1]
        readonly property list<real> expressiveDefaultSpatial: [0.38, 1.21, 0.22, 1, 1, 1]
        readonly property list<real> expressiveSlowSpatial: [0.39, 1.29, 0.35, 0.98, 1, 1]
        readonly property list<real> expressiveFastEffects: [0.31, 0.94, 0.34, 1, 1, 1]
        readonly property list<real> expressiveDefaultEffects: [0.34, 0.8, 0.34, 1, 1, 1]
        readonly property list<real> expressiveSlowEffects: [0.34, 0.88, 0.34, 1, 1, 1]
    }

    component AnimTokens: QtObject {
        readonly property AnimDurations durations: AnimDurations {}
        readonly property AnimCurves curves: AnimCurves {}
    }

    // ------------------------------------------------------------------ colour helpers (Caelestia algorithm)
    function getLuminance(c: color): real {
        if (c.r == 0 && c.g == 0 && c.b == 0)
            return 0;
        return Math.sqrt(0.299 * (c.r ** 2) + 0.587 * (c.g ** 2) + 0.114 * (c.b ** 2));
    }

    function alterColour(c: color, a: real, layer: int): color {
        const luminance = getLuminance(c);
        const offset = (!light || layer == 1 ? 1 : -layer / 2) * (light ? 0.2 : 0.3) * (1 - transparency.base) * (1 + wallLuminance * (light ? (layer == 1 ? 3 : 1) : 2.5));
        if (luminance <= 0) {
            // Caelestia divides by zero here (pure black); lift black to a grey of luminance `offset` instead.
            const v = Math.max(0, Math.min(1, offset));
            return Qt.rgba(v, v, v, a);
        }
        const scale = (luminance + offset) / luminance;
        const r = Math.max(0, Math.min(1, c.r * scale));
        const g = Math.max(0, Math.min(1, c.g * scale));
        const b = Math.max(0, Math.min(1, c.b * scale));
        return Qt.rgba(r, g, b, a);
    }

    function layer(c: color, layer: var): color {
        if (!transparency.enabled)
            return c;
        return layer === 0 ? Qt.alpha(c, transparency.base) : alterColour(c, transparency.layers, layer ?? 1);
    }

    function on(c: color): color {
        if (c.hslLightness < 0.5)
            return Qt.hsla(c.hslHue, c.hslSaturation, 0.9, 1);
        return Qt.hsla(c.hslHue, c.hslSaturation, 0.1, 1);
    }

    function alpha(c: color, a: real): color {
        return Qt.alpha(c, a);
    }

    function mix(a: color, b: color, t: real): color {
        const k = Math.max(0, Math.min(1, t));
        return Qt.rgba(a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k, a.a + (b.a - a.a) * k);
    }

    function s(px: real): int {
        return Math.round(px * root.scale);
    }

    function reload(): void {
        schemeFile.reload();
        shellConfigFile.reload();
    }

    // ------------------------------------------------------------------ loading
    function applyScheme(text: string): void {
        let scheme = null;
        if (text) {
            try {
                scheme = JSON.parse(text);
            } catch (e) {
                console.warn("Theme: invalid Caelestia scheme.json:", e);
                scheme = null;
            }
        }
        const colours = (scheme && typeof scheme === "object" && scheme.colours && typeof scheme.colours === "object") ? scheme.colours : null;
        const isLight = colours !== null && scheme.mode === "light";

        const next = Object.assign({}, priv.darkDefaults, isLight ? priv.lightSuccess : {});
        if (colours !== null) {
            for (const name of Object.keys(colours)) {
                if (name.startsWith("term"))
                    continue;
                const prop = "m3" + name;
                if (!Object.prototype.hasOwnProperty.call(next, prop))
                    continue;
                const hex = priv.normaliseHex(colours[name]);
                if (hex !== "")
                    next[prop] = hex;
            }
        }

        priv.light = isLight;
        priv.schemeName = colours !== null ? String(scheme.name ?? "") : "";
        priv.schemeFlavour = colours !== null ? String(scheme.flavour ?? "") : "";
        priv.schemeVariant = colours !== null ? String(scheme.variant ?? "") : "";
        priv.schemeLoaded = colours !== null;

        for (const prop of Object.keys(next)) {
            if (!Qt.colorEqual(root.palette[prop], next[prop]))
                root.palette[prop] = next[prop];
        }
    }

    function applyShellConfig(text: string): void {
        let cfg = null;
        if (text) {
            try {
                cfg = JSON.parse(text);
            } catch (e) {
                console.warn("Theme: invalid Caelestia shell.json:", e);
                cfg = null;
            }
        }
        const app = (cfg && typeof cfg === "object" && cfg.appearance && typeof cfg.appearance === "object") ? cfg.appearance : {};
        const tr = (app.transparency && typeof app.transparency === "object") ? app.transparency : {};

        priv.trEnabled = typeof tr.enabled === "boolean" ? tr.enabled : false;
        priv.trBase = priv.number(tr.base, 0.85, 0, 1);
        priv.trLayers = priv.number(tr.layers, 0.4, 0, 1);
        priv.roundingScale = priv.number(app.rounding?.scale, 1, 0, 4);
        priv.spacingScale = priv.number(app.spacing?.scale, 1, 0, 4);
        priv.paddingScale = priv.number(app.padding?.scale, 1, 0, 4);
        priv.fontScale = priv.number(app.font?.scale, 1, 0.25, 4);
        priv.animScale = priv.number(app.anim?.durations?.scale, 1, 0, 10);
    }

    QtObject {
        id: priv

        property bool light: false
        property string schemeName: ""
        property string schemeFlavour: ""
        property string schemeVariant: ""
        property bool schemeLoaded: false

        property bool trEnabled: false
        property real trBase: 0.85
        property real trLayers: 0.4
        property real roundingScale: 1
        property real spacingScale: 1
        property real paddingScale: 1
        property real fontScale: 1
        property real animScale: 1

        readonly property string home: {
            const h = Quickshell.env("HOME");
            return (typeof h === "string" && h !== "") ? h : "";
        }
        readonly property string stateHome: {
            const v = Quickshell.env("XDG_STATE_HOME");
            if (typeof v === "string" && v.startsWith("/"))
                return v;
            return home !== "" ? home + "/.local/state" : "";
        }
        readonly property string configHome: {
            const v = Quickshell.env("XDG_CONFIG_HOME");
            if (typeof v === "string" && v.startsWith("/"))
                return v;
            return home !== "" ? home + "/.config" : "";
        }
        readonly property string schemePath: stateHome !== "" ? stateHome + "/caelestia/scheme.json" : ""
        readonly property string shellConfigPath: configHome !== "" ? configHome + "/caelestia/shell.json" : ""

        readonly property string gsfFile: "GoogleSansFlex-VariableFont_GRAD,ROND,opsz,slnt,wdth,wght.ttf"
        readonly property string gsfSystemUrl: "file:///etc/xdg/quickshell/caelestia/assets/google-sans-flex/" + gsfFile
        readonly property string gsfUserUrl: configHome !== "" ? "file://" + configHome + "/quickshell/caelestia/assets/google-sans-flex/" + gsfFile : ""

        // Lower-cased installed family names. Qt.fontFamilies() is a QStringList sequence, not a JS Array
        // (Array.isArray() is false for it), so it is copied; fontconfig matches family names case-insensitively.
        readonly property var families: {
            const set = {};
            const list = Qt.fontFamilies();
            for (let i = 0; i < list.length; i++)
                set[String(list[i]).toLowerCase()] = true;
            return set;
        }

        readonly property string sansFamily: {
            if (gsfSystem.status === FontLoader.Ready && gsfSystem.name !== "")
                return gsfSystem.name;
            if (gsfUser.status === FontLoader.Ready && gsfUser.name !== "")
                return gsfUser.name;
            // Qt.application is QQuickApplication at run time (it has `font`); qmllint only knows QQmlApplication.
            return pickFamily(["GoogleSansFlex", "Google Sans Flex", "Rubik"], String(Qt.application.font.family ?? "") || "sans-serif");
        }
        readonly property bool sansIsFlex: sansFamily.replace(/\s+/g, "").toLowerCase() === "googlesansflex"
        readonly property string monoFamily: pickFamily(["CaskaydiaCove NF", "CaskaydiaCove Nerd Font", "CaskaydiaCove Nerd Font Mono", "JetBrainsMono Nerd Font", "JetBrains Mono", "Cascadia Code", "DejaVu Sans Mono"], "monospace")

        // Caelestia M3Palette defaults (pink dark) — used when scheme.json is missing or invalid.
        readonly property var darkDefaults: ({
                "m3primary_paletteKeyColor": "#a8627b",
                "m3secondary_paletteKeyColor": "#8e6f78",
                "m3tertiary_paletteKeyColor": "#986e4c",
                "m3neutral_paletteKeyColor": "#807477",
                "m3neutral_variant_paletteKeyColor": "#837377",
                "m3background": "#191114",
                "m3onBackground": "#efdfe2",
                "m3surface": "#191114",
                "m3surfaceDim": "#191114",
                "m3surfaceBright": "#403739",
                "m3surfaceContainerLowest": "#130c0e",
                "m3surfaceContainerLow": "#22191c",
                "m3surfaceContainer": "#261d20",
                "m3surfaceContainerHigh": "#31282a",
                "m3surfaceContainerHighest": "#3c3235",
                "m3onSurface": "#efdfe2",
                "m3surfaceVariant": "#514347",
                "m3onSurfaceVariant": "#d5c2c6",
                "m3inverseSurface": "#efdfe2",
                "m3inverseOnSurface": "#372e30",
                "m3outline": "#9e8c91",
                "m3outlineVariant": "#514347",
                "m3shadow": "#000000",
                "m3scrim": "#000000",
                "m3surfaceTint": "#ffb0ca",
                "m3primary": "#ffb0ca",
                "m3onPrimary": "#541d34",
                "m3primaryContainer": "#6f334a",
                "m3onPrimaryContainer": "#ffd9e3",
                "m3inversePrimary": "#8b4a62",
                "m3secondary": "#e2bdc7",
                "m3onSecondary": "#422932",
                "m3secondaryContainer": "#5a3f48",
                "m3onSecondaryContainer": "#ffd9e3",
                "m3tertiary": "#f0bc95",
                "m3onTertiary": "#48290c",
                "m3tertiaryContainer": "#b58763",
                "m3onTertiaryContainer": "#000000",
                "m3error": "#ffb4ab",
                "m3onError": "#690005",
                "m3errorContainer": "#93000a",
                "m3onErrorContainer": "#ffdad6",
                "m3success": "#b5ccba",
                "m3onSuccess": "#213528",
                "m3successContainer": "#374b3e",
                "m3onSuccessContainer": "#d1e9d6",
                "m3primaryFixed": "#ffd9e3",
                "m3primaryFixedDim": "#ffb0ca",
                "m3onPrimaryFixed": "#39071f",
                "m3onPrimaryFixedVariant": "#6f334a",
                "m3secondaryFixed": "#ffd9e3",
                "m3secondaryFixedDim": "#e2bdc7",
                "m3onSecondaryFixed": "#2b151d",
                "m3onSecondaryFixedVariant": "#5a3f48",
                "m3tertiaryFixed": "#ffdcc3",
                "m3tertiaryFixedDim": "#f0bc95",
                "m3onTertiaryFixed": "#2f1500",
                "m3onTertiaryFixedVariant": "#623f21"
            })

        // Caelestia's fixed success colours for light schemes (used only if a light scheme lacks them).
        readonly property var lightSuccess: ({
                "m3success": "#4f6354",
                "m3onSuccess": "#ffffff",
                "m3successContainer": "#d1e8d5",
                "m3onSuccessContainer": "#0c1f13"
            })

        function hasFamily(name: string): bool {
            return families[String(name).toLowerCase()] === true;
        }

        function pickFamily(candidates: var, fallback: string): string {
            for (const name of candidates) {
                if (hasFamily(name))
                    return name;
            }
            return fallback;
        }

        function normaliseHex(value: var): string {
            if (typeof value !== "string")
                return "";
            const hex = value.startsWith("#") ? value.slice(1) : value;
            if (/^[0-9a-fA-F]{6}$/.test(hex))
                return "#" + hex.toLowerCase();
            if (/^[0-9a-fA-F]{8}$/.test(hex))
                return "#" + hex.slice(6, 8).toLowerCase() + hex.slice(0, 6).toLowerCase(); // rrggbbaa → #aarrggbb
            return "";
        }

        function number(value: var, fallback: real, min: real, max: real): real {
            const n = Number(value);
            if (value === undefined || value === null || value === "" || typeof value === "boolean" || !isFinite(n))
                return fallback;
            return Math.max(min, Math.min(max, n));
        }
    }

    FileView {
        id: schemeFile

        path: priv.schemePath
        watchChanges: true
        printErrors: false
        onFileChanged: schemeFile.reload()
        onLoaded: root.applyScheme(schemeFile.text())
        onLoadFailed: root.applyScheme("")
    }

    FileView {
        id: shellConfigFile

        path: priv.shellConfigPath
        watchChanges: true
        printErrors: false
        onFileChanged: shellConfigFile.reload()
        onLoaded: root.applyShellConfig(shellConfigFile.text())
        onLoadFailed: root.applyShellConfig("")
    }

    FontLoader {
        id: gsfSystem

        source: priv.gsfSystemUrl
    }

    FontLoader {
        id: gsfUser

        // Only tried when the system-wide Caelestia install does not have the font (avoids a second warning).
        source: gsfSystem.status === FontLoader.Error ? priv.gsfUserUrl : ""
    }

    component Transparency: QtObject {
        readonly property bool enabled: priv.trEnabled
        readonly property real base: Math.max(0, Math.min(1, priv.trBase - (root.light ? 0.1 : 0)))
        readonly property real layers: Math.max(0, Math.min(1, priv.trLayers))
    }

    component M3TPalette: QtObject {
        readonly property color m3primary_paletteKeyColor: root.layer(root.palette.m3primary_paletteKeyColor)
        readonly property color m3secondary_paletteKeyColor: root.layer(root.palette.m3secondary_paletteKeyColor)
        readonly property color m3tertiary_paletteKeyColor: root.layer(root.palette.m3tertiary_paletteKeyColor)
        readonly property color m3neutral_paletteKeyColor: root.layer(root.palette.m3neutral_paletteKeyColor)
        readonly property color m3neutral_variant_paletteKeyColor: root.layer(root.palette.m3neutral_variant_paletteKeyColor)
        readonly property color m3background: root.layer(root.palette.m3background, 0)
        readonly property color m3onBackground: root.layer(root.palette.m3onBackground)
        readonly property color m3surface: root.layer(root.palette.m3surface, 0)
        readonly property color m3surfaceDim: root.layer(root.palette.m3surfaceDim, 0)
        readonly property color m3surfaceBright: root.layer(root.palette.m3surfaceBright, 0)
        readonly property color m3surfaceContainerLowest: root.layer(root.palette.m3surfaceContainerLowest)
        readonly property color m3surfaceContainerLow: root.layer(root.palette.m3surfaceContainerLow)
        readonly property color m3surfaceContainer: root.layer(root.palette.m3surfaceContainer)
        readonly property color m3surfaceContainerHigh: root.layer(root.palette.m3surfaceContainerHigh)
        readonly property color m3surfaceContainerHighest: root.layer(root.palette.m3surfaceContainerHighest)
        readonly property color m3onSurface: root.layer(root.palette.m3onSurface)
        readonly property color m3surfaceVariant: root.layer(root.palette.m3surfaceVariant, 0)
        readonly property color m3onSurfaceVariant: root.layer(root.palette.m3onSurfaceVariant)
        readonly property color m3inverseSurface: root.layer(root.palette.m3inverseSurface, 0)
        readonly property color m3inverseOnSurface: root.layer(root.palette.m3inverseOnSurface)
        readonly property color m3outline: root.layer(root.palette.m3outline)
        readonly property color m3outlineVariant: root.layer(root.palette.m3outlineVariant)
        readonly property color m3shadow: root.layer(root.palette.m3shadow)
        readonly property color m3scrim: root.layer(root.palette.m3scrim)
        readonly property color m3surfaceTint: root.layer(root.palette.m3surfaceTint)
        readonly property color m3primary: root.layer(root.palette.m3primary)
        readonly property color m3onPrimary: root.layer(root.palette.m3onPrimary)
        readonly property color m3primaryContainer: root.layer(root.palette.m3primaryContainer)
        readonly property color m3onPrimaryContainer: root.layer(root.palette.m3onPrimaryContainer)
        readonly property color m3inversePrimary: root.layer(root.palette.m3inversePrimary)
        readonly property color m3secondary: root.layer(root.palette.m3secondary)
        readonly property color m3onSecondary: root.layer(root.palette.m3onSecondary)
        readonly property color m3secondaryContainer: root.layer(root.palette.m3secondaryContainer)
        readonly property color m3onSecondaryContainer: root.layer(root.palette.m3onSecondaryContainer)
        readonly property color m3tertiary: root.layer(root.palette.m3tertiary)
        readonly property color m3onTertiary: root.layer(root.palette.m3onTertiary)
        readonly property color m3tertiaryContainer: root.layer(root.palette.m3tertiaryContainer)
        readonly property color m3onTertiaryContainer: root.layer(root.palette.m3onTertiaryContainer)
        readonly property color m3error: root.layer(root.palette.m3error)
        readonly property color m3onError: root.layer(root.palette.m3onError)
        readonly property color m3errorContainer: root.layer(root.palette.m3errorContainer)
        readonly property color m3onErrorContainer: root.layer(root.palette.m3onErrorContainer)
        readonly property color m3success: root.layer(root.palette.m3success)
        readonly property color m3onSuccess: root.layer(root.palette.m3onSuccess)
        readonly property color m3successContainer: root.layer(root.palette.m3successContainer)
        readonly property color m3onSuccessContainer: root.layer(root.palette.m3onSuccessContainer)
        readonly property color m3primaryFixed: root.layer(root.palette.m3primaryFixed)
        readonly property color m3primaryFixedDim: root.layer(root.palette.m3primaryFixedDim)
        readonly property color m3onPrimaryFixed: root.layer(root.palette.m3onPrimaryFixed)
        readonly property color m3onPrimaryFixedVariant: root.layer(root.palette.m3onPrimaryFixedVariant)
        readonly property color m3secondaryFixed: root.layer(root.palette.m3secondaryFixed)
        readonly property color m3secondaryFixedDim: root.layer(root.palette.m3secondaryFixedDim)
        readonly property color m3onSecondaryFixed: root.layer(root.palette.m3onSecondaryFixed)
        readonly property color m3onSecondaryFixedVariant: root.layer(root.palette.m3onSecondaryFixedVariant)
        readonly property color m3tertiaryFixed: root.layer(root.palette.m3tertiaryFixed)
        readonly property color m3tertiaryFixedDim: root.layer(root.palette.m3tertiaryFixedDim)
        readonly property color m3onTertiaryFixed: root.layer(root.palette.m3onTertiaryFixed)
        readonly property color m3onTertiaryFixedVariant: root.layer(root.palette.m3onTertiaryFixedVariant)
    }

    component M3Palette: QtObject {
        property color m3primary_paletteKeyColor: "#a8627b"
        property color m3secondary_paletteKeyColor: "#8e6f78"
        property color m3tertiary_paletteKeyColor: "#986e4c"
        property color m3neutral_paletteKeyColor: "#807477"
        property color m3neutral_variant_paletteKeyColor: "#837377"
        property color m3background: "#191114"
        property color m3onBackground: "#efdfe2"
        property color m3surface: "#191114"
        property color m3surfaceDim: "#191114"
        property color m3surfaceBright: "#403739"
        property color m3surfaceContainerLowest: "#130c0e"
        property color m3surfaceContainerLow: "#22191c"
        property color m3surfaceContainer: "#261d20"
        property color m3surfaceContainerHigh: "#31282a"
        property color m3surfaceContainerHighest: "#3c3235"
        property color m3onSurface: "#efdfe2"
        property color m3surfaceVariant: "#514347"
        property color m3onSurfaceVariant: "#d5c2c6"
        property color m3inverseSurface: "#efdfe2"
        property color m3inverseOnSurface: "#372e30"
        property color m3outline: "#9e8c91"
        property color m3outlineVariant: "#514347"
        property color m3shadow: "#000000"
        property color m3scrim: "#000000"
        property color m3surfaceTint: "#ffb0ca"
        property color m3primary: "#ffb0ca"
        property color m3onPrimary: "#541d34"
        property color m3primaryContainer: "#6f334a"
        property color m3onPrimaryContainer: "#ffd9e3"
        property color m3inversePrimary: "#8b4a62"
        property color m3secondary: "#e2bdc7"
        property color m3onSecondary: "#422932"
        property color m3secondaryContainer: "#5a3f48"
        property color m3onSecondaryContainer: "#ffd9e3"
        property color m3tertiary: "#f0bc95"
        property color m3onTertiary: "#48290c"
        property color m3tertiaryContainer: "#b58763"
        property color m3onTertiaryContainer: "#000000"
        property color m3error: "#ffb4ab"
        property color m3onError: "#690005"
        property color m3errorContainer: "#93000a"
        property color m3onErrorContainer: "#ffdad6"
        property color m3success: "#b5ccba"
        property color m3onSuccess: "#213528"
        property color m3successContainer: "#374b3e"
        property color m3onSuccessContainer: "#d1e9d6"
        property color m3primaryFixed: "#ffd9e3"
        property color m3primaryFixedDim: "#ffb0ca"
        property color m3onPrimaryFixed: "#39071f"
        property color m3onPrimaryFixedVariant: "#6f334a"
        property color m3secondaryFixed: "#ffd9e3"
        property color m3secondaryFixedDim: "#e2bdc7"
        property color m3onSecondaryFixed: "#2b151d"
        property color m3onSecondaryFixedVariant: "#5a3f48"
        property color m3tertiaryFixed: "#ffdcc3"
        property color m3tertiaryFixedDim: "#f0bc95"
        property color m3onTertiaryFixed: "#2f1500"
        property color m3onTertiaryFixedVariant: "#623f21"
    }
}
