pragma ComponentBehavior: Bound

// CropEditor — dialog "crop" for images (Backend.preview "image": original EXIF-rotated size + PNG) and videos
// (preview "video_info" for the source size + "video_frames" for stills to crop on).
// Draggable crop frame with 8 handles + move (drag outside the frame to draw a new one), rule-of-thirds grid,
// ratio chips (Free, Original, 1:1, 4:3, 3:2, 16:9, 9:16, 4:5, 2:3, 21:9) + orientation swap, linked X/Y/W/H fields
// in original pixels, keyboard: arrows nudge 1 px (Shift 10 px), Ctrl+arrows resize, R reset, X swap, Ctrl+Z undo.
// Multi-file: filmstrip with "Same for all" (default; the normalised frame is applied to every file, a fixed ratio is
// fitted again to each file's shape) or per-file frames. Params (SPEC §5.6): rect {x, y, w, h} in original pixels (video: even sizes), ratio ("free" | "original" |
// "W:H"), per_file {path: {rect, ratio}} when files differ, plus the remaining simple params of the action (Options).

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    readonly property bool video: root.isVideo(root.currentFile)
    property bool sameForAll: true
    property var shared: priv.fullState()
    property var fileStates: []
    property var infos: ({})
    readonly property var info: root.infos[root.currentFile?.path ?? ""] ?? null
    readonly property real ow: DU.num(root.info?.ow) ?? 0
    readonly property real oh: DU.num(root.info?.oh) ?? 0
    readonly property bool perFile: !root.sameForAll && root.fileCount > 1
    readonly property var cur: priv.fitState(root.perFile ? (root.fileStates[root.currentIndex] ?? priv.fullState()) : root.shared, root.ow, root.oh)
    readonly property var pxRect: priv.toPx(root.cur, root.ow, root.oh, root.video)
    readonly property var ratios: [
        {
            id: "free",
            label: I18n.t("dialog.crop.free")
        },
        {
            id: "original",
            label: I18n.t("dialog.crop.original")
        },
        {
            id: "1:1",
            label: "1:1"
        },
        {
            id: "4:3",
            label: "4:3"
        },
        {
            id: "3:2",
            label: "3:2"
        },
        {
            id: "16:9",
            label: "16:9"
        },
        {
            id: "9:16",
            label: "9:16"
        },
        {
            id: "4:5",
            label: "4:5"
        },
        {
            id: "2:3",
            label: "2:3"
        },
        {
            id: "21:9",
            label: "21:9"
        }
    ]

    function ratioPx(state: var, w: real, h: real): var {
        const id = String(state?.ratio ?? "free");
        let r = null;
        if (id === "original")
            r = (w > 0 && h > 0) ? w / h : null;
        else
            r = DU.ratioOf(id);
        if (r === null)
            return null;
        return state?.swapped === true ? 1 / r : r;
    }

    function ratioParam(state: var, w: real, h: real): string {
        const id = String(state?.ratio ?? "free");
        if (id === "free")
            return "free";
        if (id === "original") {
            if (state?.swapped !== true)
                return "original";
            const g = priv.gcd(Math.round(h), Math.round(w));
            return g > 0 ? (Math.round(h) / g) + ":" + (Math.round(w) / g) : "original";
        }
        if (state?.swapped === true) {
            const parts = id.split(":");
            return parts.length === 2 ? parts[1] + ":" + parts[0] : id;
        }
        return id;
    }

    function setCur(next: var): void {
        if (root.perFile) {
            const list = root.fileStates.slice();
            next.touched = true;
            list[root.currentIndex] = next;
            root.fileStates = list;
        } else {
            root.shared = next;
        }
        root.dirty = true;
    }

    function setPx(px: var): void {
        if (root.ow <= 0 || root.oh <= 0)
            return;
        const next = Object.assign({}, root.cur, {
            x: px.x / root.ow,
            y: px.y / root.oh,
            w: px.w / root.ow,
            h: px.h / root.oh
        });
        root.setCur(next);
    }

    function pushHistory(): void {
        const snap = {
            sameForAll: root.sameForAll,
            shared: DU.copy(root.shared),
            states: DU.copy(root.fileStates)
        };
        const h = priv.history.slice(-49);
        h.push(snap);
        priv.history = h;
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        const snap = h.pop();
        priv.history = h;
        root.sameForAll = snap.sameForAll;
        root.shared = snap.shared;
        root.fileStates = snap.states;
        root.dirty = h.length > 0;
        return true;
    }

    function selectRatio(id: string): void {
        root.pushHistory();
        const next = Object.assign({}, root.cur, {
            ratio: id,
            swapped: false
        });
        priv.fitToRatio(next);
        root.setCur(next);
    }

    function swapOrientation(): void {
        if (root.ow <= 0 || root.oh <= 0)
            return;
        root.pushHistory();
        const next = Object.assign({}, root.cur);
        if (String(next.ratio) === "free") {
            // Transpose the frame around its centre (in pixels), clamped to the image.
            const px = root.pxRect;
            const cx = px.x + px.w / 2;
            const cy = px.y + px.h / 2;
            const w = Math.min(px.h, root.ow);
            const h = Math.min(px.w, root.oh);
            const x = DU.clamp(cx - w / 2, 0, root.ow - w);
            const y = DU.clamp(cy - h / 2, 0, root.oh - h);
            Object.assign(next, {
                x: x / root.ow,
                y: y / root.oh,
                w: w / root.ow,
                h: h / root.oh
            });
        } else {
            next.swapped = !(next.swapped === true);
            priv.fitToRatio(next);
        }
        root.setCur(next);
    }

    function nudge(dx: real, dy: real): void {
        if (root.ow <= 0 || root.oh <= 0)
            return;
        const px = Object.assign({}, root.pxRect);
        px.x = DU.clamp(px.x + dx, 0, root.ow - px.w);
        px.y = DU.clamp(px.y + dy, 0, root.oh - px.h);
        priv.keyHistory();
        root.setPx(px);
    }

    function resizeBy(dw: real, dh: real): void {
        if (root.ow <= 0 || root.oh <= 0)
            return;
        const start = root.pxRect;
        const handle = dw !== 0 ? "r" : "b";
        const px = priv.dragRect(start, handle, dw, dh, root.ratioPx(root.cur, root.ow, root.oh), root.ow, root.oh, 1);
        priv.keyHistory();
        root.setPx(px);
    }

    function commitField(name: string, value: int): void {
        if (root.ow <= 0 || root.oh <= 0)
            return;
        const r = root.ratioPx(root.cur, root.ow, root.oh);
        let {
            x,
            y,
            w,
            h
        } = root.pxRect;
        const W = root.ow;
        const H = root.oh;
        if (name === "x") {
            x = DU.clamp(value, 0, W - w);
        } else if (name === "y") {
            y = DU.clamp(value, 0, H - h);
        } else if (name === "w") {
            w = DU.clamp(value, 1, W);
            if (r !== null) {
                h = w / r;
                if (h > H) {
                    h = H;
                    w = h * r;
                }
            }
        } else if (name === "h") {
            h = DU.clamp(value, 1, H);
            if (r !== null) {
                w = h * r;
                if (w > W) {
                    w = W;
                    h = w / r;
                }
            }
        }
        x = DU.clamp(x, 0, W - w);
        y = DU.clamp(y, 0, H - h);
        root.pushHistory();
        root.setPx({
            x: x,
            y: y,
            w: w,
            h: h
        });
    }

    function reset(): void {
        root.pushHistory();
        root.sameForAll = true;
        root.shared = priv.fullState();
        root.fileStates = root.fileList.map(() => priv.fullState());
        root.dirty = false;
    }

    function setSameForAll(value: bool): void {
        if (value === root.sameForAll)
            return;
        root.pushHistory();
        if (value) {
            // The frame on screen becomes the one for every file.
            root.shared = Object.assign({}, root.cur, {
                touched: false
            });
        } else {
            // Every file starts from the shared frame and can then be edited on its own.
            root.fileStates = root.fileList.map(() => Object.assign(DU.copy(root.shared), {
                    touched: false
                }));
        }
        root.sameForAll = value;
        root.dirty = true;
    }

    function selectFrame(index: int): void {
        const path = root.currentFile?.path ?? "";
        const inf = root.infos[path];
        if (!inf || !Array.isArray(inf.frames) || index < 0 || index >= inf.frames.length)
            return;
        priv.setInfo(path, Object.assign({}, inf, {
            frame: index,
            png: String(inf.frames[index]?.png ?? "")
        }));
    }

    function requestApply(): void {
        if (root.busy || !root.canApply)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        root.busy = true;
        priv.ensureDims(() => {
            root.busy = false;
            const params = priv.buildParams();
            if (params === null)
                return;
            root.apply(Object.assign({}, extra, params));
        });
    }

    applyLabel: root.video ? I18n.t("dialog.crop.apply_video") : I18n.t("dialog.crop.apply")
    applyIcon: "crop"
    canApply: root.ow > 0 && root.oh > 0
    footerHint: root.ow > 0 ? I18n.t("dialog.crop.size", {
        w: I18n.formatCount(root.pxRect.w),
        h: I18n.formatCount(root.pxRect.h)
    }) : ""

    Component.onCompleted: {
        root.fileStates = root.fileList.map(() => priv.fullState());
        priv.ensureInfo(root.currentFile);
        Qt.callLater(() => priv.focusCanvas());
    }
    Component.onDestruction: priv.life.alive = false
    // Deferred: the first evaluation of currentFile can happen inside another binding (binding loop).
    onCurrentFileChanged: Qt.callLater(() => priv.ensureInfo(root.currentFile))

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastKeyHistory: 0
        property string dragMode: ""
        property var dragStart: null
        property var pressPx: null
        readonly property bool dragging: priv.dragMode !== ""

        // Keyboard focus on the canvas, without the ring that marks Tab navigation.
        function focusCanvas(): void {
            canvasFocus.forceActiveFocus();
            canvasFocus.viaKeyboard = false;
        }

        function fullState(): var {
            return {
                x: 0,
                y: 0,
                w: 1,
                h: 1,
                ratio: "free",
                swapped: false,
                touched: false
            };
        }

        function gcd(a: int, b: int): int {
            a = Math.abs(a);
            b = Math.abs(b);
            while (b > 0) {
                const t = b;
                b = a % b;
                a = t;
            }
            return a;
        }

        function keyHistory(): void {
            // Group rapid key nudges into one undo step.
            const now = Date.now();
            if (now - priv.lastKeyHistory > 800)
                root.pushHistory();
            priv.lastKeyHistory = now;
        }

        function toPx(s: var, W: real, H: real, even: bool): var {
            if (!(W > 0 && H > 0) || !s)
                return {
                    x: 0,
                    y: 0,
                    w: 0,
                    h: 0
                };
            let w = Math.max(1, Math.round(s.w * W));
            let h = Math.max(1, Math.round(s.h * H));
            let x = Math.round(s.x * W);
            let y = Math.round(s.y * H);
            if (even) {
                w = Math.max(2, w - (w % 2));
                h = Math.max(2, h - (h % 2));
                x = x - (x % 2);
                y = y - (y % 2);
            }
            w = Math.min(w, W);
            h = Math.min(h, H);
            x = DU.clamp(x, 0, W - w);
            y = DU.clamp(y, 0, H - h);
            return {
                x: x,
                y: y,
                w: w,
                h: h
            };
        }

        // The state as a frame of a W × H file. Free frames are normalised and apply as they are; a fixed ratio is fitted
        // again for this file (same centre, same size relative to the largest frame of that ratio), so the shared
        // frame — or a per-file copy of it — keeps its ratio on files of another shape. For the file the frame was
        // drawn on this is the identity: a ratio frame there is k × the largest one, with k = max(w, h) normalised.
        function fitState(s: var, W: real, H: real): var {
            if (!s || !(W > 0 && H > 0))
                return s;
            const r = root.ratioPx(s, W, H);
            if (r === null)
                return s;
            const k = DU.clamp(Math.max(Number(s.w), Number(s.h)), 0, 1);
            let mw = W;
            let mh = W / r;
            if (mh > H) {
                mh = H;
                mw = H * r;
            }
            const w = mw * k;
            const h = mh * k;
            const x = DU.clamp((Number(s.x) + Number(s.w) / 2) * W - w / 2, 0, W - w);
            const y = DU.clamp((Number(s.y) + Number(s.h) / 2) * H - h / 2, 0, H - h);
            return Object.assign({}, s, {
                x: x / W,
                y: y / H,
                w: w / W,
                h: h / H
            });
        }

        // Largest frame of the state's ratio inside the image, centred on the current frame.
        function fitToRatio(s: var): void {
            const W = root.ow;
            const H = root.oh;
            const r = root.ratioPx(s, W, H);
            if (r === null || !(W > 0 && H > 0))
                return;
            let w = W;
            let h = w / r;
            if (h > H) {
                h = H;
                w = h * r;
            }
            const cx = (s.x + s.w / 2) * W;
            const cy = (s.y + s.h / 2) * H;
            const x = DU.clamp(cx - w / 2, 0, W - w);
            const y = DU.clamp(cy - h / 2, 0, H - h);
            s.x = x / W;
            s.y = y / H;
            s.w = w / W;
            s.h = h / H;
        }

        // Resize / move a pixel rect by (dx, dy) with handle h ("tl" … "l", "move"), optional ratio r (w/h).
        function dragRect(s: var, h: string, dx: real, dy: real, r: var, W: real, H: real, minS: real): var {
            if (h === "move")
                return {
                    x: DU.clamp(s.x + dx, 0, W - s.w),
                    y: DU.clamp(s.y + dy, 0, H - s.h),
                    w: s.w,
                    h: s.h
                };
            const hasL = h.indexOf("l") !== -1;
            const hasR = h.indexOf("r") !== -1;
            const hasT = h.indexOf("t") !== -1;
            const hasB = h.indexOf("b") !== -1;
            const m = Math.min(minS, W, H);
            if (r === null || r === undefined) {
                let left = s.x;
                let top = s.y;
                let right = s.x + s.w;
                let bottom = s.y + s.h;
                if (hasL)
                    left = DU.clamp(left + dx, 0, right - m);
                if (hasR)
                    right = DU.clamp(right + dx, left + m, W);
                if (hasT)
                    top = DU.clamp(top + dy, 0, bottom - m);
                if (hasB)
                    bottom = DU.clamp(bottom + dy, top + m, H);
                return {
                    x: left,
                    y: top,
                    w: right - left,
                    h: bottom - top
                };
            }
            const corner = (hasL || hasR) && (hasT || hasB);
            if (corner) {
                const ax = hasL ? s.x + s.w : s.x;
                const ay = hasT ? s.y + s.h : s.y;
                const px = (hasL ? s.x : s.x + s.w) + dx;
                const py = (hasT ? s.y : s.y + s.h) + dy;
                let w = Math.max(hasL ? ax - px : px - ax, m);
                let hh = Math.max(hasT ? ay - py : py - ay, m);
                w = Math.max(w, hh * r);
                hh = w / r;
                const maxW = hasL ? ax : W - ax;
                const maxH = hasT ? ay : H - ay;
                const k = Math.min(1, maxW / w, maxH / hh);
                w *= k;
                hh *= k;
                return {
                    x: hasL ? ax - w : ax,
                    y: hasT ? ay - hh : ay,
                    w: w,
                    h: hh
                };
            }
            if (hasL || hasR) {
                const ax = hasL ? s.x + s.w : s.x;
                const maxW = Math.min(hasL ? ax : W - ax, H * r);
                const w = DU.clamp((hasL ? s.w - dx : s.w + dx), Math.min(m, maxW), maxW);
                const hh = w / r;
                const cy = s.y + s.h / 2;
                return {
                    x: hasL ? ax - w : ax,
                    y: DU.clamp(cy - hh / 2, 0, H - hh),
                    w: w,
                    h: hh
                };
            }
            const ay = hasT ? s.y + s.h : s.y;
            const maxH = Math.min(hasT ? ay : H - ay, W / r);
            const hh = DU.clamp((hasT ? s.h - dy : s.h + dy), Math.min(m, maxH), maxH);
            const w = hh * r;
            const cx = s.x + s.w / 2;
            return {
                x: DU.clamp(cx - w / 2, 0, W - w),
                y: hasT ? ay - hh : ay,
                w: w,
                h: hh
            };
        }

        // New frame drawn from an anchor point to the pointer (any direction).
        function drawRect(ax: real, ay: real, px: real, py: real, r: var, W: real, H: real, minS: real): var {
            const left = px < ax;
            const up = py < ay;
            const tx = DU.clamp(px, 0, W);
            const ty = DU.clamp(py, 0, H);
            let w = Math.max(Math.abs(tx - ax), Math.min(minS, W));
            let h = Math.max(Math.abs(ty - ay), Math.min(minS, H));
            if (r !== null && r !== undefined) {
                w = Math.max(w, h * r);
                h = w / r;
                const maxW = left ? ax : W - ax;
                const maxH = up ? ay : H - ay;
                const k = Math.min(1, maxW / w, maxH / h);
                w *= k;
                h *= k;
            } else {
                w = Math.min(w, left ? ax : W - ax);
                h = Math.min(h, up ? ay : H - ay);
            }
            return {
                x: left ? ax - w : ax,
                y: up ? ay - h : ay,
                w: Math.max(1, w),
                h: Math.max(1, h)
            };
        }

        function hitTest(mx: real, my: real): string {
            if (!(root.ow > 0 && root.oh > 0))
                return "";
            const s = root.cur;
            const L = canvas.imageX + s.x * canvas.imageWidth;
            const T = canvas.imageY + s.y * canvas.imageHeight;
            const R = L + s.w * canvas.imageWidth;
            const B = T + s.h * canvas.imageHeight;
            const tol = Theme.s(14);
            const nearL = Math.abs(mx - L) <= tol;
            const nearR = Math.abs(mx - R) <= tol;
            const nearT = Math.abs(my - T) <= tol;
            const nearB = Math.abs(my - B) <= tol;
            const inX = mx > L - tol && mx < R + tol;
            const inY = my > T - tol && my < B + tol;
            if (nearT && nearL)
                return "tl";
            if (nearT && nearR)
                return "tr";
            if (nearB && nearL)
                return "bl";
            if (nearB && nearR)
                return "br";
            if (nearT && inX)
                return "t";
            if (nearB && inX)
                return "b";
            if (nearL && inY)
                return "l";
            if (nearR && inY)
                return "r";
            if (mx > L && mx < R && my > T && my < B)
                return "move";
            return "new";
        }

        function cursorFor(h: string): int {
            switch (h) {
            case "tl":
            case "br":
                return Qt.SizeFDiagCursor;
            case "tr":
            case "bl":
                return Qt.SizeBDiagCursor;
            case "l":
            case "r":
                return Qt.SizeHorCursor;
            case "t":
            case "b":
                return Qt.SizeVerCursor;
            case "move":
                return priv.dragging ? Qt.ClosedHandCursor : Qt.OpenHandCursor;
            case "new":
                return Qt.CrossCursor;
            default:
                return Qt.ArrowCursor;
            }
        }

        function toImagePx(mx: real, my: real): var {
            return {
                x: (mx - canvas.imageX) / Math.max(1, canvas.imageWidth) * root.ow,
                y: (my - canvas.imageY) / Math.max(1, canvas.imageHeight) * root.oh
            };
        }

        function setInfo(path: string, obj: var): void {
            const next = Object.assign({}, root.infos);
            next[path] = obj;
            root.infos = next;
        }

        function ensureInfo(file: var): void {
            const path = file?.path ?? "";
            if (path === "")
                return;
            const existing = root.infos[path];
            if (existing && (existing.status === "ready" || existing.status === "loading"))
                return;
            priv.load(file, false);
        }

        // Loads the preview (full = editor preview; light = only the original size, for multi-file apply).
        function load(file: var, light: bool, done: var): void {
            const path = file.path;
            const life = priv.life;
            const isVid = root.isVideo(file);
            if (!light)
                priv.setInfo(path, {
                    status: "loading",
                    png: "",
                    ow: null,
                    oh: null,
                    frames: [],
                    frame: 0,
                    error: ""
                });
            const finish = res => {
                if (!life.alive)
                    return;
                const prev = root.infos[path];
                if (light && prev && prev.status === "ready") {
                    if (typeof done === "function")
                        done();
                    return;
                }
                priv.setInfo(path, res);
                if (typeof done === "function")
                    done();
            };
            if (isVid) {
                const res = {
                    status: "loading",
                    png: "",
                    ow: null,
                    oh: null,
                    frames: [],
                    frame: 0,
                    error: "",
                    light: light
                };
                let pending = light ? 1 : 2;
                const step = () => {
                    if (--pending > 0)
                        return;
                    res.status = (res.ow > 0 && res.oh > 0) ? "ready" : "error";
                    if (res.status === "error" && res.error === "")
                        res.error = I18n.t("dialog.preview.failed");
                    if (res.frames.length > 0) {
                        res.frame = Math.floor(res.frames.length / 2);
                        res.png = String(res.frames[res.frame].png);
                    }
                    finish(res);
                };
                Backend.preview("video_info", {
                    path: path
                }, (r, e) => {
                    if (!e && r) {
                        res.ow = DU.num(r.width);
                        res.oh = DU.num(r.height);
                    } else {
                        res.error = e?.message ?? "";
                    }
                    step();
                });
                if (!light)
                    Backend.preview("video_frames", {
                        path: path,
                        count: 6,
                        height: 540
                    }, (r, e) => {
                        res.frames = (!e && Array.isArray(r?.frames)) ? r.frames.filter(f => typeof f?.png === "string" && f.png !== "") : [];
                        step();
                    });
                return;
            }
            Backend.preview("image", {
                path: path,
                max: light ? 64 : 1600
            }, (r, e) => {
                const ok = !e && r && DU.num(r.width) > 0 && DU.num(r.height) > 0;
                finish({
                    status: ok ? "ready" : "error",
                    png: (ok && !light) ? String(r.png ?? "") : "",
                    ow: ok ? DU.num(r.width) : null,
                    oh: ok ? DU.num(r.height) : null,
                    frames: [],
                    frame: 0,
                    error: ok ? "" : (e?.message ?? I18n.t("dialog.preview.failed")),
                    light: light
                });
            });
        }

        // Makes sure the original size of every file is known (multi-file apply).
        function ensureDims(callback: var): void {
            const missing = root.fileList.filter(f => {
                const inf = root.infos[f.path];
                return !(inf && DU.num(inf.ow) > 0 && DU.num(inf.oh) > 0);
            });
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
            const files = root.fileList;
            if (files.length === 0)
                return null;
            const entries = [];
            for (let i = 0; i < files.length; i++) {
                const f = files[i];
                const inf = root.infos[f.path];
                const W = DU.num(inf?.ow) ?? 0;
                const H = DU.num(inf?.oh) ?? 0;
                if (!(W > 0 && H > 0))
                    continue;
                const state = priv.fitState(root.perFile ? (root.fileStates[i] ?? priv.fullState()) : root.shared, W, H);
                const px = priv.toPx(state, W, H, root.isVideo(f));
                entries.push({
                    path: f.path,
                    params: {
                        rect: px,
                        ratio: root.ratioParam(state, W, H)
                    }
                });
            }
            if (entries.length === 0)
                return null;
            const out = DU.copy(entries[0].params);
            if (entries.length > 1 && entries.some(e => !DU.same(e.params, entries[0].params))) {
                const per = {};
                for (const e of entries)
                    per[e.path] = e.params;
                out.per_file = per;
            }
            return out;
        }
    }

    // ------------------------------------------------------------------ layout
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
            source: String(root.info?.png ?? "")
            sourceWidth: root.ow
            sourceHeight: root.oh

            Item {
                id: frameLayer

                readonly property real rx: root.cur.x * width
                readonly property real ry: root.cur.y * height
                readonly property real rw: root.cur.w * width
                readonly property real rh: root.cur.h * height
                readonly property color shade: Qt.rgba(0, 0, 0, 0.55)

                anchors.fill: parent
                visible: root.ow > 0 && root.oh > 0

                Rectangle {
                    x: 0
                    y: 0
                    width: parent.width
                    height: frameLayer.ry
                    color: frameLayer.shade
                }
                Rectangle {
                    x: 0
                    y: frameLayer.ry + frameLayer.rh
                    width: parent.width
                    height: Math.max(0, parent.height - y)
                    color: frameLayer.shade
                }
                Rectangle {
                    x: 0
                    y: frameLayer.ry
                    width: frameLayer.rx
                    height: frameLayer.rh
                    color: frameLayer.shade
                }
                Rectangle {
                    x: frameLayer.rx + frameLayer.rw
                    y: frameLayer.ry
                    width: Math.max(0, parent.width - x)
                    height: frameLayer.rh
                    color: frameLayer.shade
                }

                Item {
                    id: frameRect

                    x: frameLayer.rx
                    y: frameLayer.ry
                    width: frameLayer.rw
                    height: frameLayer.rh

                    // Rule of thirds.
                    Repeater {
                        model: 2

                        Rectangle {
                            required property int index

                            x: Math.round(frameRect.width * (index + 1) / 3)
                            width: Math.max(1, Math.round(Theme.scale))
                            height: frameRect.height
                            color: Qt.rgba(1, 1, 1, priv.dragging ? 0.7 : 0.35)
                        }
                    }
                    Repeater {
                        model: 2

                        Rectangle {
                            required property int index

                            y: Math.round(frameRect.height * (index + 1) / 3)
                            height: Math.max(1, Math.round(Theme.scale))
                            width: frameRect.width
                            color: Qt.rgba(1, 1, 1, priv.dragging ? 0.7 : 0.35)
                        }
                    }

                    Rectangle {
                        anchors.fill: parent
                        color: "transparent"
                        border.width: Math.max(1, Math.round(2 * Theme.scale))
                        border.color: Theme.palette.m3primary
                    }

                    // Corner handles.
                    Repeater {
                        model: [[0, 0], [1, 0], [0, 1], [1, 1]]

                        Rectangle {
                            required property var modelData

                            readonly property real size: Theme.s(14)

                            x: modelData[0] * frameRect.width - size / 2
                            y: modelData[1] * frameRect.height - size / 2
                            width: size
                            height: size
                            radius: Theme.rounding.extraSmall
                            color: Theme.palette.m3primary
                            border.width: Math.max(1, Math.round(1.5 * Theme.scale))
                            border.color: Theme.palette.m3onPrimary
                        }
                    }

                    // Edge handles.
                    Repeater {
                        model: [[0.5, 0, 1], [0.5, 1, 1], [0, 0.5, 0], [1, 0.5, 0]]

                        Rectangle {
                            required property var modelData

                            readonly property bool horizontal: modelData[2] === 1

                            width: horizontal ? Theme.s(24) : Theme.s(6)
                            height: horizontal ? Theme.s(6) : Theme.s(24)
                            x: modelData[0] * frameRect.width - width / 2
                            y: modelData[1] * frameRect.height - height / 2
                            radius: Math.min(width, height) / 2
                            visible: frameRect.width > Theme.s(48) && frameRect.height > Theme.s(48)
                            color: Theme.palette.m3primary
                            border.width: Math.max(1, Math.round(Theme.scale))
                            border.color: Theme.palette.m3onPrimary
                        }
                    }

                    // Size bubble while dragging.
                    StyledRect {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: parent.height > Theme.s(60) ? Theme.spacing.small : -height - Theme.spacing.small
                        visible: priv.dragging
                        implicitWidth: sizeText.implicitWidth + Theme.padding.medium * 2
                        implicitHeight: sizeText.implicitHeight + Theme.padding.extraSmall * 2
                        width: implicitWidth
                        height: implicitHeight
                        radius: height / 2
                        color: Theme.palette.m3inverseSurface

                        StyledText {
                            id: sizeText

                            anchors.centerIn: parent
                            text: root.pxRect.w + " × " + root.pxRect.h
                            color: Theme.palette.m3inverseOnSurface
                            font.pointSize: Theme.font.size.small
                            font.weight: Font.Medium
                        }
                    }
                }
            }
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading"
            error: root.info?.status === "error" ? String(root.info?.error ?? "") || I18n.t("dialog.preview.failed") : ""
            onRetry: priv.load(root.currentFile, false)
        }

        FocusScope {
            id: canvasFocus

            // Focus ring only for keyboard focus (Tab), not after clicks or the initial programmatic focus.
            property bool viaKeyboard: false

            anchors.fill: parent
            activeFocusOnTab: true
            onActiveFocusChanged: canvasFocus.viaKeyboard = canvasFocus.activeFocus && !pointer.pressed

            Keys.onPressed: event => {
                const big = (event.modifiers & Qt.ShiftModifier) !== 0;
                const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
                const step = big ? 10 : 1;
                let handled = true;
                switch (event.key) {
                case Qt.Key_Left:
                    ctrl ? root.resizeBy(-step, 0) : root.nudge(-step, 0);
                    break;
                case Qt.Key_Right:
                    ctrl ? root.resizeBy(step, 0) : root.nudge(step, 0);
                    break;
                case Qt.Key_Up:
                    ctrl ? root.resizeBy(0, -step) : root.nudge(0, -step);
                    break;
                case Qt.Key_Down:
                    ctrl ? root.resizeBy(0, step) : root.nudge(0, step);
                    break;
                case Qt.Key_R:
                    if (ctrl || (event.modifiers & Qt.AltModifier))
                        handled = false;
                    else
                        root.reset();
                    break;
                case Qt.Key_X:
                    if (ctrl || (event.modifiers & Qt.AltModifier))
                        handled = false;
                    else
                        root.swapOrientation();
                    break;
                default:
                    handled = false;
                }
                event.accepted = handled;
            }

            Rectangle {
                anchors.fill: parent
                anchors.margins: Math.round(2 * Theme.scale)
                radius: Theme.rounding.large
                color: "transparent"
                border.width: Math.max(1, Math.round(2 * Theme.scale))
                border.color: Theme.palette.m3secondary
                visible: canvasFocus.activeFocus && canvasFocus.viaKeyboard
            }

            MouseArea {
                id: pointer

                property string hover: ""

                anchors.fill: parent
                hoverEnabled: true
                enabled: root.ow > 0 && root.oh > 0
                cursorShape: priv.cursorFor(priv.dragging ? priv.dragMode : pointer.hover)
                preventStealing: true

                onPositionChanged: mouse => {
                    if (!priv.dragging) {
                        pointer.hover = priv.hitTest(mouse.x, mouse.y);
                        return;
                    }
                    const p = priv.toImagePx(mouse.x, mouse.y);
                    const r = root.ratioPx(root.cur, root.ow, root.oh);
                    const minS = Math.max(2, Theme.s(16) * root.ow / Math.max(1, canvas.imageWidth));
                    let px;
                    if (priv.dragMode === "new")
                        px = priv.drawRect(priv.pressPx.x, priv.pressPx.y, p.x, p.y, r, root.ow, root.oh, minS);
                    else
                        px = priv.dragRect(priv.dragStart, priv.dragMode, p.x - priv.pressPx.x, p.y - priv.pressPx.y, r, root.ow, root.oh, minS);
                    root.setPx(px);
                }
                onPressed: mouse => {
                    canvasFocus.forceActiveFocus();
                    const mode = priv.hitTest(mouse.x, mouse.y);
                    if (mode === "")
                        return;
                    root.pushHistory();
                    const p = priv.toImagePx(mouse.x, mouse.y);
                    priv.pressPx = {
                        x: DU.clamp(p.x, 0, root.ow),
                        y: DU.clamp(p.y, 0, root.oh)
                    };
                    priv.dragStart = Object.assign({}, root.pxRect);
                    priv.dragMode = mode;
                }
                onReleased: {
                    priv.dragMode = "";
                    priv.dragStart = null;
                }
                onCanceled: {
                    priv.dragMode = "";
                    priv.dragStart = null;
                }
                onDoubleClicked: mouse => {
                    // Double-click inside the frame: fill the image (keeping the ratio).
                    if (priv.hitTest(mouse.x, mouse.y) !== "move")
                        return;
                    root.pushHistory();
                    const next = Object.assign({}, root.cur, {
                        x: 0,
                        y: 0,
                        w: 1,
                        h: 1
                    });
                    priv.fitToRatio(next);
                    root.setCur(next);
                }
            }
        }
    }

    // ------------------------------------------------------------------ side panel
    ScrollArea {
        id: side

        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.medium : 0
        width: Theme.s(300)

        Column {
            width: side.width - Theme.s(10)
            spacing: Theme.spacing.large

            Column {
                width: parent.width
                spacing: Theme.spacing.small

                Item {
                    width: parent.width
                    height: Math.max(ratioTitle.implicitHeight, swapButton.height)

                    StyledText {
                        id: ratioTitle

                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.t("dialog.crop.ratio")
                        font.pointSize: Theme.font.size.normal
                        font.weight: Font.Medium
                    }

                    IconButton {
                        id: swapButton

                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        type: ButtonBase.Tonal
                        icon: "screen_rotation"
                        iconSize: Theme.font.iconSize.small
                        disabled: !(root.ow > 0)
                        tooltip: I18n.t("dialog.crop.swap")
                        onClicked: root.swapOrientation()
                    }
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: root.ratios

                        Chip {
                            id: ratioChip

                            required property var modelData

                            type: Chip.Filter
                            activeFocusOnTab: true
                            text: {
                                const id = String(ratioChip.modelData.id);
                                if (root.cur.ratio === id && root.cur.swapped === true && id.indexOf(":") !== -1) {
                                    const parts = id.split(":");
                                    return parts[1] + ":" + parts[0];
                                }
                                return String(ratioChip.modelData.label);
                            }
                            selected: root.cur.ratio === ratioChip.modelData.id
                            disabled: !(root.ow > 0)
                            onClicked: root.selectRatio(String(ratioChip.modelData.id))
                        }
                    }
                }
            }

            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.crop.dimensions")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Grid {
                    width: parent.width
                    columns: 2
                    columnSpacing: Theme.spacing.medium
                    rowSpacing: Theme.spacing.medium

                    PxField {
                        width: (parent.width - parent.columnSpacing) / 2
                        label: "X"
                        value: root.pxRect.x
                        disabled: !(root.ow > 0)
                        onCommitted: v => root.commitField("x", v)
                    }
                    PxField {
                        width: (parent.width - parent.columnSpacing) / 2
                        label: "Y"
                        value: root.pxRect.y
                        disabled: !(root.ow > 0)
                        onCommitted: v => root.commitField("y", v)
                    }
                    PxField {
                        width: (parent.width - parent.columnSpacing) / 2
                        label: I18n.t("dialog.crop.width")
                        value: root.pxRect.w
                        disabled: !(root.ow > 0)
                        onCommitted: v => root.commitField("w", v)
                    }
                    PxField {
                        width: (parent.width - parent.columnSpacing) / 2
                        label: I18n.t("dialog.crop.height")
                        value: root.pxRect.h
                        disabled: !(root.ow > 0)
                        onCommitted: v => root.commitField("h", v)
                    }
                }

                Row {
                    spacing: Theme.spacing.small
                    visible: root.ratioPx(root.cur, root.ow, root.oh) !== null

                    MaterialIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "link"
                        size: Theme.font.iconSize.small
                        color: Theme.palette.m3onSurfaceVariant
                    }

                    StyledText {
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.t("dialog.crop.linked")
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3onSurfaceVariant
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.ow > 0
                    text: I18n.t("dialog.crop.original_size", {
                        w: I18n.formatCount(root.ow),
                        h: I18n.formatCount(root.oh)
                    })
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }
            }

            // Video: still used as the crop background.
            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: root.video && (root.info?.frames?.length ?? 0) > 1

                StyledText {
                    text: I18n.t("dialog.crop.frame")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: root.video ? (root.info?.frames ?? []) : []

                        Item {
                            id: frameThumb

                            required property var modelData
                            required property int index
                            readonly property bool current: frameThumb.index === (root.info?.frame ?? -1)

                            width: Theme.s(84)
                            height: Theme.s(52)

                            Image {
                                anchors.fill: parent
                                anchors.margins: Math.round(2 * Theme.scale)
                                source: root.previewUrl(String(frameThumb.modelData?.png ?? ""))
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                sourceSize.height: Theme.s(104)
                            }

                            Rectangle {
                                anchors.fill: parent
                                radius: Theme.rounding.small
                                color: "transparent"
                                border.width: frameThumb.current ? Math.max(2, Math.round(2 * Theme.scale)) : Math.max(1, Math.round(Theme.scale))
                                border.color: frameThumb.current ? Theme.palette.m3primary : Theme.palette.m3outlineVariant
                            }

                            StyledText {
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: Theme.spacing.extraSmall
                                text: I18n.formatDuration(frameThumb.modelData?.t ?? 0)
                                font.pointSize: Theme.font.size.small * 0.85
                                color: "white"
                                style: Text.Outline
                                styleColor: Qt.rgba(0, 0, 0, 0.6)
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.selectFrame(frameThumb.index)
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
                    exclude: root.optionExcludes(["rect", "ratio", "per_file"])
                }
            }

            StyledText {
                width: parent.width
                text: I18n.t("dialog.crop.keys")
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
        showSameForAll: true
        sameForAll: root.sameForAll
        edited: root.perFile ? root.fileStates.map(s => s?.touched === true) : []
        // The focus moves first: a field being typed in commits to the file it was typed for.
        onSelected: index => {
            priv.focusCanvas();
            root.currentIndex = index;
        }
        onSameForAllToggled: value => {
            priv.focusCanvas();
            root.setSameForAll(value);
        }
    }

    component PxField: M3TextField {
        id: pf

        property int value: 0
        // The user typed something not committed yet. Otherwise the field just follows the value, also while it has
        // the focus (ratio chips, undo…), so a stale text can never be committed.
        property bool edited: false
        signal committed(int v)

        mono: true
        suffixText: "px"
        inputMethodHints: Qt.ImhDigitsOnly
        validator: /^\d{1,7}$/
        onTextEdited: pf.edited = true
        onValueChanged: {
            if (!pf.edited)
                pf.text = String(pf.value);
        }
        // Enter / focus loss (Apply also takes the focus first). The text then shows the value actually set (clamped,
        // or adjusted by a fixed ratio), also while the field keeps the focus after Enter.
        onEditingFinished: {
            const n = parseInt(pf.text, 10);
            const typed = pf.edited;
            pf.edited = false;
            if (typed && isFinite(n) && n !== pf.value)
                pf.committed(n);
            pf.text = String(pf.value);
        }
        // Enter only commits (TextInput emitted editingFinished and passes the key on): the dialog must not apply
        // and close while the user is still setting the other values. Ctrl+Enter still applies.
        Keys.onReturnPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
        Keys.onEnterPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
        Component.onCompleted: pf.text = String(pf.value)
    }
}
