pragma ComponentBehavior: Bound

// RedactEditor — dialog "redact" for images (Backend.preview "image") and PDFs ("pdf_info" + "pdf_page" per page,
// password prompt for locked files, "pdf_search" to find text).
// Draw boxes by dragging on the preview; click a box to select it, drag it to move, drag its corners / edges to
// resize; Delete / Backspace removes it; arrows nudge (Shift ×10); PageUp / PageDown change PDF page; Ctrl+Z undo.
// Styles: Solid (with colour, images only) / Blur / Pixelate for images; PDFs use true redaction (solid black, the
// text under the boxes is removed). Text search (PDF) adds boxes for every match and sends the term too.
// Multi-file: filmstrip, every file keeps its own boxes ("Use these areas for all files" copies them).
// Params (SPEC §5.6): boxes [{page (PDF page, null = images / every page), x, y, w, h (0..1), style, color}],
// terms [str] (PDF), password (unlocked PDF), per_file {path: {boxes, terms}} when files differ, plus the simple
// params of the action (Options).
// Extra API: unlock(password) retries a locked PDF; ready / locked tell the preview state.

import QtQuick
import QtQuick.Effects
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    readonly property bool pdf: root.isPdf(root.currentFile)
    property var fileBoxes: []
    property var fileTerms: []
    property var infos: ({})
    property int page: 0
    property int selected: -1
    property string newStyle: String(root.paramDefault("style", "solid"))
    property string solidColor: {
        const c = root.paramDefault("color", "#000000");
        return (typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c)) ? c.toLowerCase() : "#000000";
    }
    property string searchText: ""
    property bool searching: false
    property string searchMessage: ""
    property bool searchError: false

    readonly property var info: root.infos[root.currentFile?.path ?? ""] ?? null
    readonly property var boxes: root.fileBoxes[root.currentIndex] ?? []
    readonly property var terms: root.fileTerms[root.currentIndex] ?? []
    readonly property int pageCount: root.pdf ? (DU.num(root.info?.pages) ?? 0) : 1
    readonly property var pageInfo: root.pdf ? (root.info?.pageImages?.[String(root.page)] ?? null) : null
    readonly property var pageSize: {
        if (!root.pdf)
            return [DU.num(root.info?.ow) ?? 0, DU.num(root.info?.oh) ?? 0];
        const sizes = root.info?.sizes;
        const s = Array.isArray(sizes) ? sizes[root.page] : null;
        if (Array.isArray(s) && DU.num(s[0]) > 0 && DU.num(s[1]) > 0)
            return [Number(s[0]), Number(s[1])];
        return [DU.num(root.pageInfo?.width_pt) ?? 0, DU.num(root.pageInfo?.height_pt) ?? 0];
    }
    readonly property real srcW: root.pageSize[0]
    readonly property real srcH: root.pageSize[1]
    readonly property string previewPng: root.pdf ? String(root.pageInfo?.png ?? "") : String(root.info?.png ?? "")
    readonly property bool ready: root.info?.status === "ready" && root.srcW > 0 && root.srcH > 0
    readonly property bool locked: root.info?.status === "locked"
    // Boxes drawn on the current page: [{b, i}] (i = index in `boxes`).
    readonly property var pageBoxes: {
        const out = [];
        const list = root.boxes;
        for (let i = 0; i < list.length; i++) {
            const b = list[i];
            if (!root.pdf || b.page === null || b.page === undefined || b.page === root.page)
                out.push({
                    b: b,
                    i: i
                });
        }
        return out;
    }
    readonly property var selectedBox: (root.selected >= 0 && root.selected < root.boxes.length) ? root.boxes[root.selected] : null
    readonly property string activeStyle: root.selectedBox ? String(root.selectedBox.style ?? "solid") : root.newStyle
    readonly property bool lossyStyles: root.fileBoxes.some((list, fi) => !root.isPdf(root.fileList[fi]) && (list ?? []).some(b => b.style === "blur" || b.style === "pixelate"))
    readonly property int totalBoxes: root.fileBoxes.reduce((n, list) => n + (list?.length ?? 0), 0)
    readonly property int totalTerms: root.fileTerms.reduce((n, list) => n + (list?.length ?? 0), 0)
    readonly property int emptyFiles: {
        let n = 0;
        for (let i = 0; i < root.fileCount; i++) {
            if ((root.fileBoxes[i]?.length ?? 0) === 0 && (root.fileTerms[i]?.length ?? 0) === 0)
                n++;
        }
        return n;
    }

    function boxLabel(b: var): string {
        const size = root.pdf ? "" : (" · " + Math.round((b.w ?? 0) * root.srcW) + " × " + Math.round((b.h ?? 0) * root.srcH) + " px");
        if (!root.pdf)
            return I18n.t("dialog.redact.style." + String(b.style ?? "solid")) + size;
        if (b.page === null || b.page === undefined)
            return I18n.t("dialog.redact.all_pages");
        return I18n.t("dialog.redact.page_n", {
            page: Number(b.page) + 1
        });
    }

    function setBoxes(list: var): void {
        const all = root.fileBoxes.slice();
        while (all.length < root.fileCount)
            all.push([]);
        all[root.currentIndex] = list;
        root.fileBoxes = all;
        root.dirty = true;
    }

    function setTerms(list: var): void {
        const all = root.fileTerms.slice();
        while (all.length < root.fileCount)
            all.push([]);
        all[root.currentIndex] = list;
        root.fileTerms = all;
        root.dirty = true;
    }

    function updateBox(index: int, patch: var): void {
        if (index < 0 || index >= root.boxes.length)
            return;
        const list = root.boxes.slice();
        list[index] = Object.assign({}, list[index], patch);
        root.setBoxes(list);
    }

    function pushHistory(): void {
        const h = priv.history.slice(-49);
        h.push({
            boxes: DU.copy(root.fileBoxes),
            terms: DU.copy(root.fileTerms),
            selected: root.selected,
            index: root.currentIndex
        });
        priv.history = h;
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        const snap = h.pop();
        priv.history = h;
        root.fileBoxes = snap.boxes;
        root.fileTerms = snap.terms;
        if (snap.index === root.currentIndex)
            root.selected = snap.selected < root.boxes.length ? snap.selected : -1;
        else
            root.selected = -1;
        root.dirty = root.totalBoxes > 0 || root.totalTerms > 0;
        return true;
    }

    function reset(): void {
        if (root.totalBoxes === 0 && root.totalTerms === 0)
            return;
        root.pushHistory();
        root.fileBoxes = root.fileList.map(() => []);
        root.fileTerms = root.fileList.map(() => []);
        root.selected = -1;
        root.searchMessage = "";
        root.dirty = false;
    }

    function setStyle(style: string): void {
        if (root.pdf && style !== "solid")
            return;
        root.newStyle = style;
        if (root.selectedBox && root.selectedBox.style !== style) {
            root.pushHistory();
            root.updateBox(root.selected, {
                style: style
            });
        }
    }

    function setColor(color: string): void {
        root.solidColor = color;
        if (root.selectedBox && root.selectedBox.style === "solid" && root.selectedBox.color !== color) {
            root.pushHistory();
            root.updateBox(root.selected, {
                color: color
            });
        }
    }

    function deleteBox(index: int): void {
        if (index < 0 || index >= root.boxes.length)
            return;
        root.pushHistory();
        const list = root.boxes.slice();
        const removed = list.splice(index, 1)[0];
        // A search term redacts every occurrence: once one of its matches is removed, keep the remaining matches as
        // plain boxes and stop sending the term.
        if (typeof removed?.term === "string" && removed.term !== "") {
            for (let i = 0; i < list.length; i++) {
                if (list[i].term === removed.term)
                    list[i] = Object.assign({}, list[i], {
                        term: ""
                    });
            }
            root.setTerms(root.terms.filter(t => t !== removed.term));
        }
        root.setBoxes(list);
        if (root.selected === index)
            root.selected = -1;
        else if (root.selected > index)
            root.selected--;
    }

    function removeTerm(term: string): void {
        root.pushHistory();
        root.setTerms(root.terms.filter(t => t !== term));
        root.setBoxes(root.boxes.filter(b => b.term !== term));
        root.selected = -1;
    }

    function clearBoxes(): void {
        if (root.boxes.length === 0 && root.terms.length === 0)
            return;
        root.pushHistory();
        root.setBoxes([]);
        root.setTerms([]);
        root.selected = -1;
    }

    function copyToAll(): void {
        if (root.fileCount < 2 || root.pdf)
            return;
        root.pushHistory();
        const src = root.boxes.map(b => Object.assign({}, b, {
                term: ""
            }));
        const all = [];
        for (let i = 0; i < root.fileCount; i++)
            all.push(root.isPdf(root.fileList[i]) ? (root.fileBoxes[i] ?? []) : DU.copy(src));
        root.fileBoxes = all;
        root.dirty = true;
    }

    function goToPage(p: int): void {
        if (!root.pdf || root.pageCount <= 0)
            return;
        const n = DU.clamp(p, 0, root.pageCount - 1);
        if (n !== root.page) {
            root.page = n;
            if (root.selectedBox && root.selectedBox.page !== null && root.selectedBox.page !== n)
                root.selected = -1;
        }
        priv.ensurePage(root.currentFile, n);
        if (n + 1 < root.pageCount)
            priv.ensurePage(root.currentFile, n + 1);
    }

    function selectBox(index: int): void {
        root.selected = index;
        const b = root.selectedBox;
        if (b && root.pdf && b.page !== null && b.page !== undefined && b.page !== root.page)
            root.goToPage(Number(b.page));
        if (b)
            root.newStyle = String(b.style ?? "solid");
    }

    function nudge(dx: real, dy: real): void {
        const b = root.selectedBox;
        if (!b || !root.ready)
            return;
        priv.keyHistory();
        root.updateBox(root.selected, {
            x: DU.clamp(b.x + dx / root.srcW, 0, 1 - b.w),
            y: DU.clamp(b.y + dy / root.srcH, 0, 1 - b.h)
        });
    }

    function search(): void {
        const q = root.searchText.trim();
        const file = root.currentFile;
        if (!root.pdf || q === "" || root.searching || !file)
            return;
        const index = root.currentIndex;
        const inf = root.info;
        const life = priv.life;
        root.searching = true;
        root.searchMessage = "";
        root.searchError = false;
        const args = {
            path: file.path,
            query: q
        };
        if (typeof inf?.password === "string" && inf.password !== "")
            args.password = inf.password;
        Backend.preview("pdf_search", args, (r, e) => {
            if (!life.alive)
                return;
            root.searching = false;
            if (e) {
                root.searchMessage = String(e.message ?? I18n.t("ui.error.unexpected"));
                root.searchError = true;
                return;
            }
            if (index !== root.currentIndex)
                return;
            const matches = Array.isArray(r?.matches) ? r.matches : [];
            const sizes = Array.isArray(inf?.sizes) ? inf.sizes : [];
            const added = [];
            const pagesHit = [];
            for (const m of matches) {
                const pg = Math.round(Number(m?.page));
                const sz = sizes[pg];
                if (!isFinite(pg) || !Array.isArray(sz) || !(Number(sz[0]) > 0 && Number(sz[1]) > 0))
                    continue;
                const W = Number(sz[0]);
                const H = Number(sz[1]);
                for (const rect of (Array.isArray(m.rects) ? m.rects : [])) {
                    if (!Array.isArray(rect) || rect.length < 4)
                        continue;
                    const pad = 1;
                    const x0 = DU.clamp((Number(rect[0]) - pad) / W, 0, 1);
                    const y0 = DU.clamp((Number(rect[1]) - pad) / H, 0, 1);
                    const x1 = DU.clamp((Number(rect[2]) + pad) / W, 0, 1);
                    const y1 = DU.clamp((Number(rect[3]) + pad) / H, 0, 1);
                    if (!(x1 > x0 && y1 > y0))
                        continue;
                    const box = {
                        page: pg,
                        x: x0,
                        y: y0,
                        w: x1 - x0,
                        h: y1 - y0,
                        style: "solid",
                        color: "#000000",
                        term: q
                    };
                    const dup = root.boxes.concat(added).some(o => o.page === pg && Math.abs(o.x - box.x) < 1e-4 && Math.abs(o.y - box.y) < 1e-4 && Math.abs(o.w - box.w) < 1e-4 && Math.abs(o.h - box.h) < 1e-4);
                    if (dup)
                        continue;
                    added.push(box);
                    if (pagesHit.indexOf(pg) === -1)
                        pagesHit.push(pg);
                }
            }
            const total = Number(r?.count ?? 0) > 0 ? Number(r.count) : added.length;
            if (added.length === 0 && total === 0) {
                root.searchMessage = I18n.t("dialog.redact.no_matches", {
                    query: q
                });
                root.searchError = true;
                return;
            }
            root.pushHistory();
            root.setBoxes(root.boxes.concat(added));
            if (root.terms.indexOf(q) === -1)
                root.setTerms(root.terms.concat([q]));
            root.searchMessage = I18n.t("dialog.redact.matches", {
                count: I18n.formatCount(Math.max(total, added.length)),
                pages: I18n.formatCount(pagesHit.length)
            });
            searchField.clear();
            if (pagesHit.length > 0 && pagesHit.indexOf(root.page) === -1)
                root.goToPage(Math.min(...pagesHit));
        });
    }

    // Retry a locked PDF with a password (the inline prompt calls this too).
    function unlock(password: string): void {
        priv.unlock(password);
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        const params = priv.buildParams();
        if (params === null)
            return;
        root.apply(Object.assign({}, extra, params));
    }

    applyLabel: I18n.t("dialog.redact.apply")
    applyIcon: "ink_eraser"
    canApply: root.totalBoxes > 0 || root.totalTerms > 0
    footerHint: {
        if (root.totalBoxes === 0 && root.totalTerms === 0)
            return I18n.t("dialog.redact.hint_draw");
        if (root.fileCount > 1 && root.emptyFiles > 0)
            return I18n.tn("dialog.redact.empty_files", root.emptyFiles);
        return I18n.tn("dialog.redact.count", root.totalBoxes);
    }
    footerHintError: root.fileCount > 1 && root.emptyFiles > 0 && root.totalBoxes > 0

    Component.onCompleted: {
        root.fileBoxes = root.fileList.map(() => []);
        root.fileTerms = root.fileList.map(() => []);
        if (root.newStyle !== "solid" && root.newStyle !== "blur" && root.newStyle !== "pixelate")
            root.newStyle = "solid";
        priv.ensureInfo(root.currentFile);
        Qt.callLater(() => {
            canvasFocus.forceActiveFocus();
            canvasFocus.viaKeyboard = false;
        });
    }
    Component.onDestruction: priv.life.alive = false
    // Deferred: the first evaluation of currentFile can happen inside another binding (binding loop).
    onCurrentFileChanged: Qt.callLater(() => {
        root.selected = -1;
        root.page = 0;
        root.searchMessage = "";
        priv.ensureInfo(root.currentFile);
    })

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastKeyHistory: 0
        property string dragMode: ""
        property int dragIndex: -1
        property var dragStart: null
        property var pressNorm: null
        property bool created: false
        property bool unlocking: false
        property bool wrongPassword: false
        readonly property bool dragging: priv.dragMode !== ""

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
            if (existing && existing.status !== "error")
                return;
            priv.load(file, "");
        }

        function load(file: var, password: string): void {
            const path = file.path;
            const life = priv.life;
            if (root.isPdf(file)) {
                priv.setInfo(path, {
                    status: "loading",
                    error: "",
                    pageImages: ({})
                });
                const args = {
                    path: path
                };
                if (password !== "")
                    args.password = password;
                Backend.preview("pdf_info", args, (r, e) => {
                    if (!life.alive)
                        return;
                    priv.unlocking = false;
                    if (e || !r) {
                        priv.setInfo(path, {
                            status: password !== "" ? "locked" : "error",
                            error: String(e?.message ?? I18n.t("dialog.preview.failed"))
                        });
                        priv.wrongPassword = password !== "";
                        return;
                    }
                    if (r.needs_password === true) {
                        priv.wrongPassword = password !== "" || r.wrong_password === true;
                        priv.setInfo(path, {
                            status: "locked",
                            error: ""
                        });
                        return;
                    }
                    const pages = Math.max(0, Math.round(DU.num(r.pages) ?? 0));
                    if (pages === 0) {
                        priv.setInfo(path, {
                            status: "error",
                            error: I18n.t("dialog.pdf.no_pages")
                        });
                        return;
                    }
                    priv.wrongPassword = false;
                    priv.setInfo(path, {
                        status: "ready",
                        error: "",
                        pages: pages,
                        sizes: Array.isArray(r.sizes) ? r.sizes : [],
                        password: password,
                        pageImages: ({})
                    });
                    if (root.currentFile?.path === path)
                        root.goToPage(Math.min(root.page, pages - 1));
                });
                return;
            }
            priv.setInfo(path, {
                status: "loading",
                error: ""
            });
            Backend.preview("image", {
                path: path,
                max: 1600
            }, (r, e) => {
                if (!life.alive)
                    return;
                const ok = !e && r && DU.num(r.width) > 0 && DU.num(r.height) > 0 && typeof r.png === "string" && r.png !== "";
                priv.setInfo(path, {
                    status: ok ? "ready" : "error",
                    png: ok ? String(r.png) : "",
                    ow: ok ? Number(r.width) : 0,
                    oh: ok ? Number(r.height) : 0,
                    error: ok ? "" : String(e?.message ?? I18n.t("dialog.preview.failed"))
                });
            });
        }

        function unlock(password: string): void {
            const file = root.currentFile;
            if (!file || password === "")
                return;
            priv.unlocking = true;
            priv.load(file, password);
        }

        function pageDpi(inf: var, page: int): int {
            const s = Array.isArray(inf?.sizes) ? inf.sizes[page] : null;
            const longest = Array.isArray(s) ? Math.max(Number(s[0]) || 0, Number(s[1]) || 0) : 0;
            if (!(longest > 0))
                return 110;
            return Math.round(DU.clamp(1400 * 72 / longest, 40, 200));
        }

        function ensurePage(file: var, page: int): void {
            const path = file?.path ?? "";
            const inf = root.infos[path];
            if (!inf || inf.status !== "ready" || page < 0 || page >= (inf.pages ?? 0))
                return;
            const key = String(page);
            const existing = inf.pageImages?.[key];
            if (existing && existing.status !== "error")
                return;
            priv.setPage(path, key, {
                status: "loading",
                png: "",
                error: ""
            });
            const life = priv.life;
            const args = {
                path: path,
                page: page,
                dpi: priv.pageDpi(inf, page)
            };
            if (typeof inf.password === "string" && inf.password !== "")
                args.password = inf.password;
            Backend.preview("pdf_page", args, (r, e) => {
                if (!life.alive)
                    return;
                const ok = !e && r && typeof r.png === "string" && r.png !== "";
                priv.setPage(path, key, {
                    status: ok ? "ready" : "error",
                    png: ok ? String(r.png) : "",
                    width_pt: ok ? DU.num(r.width_pt) : null,
                    height_pt: ok ? DU.num(r.height_pt) : null,
                    error: ok ? "" : String(e?.message ?? I18n.t("dialog.preview.failed"))
                });
            });
        }

        function setPage(path: string, key: string, value: var): void {
            const inf = root.infos[path];
            if (!inf)
                return;
            const pages = Object.assign({}, inf.pageImages ?? {});
            pages[key] = value;
            priv.setInfo(path, {
                pageImages: pages
            });
        }

        // Pointer in canvas coordinates → what a press there would do: {mode, index}.
        function hitTest(mx: real, my: real): var {
            if (!root.ready)
                return {
                    mode: "",
                    index: -1
                };
            const tol = Theme.s(8);
            const toRect = b => ({
                    L: canvas.imageX + b.x * canvas.imageWidth,
                    T: canvas.imageY + b.y * canvas.imageHeight,
                    R: canvas.imageX + (b.x + b.w) * canvas.imageWidth,
                    B: canvas.imageY + (b.y + b.h) * canvas.imageHeight
                });
            const sel = root.selectedBox;
            const selVisible = sel && (!root.pdf || sel.page === null || sel.page === undefined || sel.page === root.page);
            if (selVisible) {
                const r = toRect(sel);
                const nearL = Math.abs(mx - r.L) <= tol;
                const nearR = Math.abs(mx - r.R) <= tol;
                const nearT = Math.abs(my - r.T) <= tol;
                const nearB = Math.abs(my - r.B) <= tol;
                const inX = mx > r.L - tol && mx < r.R + tol;
                const inY = my > r.T - tol && my < r.B + tol;
                let h = "";
                if (nearT && nearL)
                    h = "tl";
                else if (nearT && nearR)
                    h = "tr";
                else if (nearB && nearL)
                    h = "bl";
                else if (nearB && nearR)
                    h = "br";
                else if (nearT && inX)
                    h = "t";
                else if (nearB && inX)
                    h = "b";
                else if (nearL && inY)
                    h = "l";
                else if (nearR && inY)
                    h = "r";
                if (h !== "")
                    return {
                        mode: h,
                        index: root.selected
                    };
            }
            const list = root.pageBoxes;
            for (let k = list.length - 1; k >= 0; k--) {
                const r = toRect(list[k].b);
                if (mx >= r.L && mx <= r.R && my >= r.T && my <= r.B)
                    return {
                        mode: "move",
                        index: list[k].i
                    };
            }
            return {
                mode: "new",
                index: -1
            };
        }

        function cursorFor(mode: string): int {
            switch (mode) {
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

        function minNorm(): var {
            const px = Theme.s(6);
            return {
                w: px / Math.max(1, canvas.imageWidth),
                h: px / Math.max(1, canvas.imageHeight)
            };
        }

        function press(mx: real, my: real): void {
            const hit = priv.hitTest(mx, my);
            if (hit.mode === "")
                return;
            root.pushHistory();
            const p = canvas.toNorm(mx, my);
            priv.pressNorm = p;
            priv.created = false;
            if (hit.mode === "new") {
                const box = {
                    page: root.pdf ? root.page : null,
                    x: p.x,
                    y: p.y,
                    w: 0,
                    h: 0,
                    style: root.pdf ? "solid" : root.newStyle,
                    color: root.solidColor,
                    term: ""
                };
                const list = root.boxes.concat([box]);
                root.setBoxes(list);
                root.selected = list.length - 1;
                priv.dragIndex = list.length - 1;
                priv.dragStart = Object.assign({}, box);
                priv.created = true;
            } else {
                root.selectBox(hit.index);
                priv.dragIndex = hit.index;
                priv.dragStart = Object.assign({}, root.boxes[hit.index]);
            }
            priv.dragMode = hit.mode;
        }

        function move(mx: real, my: real): void {
            if (!priv.dragging || priv.dragIndex < 0 || !priv.dragStart)
                return;
            const p = canvas.toNorm(mx, my);
            const s = priv.dragStart;
            const m = priv.minNorm();
            const mode = priv.dragMode;
            if (mode === "new") {
                const x0 = Math.min(priv.pressNorm.x, p.x);
                const y0 = Math.min(priv.pressNorm.y, p.y);
                root.updateBox(priv.dragIndex, {
                    x: x0,
                    y: y0,
                    w: Math.abs(p.x - priv.pressNorm.x),
                    h: Math.abs(p.y - priv.pressNorm.y)
                });
                return;
            }
            const dx = p.x - priv.pressNorm.x;
            const dy = p.y - priv.pressNorm.y;
            if (mode === "move") {
                root.updateBox(priv.dragIndex, {
                    x: DU.clamp(s.x + dx, 0, 1 - s.w),
                    y: DU.clamp(s.y + dy, 0, 1 - s.h)
                });
                return;
            }
            let left = s.x;
            let top = s.y;
            let right = s.x + s.w;
            let bottom = s.y + s.h;
            if (mode.indexOf("l") !== -1)
                left = DU.clamp(left + dx, 0, right - m.w);
            if (mode.indexOf("r") !== -1)
                right = DU.clamp(right + dx, left + m.w, 1);
            if (mode.indexOf("t") !== -1)
                top = DU.clamp(top + dy, 0, bottom - m.h);
            if (mode.indexOf("b") !== -1)
                bottom = DU.clamp(bottom + dy, top + m.h, 1);
            root.updateBox(priv.dragIndex, {
                x: left,
                y: top,
                w: right - left,
                h: bottom - top
            });
        }

        function release(): void {
            if (!priv.dragging)
                return;
            const index = priv.dragIndex;
            const b = root.boxes[index];
            const m = priv.minNorm();
            if (priv.created && b && (b.w < m.w || b.h < m.h)) {
                // A click (or a tiny drag) on empty space: no new box, just deselect.
                const h = priv.history.slice();
                const snap = h.pop();
                priv.history = h;
                if (snap) {
                    root.fileBoxes = snap.boxes;
                    root.fileTerms = snap.terms;
                }
                root.selected = -1;
            } else if (!priv.created && b && DU.same(b, priv.dragStart)) {
                // Plain click on a box: selection only, no undo step.
                const h = priv.history.slice();
                h.pop();
                priv.history = h;
            }
            priv.dragMode = "";
            priv.dragIndex = -1;
            priv.dragStart = null;
            priv.created = false;
        }

        function round6(v: real): real {
            return Math.round(v * 1e6) / 1e6;
        }

        function buildParams(): var {
            const entries = [];
            for (let i = 0; i < root.fileCount; i++) {
                const f = root.fileList[i];
                const isPdf = root.isPdf(f);
                const list = (root.fileBoxes[i] ?? []).filter(b => b.w > 0 && b.h > 0).map(b => {
                    const out = {
                        page: isPdf ? ((b.page === null || b.page === undefined) ? null : Number(b.page)) : null,
                        x: priv.round6(b.x),
                        y: priv.round6(b.y),
                        w: priv.round6(b.w),
                        h: priv.round6(b.h),
                        style: isPdf ? "solid" : String(b.style ?? "solid")
                    };
                    if (out.style === "solid")
                        out.color = isPdf ? "#000000" : String(b.color ?? root.solidColor);
                    return out;
                });
                const p = {
                    boxes: list
                };
                if (isPdf) {
                    p.terms = (root.fileTerms[i] ?? []).slice();
                    const pw = root.infos[f.path]?.password;
                    if (typeof pw === "string" && pw !== "")
                        p.password = pw;
                }
                entries.push({
                    path: f.path,
                    params: p
                });
            }
            if (entries.length === 0)
                return null;
            const out = DU.copy(entries[0].params);
            if (root.param("style") !== null)
                out.style = root.pdf ? "solid" : root.newStyle;
            if (root.param("color") !== null)
                out.color = root.solidColor;
            if (entries.length > 1 && entries.some(e => !DU.same(e.params, entries[0].params))) {
                const per = {};
                for (const e of entries)
                    per[e.path] = e.params;
                out.per_file = per;
            }
            return out;
        }
    }

    // ------------------------------------------------------------------ preview area
    Item {
        id: main

        anchors.left: parent.left
        anchors.right: side.left
        anchors.rightMargin: Theme.spacing.large
        anchors.top: parent.top
        anchors.bottom: pager.visible ? pager.top : (strip.visible ? strip.top : parent.bottom)
        anchors.bottomMargin: pager.visible || strip.visible ? Theme.spacing.small : 0

        StyledRect {
            anchors.fill: parent
            radius: Theme.rounding.large
            color: Theme.palette.m3surfaceContainerLow
        }

        PreviewCanvas {
            id: canvas

            anchors.fill: parent
            padding: Theme.s(20)
            source: root.ready ? root.previewPng : ""
            sourceWidth: root.srcW
            sourceHeight: root.srcH
            backdrop: root.pdf ? "white" : Theme.palette.m3surfaceContainerHighest
            visible: root.ready

            Repeater {
                model: root.pageBoxes

                Item {
                    id: boxItem

                    required property var modelData
                    readonly property var b: boxItem.modelData.b
                    readonly property int boxIndex: boxItem.modelData.i
                    readonly property bool isSelected: boxItem.boxIndex === root.selected
                    readonly property string style: root.pdf ? "solid" : String(boxItem.b.style ?? "solid")

                    x: boxItem.b.x * canvas.imageWidth
                    y: boxItem.b.y * canvas.imageHeight
                    width: Math.max(1, boxItem.b.w * canvas.imageWidth)
                    height: Math.max(1, boxItem.b.h * canvas.imageHeight)

                    Rectangle {
                        anchors.fill: parent
                        visible: boxItem.style === "solid"
                        color: root.pdf ? "black" : String(boxItem.b.color ?? root.solidColor)
                        opacity: root.pdf ? 0.85 : 1
                    }

                    ShaderEffectSource {
                        id: blurSource

                        anchors.fill: parent
                        visible: false
                        live: true
                        sourceItem: boxItem.style === "blur" ? canvas.image : null
                        sourceRect: Qt.rect(boxItem.x, boxItem.y, boxItem.width, boxItem.height)
                    }

                    MultiEffect {
                        anchors.fill: parent
                        visible: boxItem.style === "blur"
                        source: blurSource
                        autoPaddingEnabled: false
                        blurEnabled: true
                        blur: 1.0
                        blurMax: 48
                    }

                    ShaderEffectSource {
                        anchors.fill: parent
                        visible: boxItem.style === "pixelate"
                        live: true
                        smooth: false
                        sourceItem: boxItem.style === "pixelate" ? canvas.image : null
                        sourceRect: Qt.rect(boxItem.x, boxItem.y, boxItem.width, boxItem.height)
                        textureSize: Qt.size(Math.max(2, Math.round(boxItem.width / Theme.s(9))), Math.max(2, Math.round(boxItem.height / Theme.s(9))))
                    }

                    // Outline: dark + light so it reads on any content.
                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: -Math.max(1, Math.round(Theme.scale))
                        color: "transparent"
                        border.width: Math.max(1, Math.round(Theme.scale))
                        border.color: Qt.rgba(0, 0, 0, 0.5)
                        visible: !boxItem.isSelected
                    }
                    Rectangle {
                        anchors.fill: parent
                        color: "transparent"
                        border.width: boxItem.isSelected ? Math.max(2, Math.round(2 * Theme.scale)) : Math.max(1, Math.round(Theme.scale))
                        border.color: boxItem.isSelected ? Theme.palette.m3primary : Qt.rgba(1, 1, 1, 0.8)
                    }

                    MaterialIcon {
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        visible: (boxItem.b.page === null || boxItem.b.page === undefined) && root.pdf && boxItem.width > Theme.s(28) && boxItem.height > Theme.s(20)
                        text: "content_copy"
                        size: Theme.font.iconSize.small
                        color: "white"
                    }

                    Repeater {
                        model: boxItem.isSelected ? [[0, 0], [1, 0], [0, 1], [1, 1]] : []

                        Rectangle {
                            required property var modelData

                            readonly property real size: Theme.s(12)

                            x: modelData[0] * boxItem.width - size / 2
                            y: modelData[1] * boxItem.height - size / 2
                            width: size
                            height: size
                            radius: Theme.rounding.extraSmall
                            color: Theme.palette.m3primary
                            border.width: Math.max(1, Math.round(1.5 * Theme.scale))
                            border.color: Theme.palette.m3onPrimary
                        }
                    }
                }
            }
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading" || (root.pdf && root.info?.status === "ready" && (root.pageInfo === null || root.pageInfo?.status === "loading"))
            error: {
                if (root.info?.status === "error")
                    return String(root.info?.error ?? "") || I18n.t("dialog.preview.failed");
                if (root.pdf && root.pageInfo?.status === "error")
                    return String(root.pageInfo?.error ?? "") || I18n.t("dialog.preview.failed");
                return "";
            }
            onRetry: {
                if (root.info?.status === "error")
                    priv.load(root.currentFile, "");
                else
                    priv.ensurePage(root.currentFile, root.page);
            }
        }

        PasswordPrompt {
            id: passwordPrompt

            anchors.fill: parent
            visible: root.locked
            wrong: priv.wrongPassword
            busy: priv.unlocking
            onSubmitted: password => root.unlock(password)
            onVisibleChanged: {
                if (passwordPrompt.visible)
                    Qt.callLater(() => passwordPrompt.focusField());
            }
        }

        FocusScope {
            id: canvasFocus

            // Focus ring only for keyboard focus (Tab), not after clicks or the initial programmatic focus.
            property bool viaKeyboard: false

            anchors.fill: parent
            activeFocusOnTab: true
            onActiveFocusChanged: canvasFocus.viaKeyboard = canvasFocus.activeFocus && !pointer.pressed
            enabled: root.ready

            Keys.onPressed: event => {
                const big = (event.modifiers & Qt.ShiftModifier) !== 0;
                const step = big ? 10 : 1;
                let handled = true;
                switch (event.key) {
                case Qt.Key_Left:
                    root.nudge(-step, 0);
                    break;
                case Qt.Key_Right:
                    root.nudge(step, 0);
                    break;
                case Qt.Key_Up:
                    root.nudge(0, -step);
                    break;
                case Qt.Key_Down:
                    root.nudge(0, step);
                    break;
                case Qt.Key_Delete:
                case Qt.Key_Backspace:
                    if (root.selected >= 0)
                        root.deleteBox(root.selected);
                    else
                        handled = false;
                    break;
                case Qt.Key_PageUp:
                    root.goToPage(root.page - 1);
                    break;
                case Qt.Key_PageDown:
                    root.goToPage(root.page + 1);
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
                enabled: root.ready
                preventStealing: true
                cursorShape: priv.cursorFor(priv.dragging ? priv.dragMode : pointer.hover)

                onPositionChanged: mouse => {
                    if (priv.dragging)
                        priv.move(mouse.x, mouse.y);
                    else
                        pointer.hover = priv.hitTest(mouse.x, mouse.y).mode;
                }
                onPressed: mouse => {
                    canvasFocus.forceActiveFocus();
                    priv.press(mouse.x, mouse.y);
                }
                onReleased: priv.release()
                onCanceled: priv.release()
                onExited: pointer.hover = ""
            }
        }
    }

    // ------------------------------------------------------------------ PDF page navigator
    Item {
        id: pager

        anchors.left: main.left
        anchors.right: main.right
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.small : 0
        height: Theme.s(44)
        visible: root.pdf && root.info?.status === "ready" && root.pageCount > 1

        Row {
            anchors.centerIn: parent
            spacing: Theme.spacing.small

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Text
                icon: "first_page"
                disabled: root.page <= 0
                tooltip: I18n.t("dialog.pdf.first_page")
                onClicked: root.goToPage(0)
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Tonal
                icon: "chevron_left"
                disabled: root.page <= 0
                tooltip: I18n.t("dialog.pdf.prev_page")
                onClicked: root.goToPage(root.page - 1)
            }

            StyledText {
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(implicitWidth, Theme.s(130))
                horizontalAlignment: Text.AlignHCenter
                text: I18n.t("dialog.pdf.page_of", {
                    page: I18n.formatCount(root.page + 1),
                    total: I18n.formatCount(root.pageCount)
                })
                font.pointSize: Theme.font.size.normal
                font.features: ({
                        "tnum": 1
                    })
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Tonal
                icon: "chevron_right"
                disabled: root.page >= root.pageCount - 1
                tooltip: I18n.t("dialog.pdf.next_page")
                onClicked: root.goToPage(root.page + 1)
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                type: ButtonBase.Text
                icon: "last_page"
                disabled: root.page >= root.pageCount - 1
                tooltip: I18n.t("dialog.pdf.last_page")
                onClicked: root.goToPage(root.pageCount - 1)
            }

            StyledText {
                anchors.verticalCenter: parent.verticalCenter
                visible: root.pageBoxes.length > 0
                text: "· " + I18n.tn("dialog.redact.count", root.pageBoxes.length)
                font.pointSize: Theme.font.size.small
                color: Theme.palette.m3onSurfaceVariant
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
        width: Theme.s(310)

        Column {
            width: side.width - Theme.s(10)
            spacing: Theme.spacing.large

            // Style
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.redact.style")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small

                    Repeater {
                        model: [
                            {
                                id: "solid",
                                icon: "rectangle"
                            },
                            {
                                id: "blur",
                                icon: "blur_on"
                            },
                            {
                                id: "pixelate",
                                icon: "grid_on"
                            }
                        ]

                        Chip {
                            id: styleChip

                            required property var modelData

                            type: Chip.Filter
                            activeFocusOnTab: true
                            icon: String(styleChip.modelData.icon)
                            text: I18n.t("dialog.redact.style." + styleChip.modelData.id)
                            selected: (root.pdf ? "solid" : root.activeStyle) === styleChip.modelData.id
                            disabled: root.pdf && styleChip.modelData.id !== "solid"
                            tooltip: root.pdf && styleChip.modelData.id !== "solid" ? I18n.t("dialog.redact.pdf_solid_only") : ""
                            onClicked: root.setStyle(String(styleChip.modelData.id))
                        }
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.pdf
                    text: I18n.t("dialog.redact.pdf_true")
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }

                Row {
                    width: parent.width
                    spacing: Theme.spacing.small
                    visible: !root.pdf && root.lossyStyles

                    MaterialIcon {
                        text: "warning"
                        size: Theme.font.iconSize.small
                        color: Theme.palette.m3error
                    }

                    StyledText {
                        width: parent.width - Theme.font.iconSize.small * 1.6 - parent.spacing
                        text: I18n.t("dialog.redact.lossy_warning")
                        wrapMode: Text.Wrap
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3error
                    }
                }
            }

            // Colour (solid boxes on images)
            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: !root.pdf && root.activeStyle === "solid"

                StyledText {
                    text: I18n.t("dialog.redact.color")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                ColorPicker {
                    id: colorPicker

                    readonly property string wanted: root.selectedBox?.style === "solid" ? String(root.selectedBox?.color ?? root.solidColor) : root.solidColor

                    width: parent.width
                    alpha: false
                    showTheme: false
                    value: colorPicker.wanted
                    extraColors: ["#000000", "#ffffff", "#808080"]
                    onPicked: value => {
                        root.setColor(value);
                        // The picker assigned `value` itself: follow the selection again.
                        colorPicker.value = Qt.binding(() => colorPicker.wanted);
                    }
                }
            }

            // PDF text search
            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: root.pdf && root.ready

                StyledText {
                    text: I18n.t("dialog.redact.search")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                M3TextField {
                    id: searchField

                    width: parent.width
                    leadingIcon: "search"
                    label: I18n.t("dialog.redact.search_label")
                    clearButton: true
                    disabled: root.searching
                    text: root.searchText
                    onTextChanged: root.searchText = searchField.text
                    // Enter searches; it must not reach the dialog (which would apply).
                    Keys.onReturnPressed: event => {
                        root.search();
                        event.accepted = true;
                    }
                    Keys.onEnterPressed: event => {
                        root.search();
                        event.accepted = true;
                    }
                }

                TextButton {
                    type: ButtonBase.Tonal
                    icon: root.searching ? "hourglass_top" : "ink_eraser"
                    text: I18n.t("dialog.redact.redact_matches")
                    disabled: root.searching || root.searchText.trim() === ""
                    onClicked: root.search()
                }

                StyledText {
                    width: parent.width
                    visible: text !== ""
                    text: root.searchMessage
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: root.searchError ? Theme.palette.m3error : Theme.palette.m3onSurfaceVariant
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacing.small
                    visible: root.terms.length > 0

                    Repeater {
                        model: root.terms

                        Chip {
                            id: termChip

                            required property var modelData

                            type: Chip.Input
                            removable: true
                            icon: "text_fields"
                            text: String(termChip.modelData)
                            tooltip: I18n.t("dialog.redact.term_tooltip")
                            onRemoved: root.removeTerm(String(termChip.modelData))
                        }
                    }
                }
            }

            // Boxes list
            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: root.ready

                Item {
                    width: parent.width
                    height: Math.max(listTitle.implicitHeight, clearButton.height)

                    StyledText {
                        id: listTitle

                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.t("dialog.redact.areas") + (root.boxes.length > 0 ? " (" + root.boxes.length + ")" : "")
                        font.pointSize: Theme.font.size.normal
                        font.weight: Font.Medium
                    }

                    IconButton {
                        id: clearButton

                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        type: ButtonBase.Text
                        icon: "delete_sweep"
                        iconSize: Theme.font.iconSize.small
                        disabled: root.boxes.length === 0 && root.terms.length === 0
                        tooltip: I18n.t("dialog.redact.clear")
                        onClicked: root.clearBoxes()
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.boxes.length === 0
                    text: I18n.t("dialog.redact.empty")
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }

                Repeater {
                    model: root.boxes

                    StyledRect {
                        id: boxRow

                        required property var modelData
                        required property int index
                        readonly property bool isSelected: boxRow.index === root.selected

                        width: parent.width
                        height: Theme.s(40)
                        radius: Theme.rounding.medium
                        color: boxRow.isSelected ? Theme.palette.m3secondaryContainer : Theme.palette.m3surfaceContainerHigh

                        StateLayer {
                            color: boxRow.isSelected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurface
                            onClicked: {
                                root.selectBox(boxRow.index);
                                canvasFocus.forceActiveFocus();
                            }
                        }

                        Rectangle {
                            id: swatch

                            anchors.left: parent.left
                            anchors.leftMargin: Theme.padding.medium
                            anchors.verticalCenter: parent.verticalCenter
                            width: Theme.s(18)
                            height: width
                            radius: Theme.rounding.extraSmall
                            visible: root.pdf || String(boxRow.modelData.style ?? "solid") === "solid"
                            color: root.pdf ? "black" : String(boxRow.modelData.color ?? root.solidColor)
                            border.width: Math.max(1, Math.round(Theme.scale))
                            border.color: Theme.palette.m3outline
                        }

                        MaterialIcon {
                            anchors.centerIn: swatch
                            anchors.verticalCenterOffset: 1
                            visible: !swatch.visible
                            text: String(boxRow.modelData.style) === "blur" ? "blur_on" : "grid_on"
                            size: Theme.font.iconSize.medium
                            color: boxRow.isSelected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurfaceVariant
                        }

                        StyledText {
                            anchors.left: swatch.right
                            anchors.leftMargin: Theme.spacing.medium
                            anchors.right: rowDelete.left
                            anchors.rightMargin: Theme.spacing.small
                            anchors.verticalCenter: parent.verticalCenter
                            text: {
                                const t = String(boxRow.modelData.term ?? "");
                                const base = root.boxLabel(boxRow.modelData);
                                return t !== "" ? base + " · “" + t + "”" : base;
                            }
                            elide: Text.ElideRight
                            color: boxRow.isSelected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurface
                        }

                        IconButton {
                            id: rowDelete

                            anchors.right: parent.right
                            anchors.rightMargin: Theme.spacing.extraSmall
                            anchors.verticalCenter: parent.verticalCenter
                            type: ButtonBase.Text
                            icon: "close"
                            iconSize: Theme.font.iconSize.small
                            tooltip: I18n.t("dialog.redact.delete")
                            onClicked: root.deleteBox(boxRow.index)
                        }
                    }
                }

                // PDF: repeat the selected box on every page (headers, footers, stamps).
                Item {
                    width: parent.width
                    height: Math.max(allPagesText.implicitHeight, allPagesSwitch.implicitHeight)
                    visible: root.pdf && root.selectedBox !== null && root.pageCount > 1

                    StyledText {
                        id: allPagesText

                        anchors.left: parent.left
                        anchors.right: allPagesSwitch.left
                        anchors.rightMargin: Theme.spacing.medium
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.t("dialog.redact.repeat_all_pages")
                        wrapMode: Text.Wrap
                    }

                    M3Switch {
                        id: allPagesSwitch

                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.selectedBox !== null && (root.selectedBox.page === null || root.selectedBox.page === undefined)
                        onToggled: {
                            root.pushHistory();
                            root.updateBox(root.selected, {
                                page: allPagesSwitch.checked ? null : root.page,
                                term: ""
                            });
                            // toggle() assigned `checked`: bind it again (other boxes, undo).
                            allPagesSwitch.checked = Qt.binding(() => root.selectedBox !== null && (root.selectedBox.page === null || root.selectedBox.page === undefined));
                        }
                    }
                }

                TextButton {
                    visible: root.fileCount > 1 && !root.pdf && root.boxes.length > 0
                    type: ButtonBase.Text
                    icon: "select_all"
                    text: I18n.t("dialog.redact.copy_to_all")
                    onClicked: root.copyToAll()
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
                    exclude: root.optionExcludes(["boxes", "terms", "style", "color", "password", "per_file"])
                }
            }

            StyledText {
                width: parent.width
                text: root.pdf ? I18n.t("dialog.redact.keys_pdf") : I18n.t("dialog.redact.keys")
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
        edited: root.fileList.map((f, i) => (root.fileBoxes[i]?.length ?? 0) > 0 || (root.fileTerms[i]?.length ?? 0) > 0)
        onSelected: index => root.currentIndex = index
    }
}
