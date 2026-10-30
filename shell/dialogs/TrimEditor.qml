pragma ComponentBehavior: Bound

// TrimEditor — dialog "trim" for videos (Backend.preview "video_info" + "video_frames": filmstrip timeline and large
// stills of the frames nearest to the in / out points) and audio ("audio_waveform": waveform timeline).
// Two handles on the timeline (drag them, drag the selection to move it, click elsewhere to move the nearest handle),
// time ruler, start / end fields (hh:mm:ss.mmm), selection and result durations, "Keep selection" / "Remove
// selection" mode, fast stream-copy toggle for videos. Keyboard on the timeline: ← → move the active handle by one
// frame (audio 0.1 s), Shift ×10, Home / End, [ / ] pick the start / end handle, Ctrl+Z undo. On a short body (small
// screen, large wheel.scale) the controls move to a scrolling column beside the stills.
// Multi-file: filmstrip with per-file ranges or "Same for all" (one range, clamped to each file).
// Params (SPEC §5.6): start, end (seconds), mode ("keep" | "cut"), fast (videos), per_file {path: {start, end}} when
// files differ, plus the simple params of the action (Options).

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    readonly property bool video: root.isVideo(root.currentFile)
    property var infos: ({})
    property var fileRanges: []
    property bool sameForAll: false
    property var shared: ({
            start: 0,
            end: null
        })
    property string mode: {
        const m = String(root.paramDefault("mode", "keep"));
        return m === "cut" ? "cut" : "keep";
    }
    property bool fast: root.paramDefault("fast", false) === true
    property bool fastTouched: false
    property string activeHandle: "start"

    readonly property var info: root.infos[root.currentFile?.path ?? ""] ?? null
    readonly property real duration: DU.num(root.info?.duration) ?? 0
    readonly property real fps: {
        const f = DU.num(root.info?.fps);
        return (f !== null && f > 0 && f < 1000) ? f : 25;
    }
    readonly property bool ready: root.info?.status === "ready" && root.duration > 0
    readonly property bool perFile: !root.sameForAll && root.fileCount > 1
    readonly property var range: root.perFile ? (root.fileRanges[root.currentIndex] ?? priv.fullRange()) : root.shared
    readonly property real start: DU.clamp(DU.num(root.range?.start) ?? 0, 0, root.duration)
    readonly property real end: {
        const e = DU.num(root.range?.end);
        return e === null ? root.duration : DU.clamp(e, 0, root.duration);
    }
    readonly property real minLen: root.video ? Math.max(1 / root.fps, 0.04) : 0.01
    readonly property real step: root.video ? 1 / root.fps : 0.1
    readonly property real selLen: Math.max(0, root.end - root.start)
    readonly property real resultLen: root.mode === "keep" ? root.selLen : Math.max(0, root.duration - root.selLen)
    readonly property bool fullRange: root.start <= 0.0005 && root.end >= root.duration - 0.0005
    readonly property bool valid: root.ready && root.selLen >= root.minLen - 1e-6 && !(root.mode === "cut" && root.fullRange) && root.resultLen > 0
    readonly property var frames: Array.isArray(root.info?.frames) ? root.info.frames : []
    // Short body (small screen, large wheel.scale, multi-file strip): the controls move beside the stills, which they
    // would otherwise squeeze out (SPEC §7).
    readonly property bool sideControls: root.video && root.height - priv.stripSpace < Theme.s(400)

    function setRange(s: real, e: real): void {
        if (!root.ready)
            return;
        const D = root.duration;
        let a = DU.clamp(s, 0, D);
        let b = DU.clamp(e, 0, D);
        if (b - a < root.minLen) {
            if (root.activeHandle === "start")
                a = Math.max(0, b - root.minLen);
            else
                b = Math.min(D, a + root.minLen);
        }
        const next = {
            start: Math.round(a * 1000) / 1000,
            end: Math.round(b * 1000) / 1000,
            touched: true
        };
        if (root.perFile) {
            const list = root.fileRanges.slice();
            while (list.length < root.fileCount)
                list.push(priv.fullRange());
            list[root.currentIndex] = next;
            root.fileRanges = list;
        } else {
            root.shared = next;
        }
        root.dirty = true;
    }

    function setHandle(which: string, t: real): void {
        root.activeHandle = which;
        if (which === "start")
            root.setRange(Math.min(t, root.end - root.minLen), root.end);
        else
            root.setRange(root.start, Math.max(t, root.start + root.minLen));
    }

    function pushHistory(): void {
        const h = priv.history.slice(-49);
        h.push({
            ranges: DU.copy(root.fileRanges),
            shared: DU.copy(root.shared),
            sameForAll: root.sameForAll,
            mode: root.mode,
            fast: root.fast
        });
        priv.history = h;
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        const snap = h.pop();
        priv.history = h;
        root.fileRanges = snap.ranges;
        root.shared = snap.shared;
        root.sameForAll = snap.sameForAll;
        root.mode = snap.mode;
        root.fast = snap.fast;
        root.dirty = h.length > 0;
        return true;
    }

    function reset(): void {
        root.pushHistory();
        root.fileRanges = root.fileList.map(() => priv.fullRange());
        root.shared = priv.fullRange();
        root.mode = String(root.paramDefault("mode", "keep")) === "cut" ? "cut" : "keep";
        root.dirty = false;
    }

    function setMode(m: string): void {
        if (m === root.mode)
            return;
        root.pushHistory();
        root.mode = m;
        root.dirty = true;
    }

    function setSameForAll(value: bool): void {
        if (value === root.sameForAll)
            return;
        root.pushHistory();
        if (value)
            root.shared = Object.assign({}, root.range);
        else
            root.fileRanges = root.fileList.map(() => Object.assign({}, root.shared, {
                    touched: false
                }));
        root.sameForAll = value;
        root.dirty = true;
    }

    function nudge(delta: real): void {
        if (!root.ready)
            return;
        priv.keyHistory();
        if (root.activeHandle === "start")
            root.setHandle("start", root.start + delta);
        else
            root.setHandle("end", root.end + delta);
    }

    function nearestFrame(t: real): var {
        const list = root.frames;
        let best = null;
        let bestD = Infinity;
        for (const f of list) {
            const d = Math.abs(Number(f?.t ?? 0) - t);
            if (d < bestD) {
                bestD = d;
                best = f;
            }
        }
        return best;
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        root.busy = true;
        priv.ensureDurations(() => {
            root.busy = false;
            const params = priv.buildParams();
            if (params !== null)
                root.apply(Object.assign({}, extra, params));
        });
    }

    applyLabel: I18n.t("dialog.trim.apply")
    applyIcon: "content_cut"
    // A full-range "keep" is a plain copy: only allowed when another file has its own edit. A valid time still being
    // typed counts: Apply commits it first (DialogSurface), and it may be what makes the trim applicable.
    canApply: (root.valid && (!root.fullRange || (root.perFile && root.fileRanges.some(r => r?.touched === true)))) || (root.ready && (startField.pending || endField.pending))
    footerHint: {
        if (!root.ready)
            return "";
        if (root.mode === "cut" && root.fullRange)
            return I18n.t("dialog.trim.cut_all");
        if (root.mode === "keep" && root.fullRange)
            return I18n.t("dialog.trim.hint_select");
        return I18n.t("dialog.trim.result", {
            duration: DU.formatTime(root.resultLen, true)
        });
    }
    footerHintError: root.ready && root.mode === "cut" && root.fullRange
    implicitWidth: Theme.s(980)
    implicitHeight: root.video ? Theme.s(660) : Theme.s(470)

    Component.onCompleted: {
        root.fileRanges = root.fileList.map(() => priv.fullRange());
        priv.ensureInfo(root.currentFile);
        Qt.callLater(() => timelineFocus.forceActiveFocus());
    }
    Component.onDestruction: priv.life.alive = false
    // Deferred: the first evaluation of currentFile / ready can happen inside another binding (binding loop).
    onCurrentFileChanged: Qt.callLater(() => priv.ensureInfo(root.currentFile))
    onReadyChanged: Qt.callLater(() => {
        if (root.ready && !root.fastTouched && root.param("fast") === null && root.video)
            root.fast = root.duration > 600;
    })

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastKeyHistory: 0
        property string drag: ""
        property real pressT: 0
        property real pressStart: 0
        property real pressEnd: 0
        property real hoverX: -1
        readonly property real stripSpace: strip.visible ? strip.height + Theme.spacing.medium : 0
        readonly property real timelineHeight: Theme.s(76)
        // Room above the fields in the (clipping) controls area for their floating labels.
        readonly property real fieldInset: Theme.spacing.small
        // Height the stage keeps (the controls below it scroll when they do not fit in the rest).
        readonly property real stageMinHeight: root.video ? Theme.s(140) + Theme.spacing.medium + priv.timelineHeight + ruler.height : Theme.s(120) + Theme.s(8) + ruler.height

        function fullRange(): var {
            return {
                start: 0,
                end: null,
                touched: false
            };
        }

        function keyHistory(): void {
            const now = Date.now();
            if (now - priv.lastKeyHistory > 800)
                root.pushHistory();
            priv.lastKeyHistory = now;
        }

        function setInfo(path: string, patch: var): void {
            const next = Object.assign({}, root.infos);
            next[path] = Object.assign({}, next[path] ?? {}, patch);
            root.infos = next;
        }

        function ensureInfo(file: var): void {
            const path = file?.path ?? "";
            if (path === "")
                return;
            const existing = root.infos[path];
            if (existing && (existing.status === "ready" || existing.status === "loading") && existing.light !== true)
                return;
            priv.load(file, false, null);
        }

        // full: editor preview (filmstrip / waveform); light: only the duration (multi-file apply).
        function load(file: var, light: bool, done: var): void {
            const path = file.path;
            const life = priv.life;
            const isVid = root.isVideo(file);
            const finish = () => {
                if (typeof done === "function")
                    done();
            };
            if (!light)
                priv.setInfo(path, {
                    status: "loading",
                    error: "",
                    light: false
                });
            if (isVid) {
                let pending = light ? 1 : 2;
                const res = {
                    duration: null,
                    fps: null,
                    width: null,
                    height: null,
                    has_audio: null,
                    frames: [],
                    error: ""
                };
                const step = () => {
                    if (!life.alive || --pending > 0)
                        return;
                    const dur = DU.num(res.duration);
                    const ok = dur !== null && dur > 0;
                    priv.setInfo(path, {
                        status: ok ? "ready" : "error",
                        duration: ok ? dur : null,
                        fps: res.fps,
                        width: res.width,
                        height: res.height,
                        has_audio: res.has_audio,
                        frames: res.frames,
                        error: ok ? "" : (res.error !== "" ? res.error : I18n.t("dialog.trim.no_duration")),
                        light: light
                    });
                    finish();
                };
                Backend.preview("video_info", {
                    path: path
                }, (r, e) => {
                    if (!e && r) {
                        res.duration = DU.num(r.duration);
                        res.fps = DU.num(r.fps);
                        res.width = DU.num(r.width);
                        res.height = DU.num(r.height);
                        res.has_audio = r.has_audio === true;
                    } else {
                        res.error = String(e?.message ?? "");
                    }
                    step();
                });
                if (!light)
                    Backend.preview("video_frames", {
                        path: path,
                        count: 20,
                        height: 240
                    }, (r, e) => {
                        if (!e && r) {
                            res.frames = (Array.isArray(r.frames) ? r.frames : []).filter(f => typeof f?.png === "string" && f.png !== "");
                            if (res.duration === null && DU.num(r.duration) !== null)
                                res.duration = DU.num(r.duration);
                        }
                        step();
                    });
                return;
            }
            const known = DU.num(file.duration);
            if (light && known !== null && known > 0) {
                priv.setInfo(path, {
                    status: "ready",
                    duration: known,
                    light: true
                });
                finish();
                return;
            }
            Backend.preview("audio_waveform", {
                path: path,
                width: light ? 64 : 1600,
                height: light ? 16 : 240,
                color: String(Theme.palette.m3primary)
            }, (r, e) => {
                if (!life.alive)
                    return;
                const dur = (!e && r) ? DU.num(r.duration) : null;
                const ok = dur !== null && dur > 0;
                priv.setInfo(path, {
                    status: ok ? "ready" : "error",
                    duration: ok ? dur : null,
                    waveform: (ok && !light) ? String(r.png ?? "") : "",
                    error: ok ? "" : String(e?.message ?? I18n.t("dialog.trim.no_duration")),
                    light: light
                });
                finish();
            });
        }

        function ensureDurations(callback: var): void {
            const missing = root.fileList.filter(f => !(DU.num(root.infos[f.path]?.duration) > 0));
            if (missing.length === 0 || root.fileCount < 2) {
                callback();
                return;
            }
            let pending = missing.length;
            const life = priv.life;
            for (const f of missing) {
                priv.load(f, true, () => {
                    if (!life.alive)
                        return;
                    if (--pending === 0)
                        callback();
                });
            }
        }

        function buildParams(): var {
            const entries = [];
            for (let i = 0; i < root.fileCount; i++) {
                const f = root.fileList[i];
                const D = DU.num(root.infos[f.path]?.duration);
                const r = root.perFile ? (root.fileRanges[i] ?? priv.fullRange()) : root.shared;
                const p = {};
                if (D !== null && D > 0) {
                    const s = DU.clamp(DU.num(r.start) ?? 0, 0, D);
                    const eRaw = DU.num(r.end);
                    const e = eRaw === null ? D : DU.clamp(eRaw, s, D);
                    p.start = Math.round(s * 1000) / 1000;
                    p.end = Math.round(e * 1000) / 1000;
                } else {
                    p.start = Math.round((DU.num(r.start) ?? 0) * 1000) / 1000;
                    if (DU.num(r.end) !== null)
                        p.end = Math.round(Number(r.end) * 1000) / 1000;
                }
                entries.push({
                    path: f.path,
                    params: p
                });
            }
            if (entries.length === 0)
                return null;
            const out = DU.copy(entries[0].params);
            out.mode = root.mode;
            if (root.fileList.some(f => root.isVideo(f)))
                out.fast = root.fast;
            if (entries.length > 1 && entries.some(e => !DU.same(e.params, entries[0].params))) {
                const per = {};
                for (const e of entries)
                    per[e.path] = e.params;
                out.per_file = per;
            }
            return out;
        }

        function xOf(t: real): real {
            return root.duration > 0 ? t / root.duration * timeline.width : 0;
        }

        function tOf(x: real): real {
            return root.duration > 0 ? DU.clamp(x / Math.max(1, timeline.width), 0, 1) * root.duration : 0;
        }

        function tickInterval(): real {
            const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
            const maxTicks = Math.max(2, Math.floor(timeline.width / Theme.s(90)));
            for (const s of steps) {
                if (root.duration / s <= maxTicks)
                    return s;
            }
            return 7200;
        }

        function tickLabel(t: real, interval: real): string {
            if (interval < 1)
                return I18n.formatDuration(t) + I18n.formatNumber(t - Math.floor(t), 1).slice(1);
            return I18n.formatDuration(t);
        }
    }

    // ------------------------------------------------------------------ stage: stills + timeline + ruler
    Item {
        id: stage

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: root.sideControls ? (strip.visible ? strip.top : parent.bottom) : controls.top
        anchors.bottomMargin: root.sideControls ? (strip.visible ? Theme.spacing.medium : 0) : Theme.spacing.large - priv.fieldInset

        // Large stills nearest to the in / out points (video).
        Row {
            id: stills

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.rightMargin: root.sideControls ? controls.width + Theme.spacing.large : 0
            anchors.top: parent.top
            anchors.bottom: timeline.top
            anchors.bottomMargin: Theme.spacing.medium
            spacing: Theme.spacing.medium
            visible: root.video && root.ready

            Repeater {
                model: ["start", "end"]

                StyledRect {
                    id: still

                    required property string modelData
                    readonly property real time: still.modelData === "start" ? root.start : root.end
                    readonly property var frame: root.nearestFrame(still.time)
                    readonly property bool active: root.activeHandle === still.modelData

                    width: (stills.width - stills.spacing) / 2
                    height: stills.height
                    radius: Theme.rounding.large
                    color: Theme.palette.m3surfaceContainerLow
                    border.width: still.active ? Math.max(2, Math.round(2 * Theme.scale)) : 0
                    border.color: Theme.palette.m3primary

                    Image {
                        anchors.fill: parent
                        anchors.margins: Theme.padding.small
                        anchors.bottomMargin: stillLabel.height + Theme.padding.small * 2
                        source: root.previewUrl(String(still.frame?.png ?? ""))
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        smooth: true
                    }

                    Row {
                        id: stillLabel

                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: Theme.padding.small
                        spacing: Theme.spacing.small

                        MaterialIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            text: still.modelData === "start" ? "first_page" : "last_page"
                            size: Theme.font.iconSize.small
                            color: still.active ? Theme.palette.m3primary : Theme.palette.m3onSurfaceVariant
                        }

                        StyledText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: (still.modelData === "start" ? I18n.t("dialog.trim.start") : I18n.t("dialog.trim.end")) + " · " + DU.formatTime(still.time, true)
                            font.pointSize: Theme.font.size.small
                            font.features: ({
                                    "tnum": 1
                                })
                            color: still.active ? Theme.palette.m3primary : Theme.palette.m3onSurfaceVariant
                        }
                    }

                    StyledText {
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.padding.medium
                        visible: still.frame !== null && Math.abs(Number(still.frame?.t ?? 0) - still.time) > 0.5
                        text: "≈ " + DU.formatTime(Number(still.frame?.t ?? 0), false)
                        font.pointSize: Theme.font.size.small
                        color: "white"
                        style: Text.Outline
                        styleColor: Qt.rgba(0, 0, 0, 0.6)
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.activeHandle = still.modelData;
                            timelineFocus.forceActiveFocus();
                        }
                    }
                }
            }
        }

        FocusScope {
            id: timelineFocus

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: timeline.top
            anchors.bottom: timeline.bottom
            activeFocusOnTab: true
            enabled: root.ready

            Keys.onPressed: event => {
                const big = (event.modifiers & Qt.ShiftModifier) !== 0;
                const d = root.step * (big ? 10 : 1);
                let handled = true;
                switch (event.key) {
                case Qt.Key_Left:
                    root.nudge(-d);
                    break;
                case Qt.Key_Right:
                    root.nudge(d);
                    break;
                case Qt.Key_Home:
                    root.pushHistory();
                    root.setHandle(root.activeHandle, 0);
                    break;
                case Qt.Key_End:
                    root.pushHistory();
                    root.setHandle(root.activeHandle, root.duration);
                    break;
                case Qt.Key_BracketLeft:
                    root.activeHandle = "start";
                    break;
                case Qt.Key_BracketRight:
                    root.activeHandle = "end";
                    break;
                default:
                    handled = false;
                }
                event.accepted = handled;
            }
        }

        StyledRect {
            id: timeline

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: Theme.s(8)
            anchors.rightMargin: Theme.s(8)
            anchors.bottom: ruler.top
            anchors.top: root.video ? undefined : parent.top
            anchors.topMargin: root.video ? 0 : Theme.s(8)
            height: root.video ? priv.timelineHeight : Math.max(Theme.s(120), stage.height - ruler.height - Theme.s(8))
            radius: Theme.rounding.medium
            color: Theme.palette.m3surfaceContainerHighest
            clip: true

            // Filmstrip (video).
            Row {
                anchors.fill: parent
                visible: root.video

                Repeater {
                    model: root.video ? root.frames : []

                    Image {
                        required property var modelData

                        width: timeline.width / Math.max(1, root.frames.length)
                        height: timeline.height
                        source: root.previewUrl(String(modelData?.png ?? ""))
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        sourceSize.height: Math.max(1, Math.round(timeline.height * 2))
                    }
                }
            }

            // Waveform (audio).
            Image {
                anchors.fill: parent
                anchors.topMargin: Theme.s(6)
                anchors.bottomMargin: Theme.s(6)
                visible: !root.video
                source: root.video ? "" : root.previewUrl(String(root.info?.waveform ?? ""))
                fillMode: Image.Stretch
                asynchronous: true
                smooth: true
            }

            // Shading: outside the selection (keep) or the selection itself (cut).
            Rectangle {
                x: 0
                width: Math.max(0, priv.xOf(root.start))
                height: parent.height
                visible: root.mode === "keep" && root.ready
                color: Qt.rgba(0, 0, 0, 0.6)
            }
            Rectangle {
                x: priv.xOf(root.end)
                width: Math.max(0, parent.width - x)
                height: parent.height
                visible: root.mode === "keep" && root.ready
                color: Qt.rgba(0, 0, 0, 0.6)
            }
            Rectangle {
                x: priv.xOf(root.start)
                width: Math.max(0, priv.xOf(root.end) - x)
                height: parent.height
                visible: root.mode === "cut" && root.ready
                color: Qt.alpha(Theme.palette.m3error, 0.45)

                MaterialIcon {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: 1
                    visible: parent.width > Theme.s(40)
                    text: "content_cut"
                    size: Theme.font.iconSize.large
                    color: Theme.palette.m3onError
                }
            }

            // Selection frame.
            Rectangle {
                x: priv.xOf(root.start)
                width: Math.max(2, priv.xOf(root.end) - x)
                height: parent.height
                visible: root.ready
                color: "transparent"
                border.width: Math.max(2, Math.round(2 * Theme.scale))
                border.color: root.mode === "cut" ? Theme.palette.m3error : Theme.palette.m3primary
            }

            // Hover line.
            Rectangle {
                x: Math.round(priv.hoverX)
                width: Math.max(1, Math.round(Theme.scale))
                height: parent.height
                visible: priv.hoverX >= 0 && priv.drag === "" && root.ready
                color: Qt.rgba(1, 1, 1, 0.7)
            }
        }

        // Handles (outside the clipped timeline so they can overhang it).
        Repeater {
            model: ["start", "end"]

            StyledRect {
                id: handle

                required property string modelData
                readonly property bool active: root.activeHandle === handle.modelData

                x: timeline.x + priv.xOf(handle.modelData === "start" ? root.start : root.end) - width / 2
                y: timeline.y - Theme.s(5)
                width: Theme.s(14)
                height: timeline.height + Theme.s(10)
                radius: Theme.rounding.small
                visible: root.ready
                color: handle.active ? (root.mode === "cut" ? Theme.palette.m3error : Theme.palette.m3primary) : Theme.palette.m3secondaryContainer
                border.width: Math.max(1, Math.round(Theme.scale))
                border.color: handle.active ? Theme.palette.m3surface : Theme.palette.m3outline

                Column {
                    anchors.centerIn: parent
                    spacing: Theme.s(3)

                    Repeater {
                        model: 3

                        Rectangle {
                            width: Theme.s(6)
                            height: Math.max(1, Math.round(1.5 * Theme.scale))
                            radius: height / 2
                            color: handle.active ? (root.mode === "cut" ? Theme.palette.m3onError : Theme.palette.m3onPrimary) : Theme.palette.m3onSecondaryContainer
                        }
                    }
                }
            }
        }

        // Hover / drag time bubble.
        StyledRect {
            readonly property real t: priv.drag !== "" ? (root.activeHandle === "start" ? root.start : root.end) : priv.tOf(priv.hoverX)

            x: DU.clamp(timeline.x + priv.xOf(t) - width / 2, 0, stage.width - width)
            y: timeline.y - height - Theme.s(8)
            visible: root.ready && (priv.drag !== "" || priv.hoverX >= 0)
            implicitWidth: bubbleText.implicitWidth + Theme.padding.medium * 2
            implicitHeight: bubbleText.implicitHeight + Theme.padding.extraSmall * 2
            width: implicitWidth
            height: implicitHeight
            radius: height / 2
            color: Theme.palette.m3inverseSurface
            z: 2

            StyledText {
                id: bubbleText

                anchors.centerIn: parent
                text: DU.formatTime(parent.t, true)
                font.pointSize: Theme.font.size.small
                font.features: ({
                        "tnum": 1
                    })
                color: Theme.palette.m3inverseOnSurface
            }
        }

        MouseArea {
            id: timelineMouse

            x: timeline.x - Theme.s(10)
            y: timeline.y - Theme.s(6)
            width: timeline.width + Theme.s(20)
            height: timeline.height + Theme.s(12)
            enabled: root.ready
            hoverEnabled: true
            preventStealing: true
            cursorShape: {
                if (priv.drag === "move")
                    return Qt.ClosedHandCursor;
                if (priv.drag !== "")
                    return Qt.SizeHorCursor;
                const x = priv.hoverX;
                const tol = Theme.s(10);
                if (Math.abs(x - priv.xOf(root.start)) <= tol || Math.abs(x - priv.xOf(root.end)) <= tol)
                    return Qt.SizeHorCursor;
                if (x > priv.xOf(root.start) && x < priv.xOf(root.end))
                    return Qt.OpenHandCursor;
                return Qt.PointingHandCursor;
            }

            function localX(mx: real): real {
                return DU.clamp(mx - Theme.s(10), 0, timeline.width);
            }

            onPressed: mouse => {
                timelineFocus.forceActiveFocus();
                const x = timelineMouse.localX(mouse.x);
                const sx = priv.xOf(root.start);
                const ex = priv.xOf(root.end);
                const tol = Theme.s(10);
                root.pushHistory();
                priv.pressT = priv.tOf(x);
                priv.pressStart = root.start;
                priv.pressEnd = root.end;
                const ds = Math.abs(x - sx);
                const de = Math.abs(x - ex);
                if (ds <= tol || de <= tol) {
                    priv.drag = ds <= de ? "start" : "end";
                    root.activeHandle = priv.drag;
                } else if (x > sx && x < ex) {
                    priv.drag = "move";
                } else {
                    priv.drag = ds < de ? "start" : "end";
                    root.setHandle(priv.drag, priv.tOf(x));
                }
            }
            onPositionChanged: mouse => {
                const x = timelineMouse.localX(mouse.x);
                priv.hoverX = x;
                if (priv.drag === "")
                    return;
                const t = priv.tOf(x);
                if (priv.drag === "move") {
                    const len = priv.pressEnd - priv.pressStart;
                    const s = DU.clamp(priv.pressStart + (t - priv.pressT), 0, root.duration - len);
                    root.setRange(s, s + len);
                } else {
                    root.setHandle(priv.drag, t);
                }
            }
            onReleased: priv.drag = ""
            onCanceled: priv.drag = ""
            onExited: priv.hoverX = -1
        }

        // Ruler.
        Item {
            id: ruler

            readonly property real interval: priv.tickInterval()

            anchors.left: timeline.left
            anchors.right: timeline.right
            anchors.bottom: parent.bottom
            height: Theme.s(22)
            visible: root.ready

            Repeater {
                model: root.ready ? Math.floor(root.duration / ruler.interval) + 1 : 0

                Item {
                    id: tick

                    required property int index
                    readonly property real t: tick.index * ruler.interval

                    x: priv.xOf(tick.t)
                    width: 1
                    height: ruler.height

                    Rectangle {
                        width: Math.max(1, Math.round(Theme.scale))
                        height: Theme.s(5)
                        color: Theme.palette.m3outline
                    }

                    StyledText {
                        x: tick.index === 0 ? 0 : -width / 2
                        y: Theme.s(6)
                        visible: tick.x + width / 2 <= ruler.width || tick.index === 0
                        text: priv.tickLabel(tick.t, ruler.interval)
                        font.pointSize: Theme.font.size.small * 0.9
                        font.features: ({
                                "tnum": 1
                            })
                        color: Theme.palette.m3onSurfaceVariant
                    }
                }
            }
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading"
            error: root.info?.status === "error" ? (String(root.info?.error ?? "") || I18n.t("dialog.preview.failed")) : ""
            icon: root.video ? "videocam_off" : "music_off"
            onRetry: priv.load(root.currentFile, false, null)
        }
    }

    // ------------------------------------------------------------------ controls
    // Below the stage or, on a short body, in a column beside the stills; scrolls when taller than its room.
    ScrollArea {
        id: controls

        scrollbarAlwaysOn: true
        anchors.left: root.sideControls ? undefined : parent.left
        anchors.right: parent.right
        anchors.top: root.sideControls ? parent.top : undefined
        anchors.bottom: root.sideControls ? undefined : (strip.visible ? strip.top : parent.bottom)
        anchors.bottomMargin: !root.sideControls && strip.visible ? Theme.spacing.medium : 0
        width: Math.min(Theme.s(380), root.width / 2)
        height: root.sideControls ? stills.height : Math.min(controlsRow.implicitHeight + priv.fieldInset, Math.max(Theme.s(96), root.height - priv.stripSpace - priv.stageMinHeight - Theme.spacing.large))
        contentHeight: controlsRow.implicitHeight + priv.fieldInset

        Grid {
            id: controlsRow

            y: priv.fieldInset
            width: controls.width - (root.sideControls ? Theme.s(10) : 0)
            columns: root.sideControls ? 1 : 2
            columnSpacing: Theme.spacing.extraLarge
            rowSpacing: Theme.spacing.medium

            // Times
            Column {
                width: root.sideControls ? controlsRow.width : Theme.s(380)
                spacing: Theme.spacing.small

                Row {
                    width: parent.width
                    spacing: Theme.spacing.medium

                    TimeField {
                        id: startField

                        width: (parent.width - parent.spacing) / 2
                        label: I18n.t("dialog.trim.start")
                        value: root.start
                        maxValue: root.duration
                        disabled: !root.ready
                        onCommitted: v => {
                            root.pushHistory();
                            root.setHandle("start", v);
                        }
                        onFocusedChanged: {
                            if (focused)
                                root.activeHandle = "start";
                        }
                    }

                    TimeField {
                        id: endField

                        width: (parent.width - parent.spacing) / 2
                        label: I18n.t("dialog.trim.end")
                        value: root.end
                        maxValue: root.duration
                        disabled: !root.ready
                        onCommitted: v => {
                            root.pushHistory();
                            root.setHandle("end", v);
                        }
                        onFocusedChanged: {
                            if (focused)
                                root.activeHandle = "end";
                        }
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.ready
                    text: I18n.t("dialog.trim.selection", {
                        duration: DU.formatTime(root.selLen, true)
                    }) + "  ·  " + I18n.t("dialog.trim.total", {
                        duration: DU.formatTime(root.duration, true)
                    })
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    font.features: ({
                            "tnum": 1
                        })
                    color: Theme.palette.m3onSurfaceVariant
                }
            }

            // Mode + fast copy + options
            Column {
                width: root.sideControls ? controlsRow.width : Math.max(Theme.s(240), controlsRow.width - Theme.s(380) - controlsRow.columnSpacing)
                spacing: Theme.spacing.medium

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Chip {
                        type: Chip.Filter
                        activeFocusOnTab: true
                        icon: "select"
                        text: I18n.t("dialog.trim.keep")
                        selected: root.mode === "keep"
                        onClicked: root.setMode("keep")
                    }

                    Chip {
                        type: Chip.Filter
                        activeFocusOnTab: true
                        icon: "content_cut"
                        text: I18n.t("dialog.trim.cut")
                        selected: root.mode === "cut"
                        onClicked: root.setMode("cut")
                    }
                }

                Item {
                    width: parent.width
                    height: Math.max(fastText.implicitHeight, fastSwitch.implicitHeight)
                    visible: root.video

                    Column {
                        id: fastText

                        anchors.left: parent.left
                        anchors.right: fastSwitch.left
                        anchors.rightMargin: Theme.spacing.medium
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacing.extraSmall

                        StyledText {
                            width: parent.width
                            text: I18n.t("dialog.trim.fast")
                            wrapMode: Text.Wrap
                            font.pointSize: Theme.font.size.normal
                        }

                        StyledText {
                            width: parent.width
                            text: I18n.t("dialog.trim.fast_help")
                            wrapMode: Text.Wrap
                            font.pointSize: Theme.font.size.small
                            color: Theme.palette.m3onSurfaceVariant
                        }
                    }

                    M3Switch {
                        id: fastSwitch

                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.fast
                        onToggled: {
                            root.pushHistory();
                            root.fast = fastSwitch.checked;
                            root.fastTouched = true;
                            root.dirty = true;
                            // toggle() assigned `checked`: bind it again so undo / reset show up.
                            fastSwitch.checked = Qt.binding(() => root.fast);
                        }
                    }
                }

                ParamForm {
                    id: options

                    width: parent.width
                    height: implicitHeight
                    visible: options.hasFields
                    node: root.node
                    files: root.files
                    host: root.host
                    embedded: true
                    focus: false
                    exclude: root.optionExcludes(["start", "end", "mode", "fast", "per_file", "range"])
                }

                StyledText {
                    width: parent.width
                    // The arrows move a frame (video) or 0.1 s (audio).
                    text: root.video ? I18n.t("dialog.trim.keys") : I18n.t("dialog.trim.keys_audio")
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3outline
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
        showSameForAll: true
        sameForAll: root.sameForAll
        edited: root.perFile ? root.fileRanges.map(r => r?.touched === true) : []
        // The focus moves first: a field being typed in commits to the file it was typed for.
        onSelected: index => {
            timelineFocus.forceActiveFocus();
            root.currentIndex = index;
        }
        onSameForAllToggled: value => {
            timelineFocus.forceActiveFocus();
            root.setSameForAll(value);
        }
    }

    // hh:mm:ss.mmm field committing on Enter / focus loss.
    component TimeField: M3TextField {
        id: tf

        property real value: 0
        property real maxValue: 0
        readonly property bool focused: tf.editor?.activeFocus ?? false
        // The user typed something not committed yet. Otherwise the field just follows the value, also while it has
        // the focus (undo, file switch…), so a stale text can never be committed.
        property bool edited: false
        // A valid time among those (editingFinished commits it).
        readonly property bool pending: {
            if (!tf.edited)
                return false;
            const s = DU.parseTime(tf.text);
            return !isNaN(s) && Math.abs(s - tf.value) > 0.0005;
        }
        signal committed(real v)

        mono: true
        placeholderText: "00:00:00.000"
        validator: t => {
            const s = DU.parseTime(t);
            if (isNaN(s))
                return I18n.t("dialog.form.time_invalid");
            if (tf.maxValue > 0 && s > tf.maxValue + 0.0005)
                return I18n.t("dialog.trim.beyond_end", {
                    duration: DU.formatTime(tf.maxValue, true)
                });
            return "";
        }
        onTextEdited: tf.edited = true
        onValueChanged: {
            if (!tf.edited)
                tf.text = DU.formatTime(tf.value, true);
        }
        // Enter / focus loss (Apply also takes the focus first); the text then shows the time actually set.
        onEditingFinished: {
            const s = DU.parseTime(tf.text);
            const typed = tf.edited;
            tf.edited = false;
            if (typed && !isNaN(s) && Math.abs(s - tf.value) > 0.0005)
                tf.committed(Math.min(s, tf.maxValue > 0 ? tf.maxValue : s));
            tf.text = DU.formatTime(tf.value, true);
        }
        // Enter only commits (TextInput emitted editingFinished and passes the key on); Ctrl+Enter applies.
        Keys.onReturnPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
        Keys.onEnterPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
        Component.onCompleted: tf.text = DU.formatTime(tf.value, true)
    }
}
