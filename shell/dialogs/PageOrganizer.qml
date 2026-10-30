pragma ComponentBehavior: Bound

// PageOrganizer — dialog "pages" (PDF organize): grid of page thumbnails (Backend.preview "pdf_info" + low-dpi
// "pdf_page", loaded lazily as cells appear; password prompt for locked files).
// Drag a page to move it (the grid auto-scrolls near the edges); per-page rotate ↺ ↻, duplicate and delete buttons on
// hover; click / Ctrl+click / Shift+click select; toolbar for the selection, insert blank page, reverse, select all.
// Keyboard: arrows move the selection, Alt+← / Alt+→ move the selected page, Ctrl+A select all, Delete, R / L rotate
// right / left, Ctrl+D duplicate, Ctrl+Z undo.
// Params (SPEC §5.6): pageops [{src (0-based), rotate}] in the new order (missing pages = deleted; blank pages are
// {blank: true, rotate, width, height} in points), password when the PDF was unlocked, plus the simple params of the
// action (Options).
// Extra API: unlock(password) retries a locked PDF; ready / locked / count tell the state.

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    readonly property var file: root.fileList[0] ?? null
    property var info: null
    property var thumbs: ({})
    property var selection: []
    property int anchorUid: -1
    readonly property int count: pageModel.count
    readonly property int originalCount: Math.max(0, Math.round(DU.num(root.info?.pages) ?? 0))
    readonly property bool ready: root.info?.status === "ready"
    readonly property bool locked: root.info?.status === "locked"
    property int revision: 0
    readonly property var stats: root.computeStats(root.revision)

    // `rev` only makes the binding re-evaluate when the model changes (ListModel rows are not tracked).
    function computeStats(rev: int): var {
        const used = {};
        let blanks = 0;
        let dups = 0;
        for (let i = 0; i < pageModel.count; i++) {
            const s = pageModel.get(i).src;
            if (s < 0)
                blanks++;
            else if (used[s])
                dups++;
            else
                used[s] = true;
        }
        const kept = Object.keys(used).length;
        return {
            kept: kept,
            deleted: Math.max(0, root.originalCount - kept),
            added: blanks + dups,
            revision: rev
        };
    }

    function rows(): var {
        const out = [];
        for (let i = 0; i < pageModel.count; i++) {
            const r = pageModel.get(i);
            out.push({
                uid: r.uid,
                src: r.src,
                rotate: r.rotate,
                spin: r.spin,
                pw: r.pw,
                ph: r.ph
            });
        }
        return out;
    }

    function restoreRows(list: var): void {
        pageModel.clear();
        for (const r of list)
            pageModel.append(r);
        root.revision++;
    }

    function rowAt(index: int, rev: int): var {
        if (index < 0 || index >= pageModel.count || rev < 0)
            return null;
        const r = pageModel.get(index);
        return {
            uid: r.uid,
            src: r.src,
            rotate: r.rotate,
            spin: r.spin,
            pw: r.pw,
            ph: r.ph
        };
    }

    function indexOfUid(uid: int): int {
        for (let i = 0; i < pageModel.count; i++) {
            if (pageModel.get(i).uid === uid)
                return i;
        }
        return -1;
    }

    function isSelected(uid: int): bool {
        return root.selection.indexOf(uid) !== -1;
    }

    function selectedIndices(): var {
        const out = [];
        for (let i = 0; i < pageModel.count; i++) {
            if (root.selection.indexOf(pageModel.get(i).uid) !== -1)
                out.push(i);
        }
        return out;
    }

    function pageOps(): var {
        const ops = [];
        for (let i = 0; i < pageModel.count; i++) {
            const r = pageModel.get(i);
            if (r.src >= 0)
                ops.push({
                    src: r.src,
                    rotate: r.rotate
                });
            else
                ops.push({
                    blank: true,
                    rotate: r.rotate,
                    width: Math.round(r.pw * 100) / 100,
                    height: Math.round(r.ph * 100) / 100
                });
        }
        return ops;
    }

    function changed(): void {
        root.revision++;
        root.dirty = !DU.same(root.pageOps(), priv.initialOps);
        // Drop selected uids that no longer exist.
        const alive = [];
        for (let i = 0; i < pageModel.count; i++)
            alive.push(pageModel.get(i).uid);
        const sel = root.selection.filter(u => alive.indexOf(u) !== -1);
        if (sel.length !== root.selection.length)
            root.selection = sel;
    }

    function pushHistory(): void {
        const h = priv.history.slice(-49);
        h.push({
            rows: root.rows(),
            selection: root.selection.slice()
        });
        priv.history = h;
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        const snap = h.pop();
        priv.history = h;
        root.restoreRows(snap.rows);
        root.selection = snap.selection;
        root.changed();
        return true;
    }

    function reset(): void {
        if (!root.ready)
            return;
        root.pushHistory();
        priv.buildInitial();
        root.selection = [];
        root.changed();
    }

    function select(uid: int, modifiers: int): void {
        const ctrl = (modifiers & Qt.ControlModifier) !== 0;
        const shift = (modifiers & Qt.ShiftModifier) !== 0;
        if (shift && root.anchorUid >= 0 && root.indexOfUid(root.anchorUid) >= 0) {
            const a = root.indexOfUid(root.anchorUid);
            const b = root.indexOfUid(uid);
            const lo = Math.min(a, b);
            const hi = Math.max(a, b);
            const sel = ctrl ? root.selection.slice() : [];
            for (let i = lo; i <= hi; i++) {
                const u = pageModel.get(i).uid;
                if (sel.indexOf(u) === -1)
                    sel.push(u);
            }
            root.selection = sel;
            return;
        }
        if (ctrl) {
            const sel = root.selection.slice();
            const k = sel.indexOf(uid);
            if (k === -1)
                sel.push(uid);
            else
                sel.splice(k, 1);
            root.selection = sel;
        } else {
            root.selection = [uid];
        }
        root.anchorUid = uid;
    }

    function selectAll(): void {
        const sel = [];
        for (let i = 0; i < pageModel.count; i++)
            sel.push(pageModel.get(i).uid);
        root.selection = sel;
    }

    function targets(uid: var): var {
        // A page's own button acts on that page, or on the whole selection when the page is part of it.
        if (typeof uid === "number" && uid >= 0 && !root.isSelected(uid))
            return [uid];
        return root.selection.slice();
    }

    function rotatePages(uids: var, delta: int): void {
        if (uids.length === 0)
            return;
        root.pushHistory();
        for (let i = 0; i < pageModel.count; i++) {
            const r = pageModel.get(i);
            if (uids.indexOf(r.uid) === -1)
                continue;
            pageModel.setProperty(i, "rotate", ((r.rotate + delta) % 360 + 360) % 360);
            pageModel.setProperty(i, "spin", r.spin + delta);
        }
        root.changed();
    }

    function deletePages(uids: var): void {
        if (uids.length === 0)
            return;
        root.pushHistory();
        for (let i = pageModel.count - 1; i >= 0; i--) {
            if (uids.indexOf(pageModel.get(i).uid) !== -1)
                pageModel.remove(i, 1);
        }
        root.selection = root.selection.filter(u => uids.indexOf(u) === -1);
        root.changed();
    }

    function duplicatePages(uids: var): void {
        if (uids.length === 0)
            return;
        root.pushHistory();
        const added = [];
        for (let i = pageModel.count - 1; i >= 0; i--) {
            const r = pageModel.get(i);
            if (uids.indexOf(r.uid) === -1)
                continue;
            const copy = {
                uid: ++priv.nextUid,
                src: r.src,
                rotate: r.rotate,
                spin: r.spin,
                pw: r.pw,
                ph: r.ph
            };
            pageModel.insert(i + 1, copy);
            added.push(copy.uid);
        }
        root.selection = added.reverse();
        root.changed();
    }

    function insertBlank(): void {
        if (!root.ready)
            return;
        root.pushHistory();
        const idx = root.selectedIndices();
        const at = idx.length > 0 ? idx[idx.length - 1] + 1 : pageModel.count;
        const ref = pageModel.count > 0 ? pageModel.get(Math.max(0, Math.min(at - 1, pageModel.count - 1))) : null;
        const landscape = ref ? (ref.rotate % 180 === 90) : false;
        const w = ref ? (landscape ? ref.ph : ref.pw) : 595.28;
        const h = ref ? (landscape ? ref.pw : ref.ph) : 841.89;
        const uid = ++priv.nextUid;
        pageModel.insert(at, {
            uid: uid,
            src: -1,
            rotate: 0,
            spin: 0,
            pw: w,
            ph: h
        });
        root.selection = [uid];
        root.anchorUid = uid;
        root.changed();
        grid.positionViewAtIndex(at, GridView.Contain);
    }

    function reverseOrder(): void {
        if (pageModel.count < 2)
            return;
        root.pushHistory();
        const idx = root.selectedIndices();
        if (idx.length > 1) {
            // Reverse only the selected pages, in their slots.
            const list = root.rows();
            const picked = idx.map(i => list[i]).reverse();
            idx.forEach((slot, k) => list[slot] = picked[k]);
            root.restoreRows(list);
        } else {
            root.restoreRows(root.rows().reverse());
        }
        root.changed();
    }

    function moveSelected(delta: int): void {
        const idx = root.selectedIndices();
        if (idx.length !== 1)
            return;
        const from = idx[0];
        const to = DU.clamp(from + delta, 0, pageModel.count - 1);
        if (to === from)
            return;
        priv.keyHistory();
        pageModel.move(from, to, 1);
        root.changed();
        grid.positionViewAtIndex(to, GridView.Contain);
    }

    function moveFocus(delta: int): void {
        if (pageModel.count === 0)
            return;
        const idx = root.selectedIndices();
        const cur = idx.length > 0 ? idx[idx.length - 1] : -1;
        const next = DU.clamp(cur < 0 ? 0 : cur + delta, 0, pageModel.count - 1);
        const uid = pageModel.get(next).uid;
        root.selection = [uid];
        root.anchorUid = uid;
        grid.positionViewAtIndex(next, GridView.Contain);
    }

    function requestThumb(src: int): void {
        if (src < 0 || !root.ready)
            return;
        const key = String(src);
        const existing = root.thumbs[key];
        if (existing && existing.status !== "error")
            return;
        const next = Object.assign({}, root.thumbs);
        next[key] = {
            status: "loading",
            png: ""
        };
        root.thumbs = next;
        const life = priv.life;
        const args = {
            path: root.file?.path ?? "",
            page: src,
            dpi: priv.thumbDpi(src)
        };
        if (typeof root.info?.password === "string" && root.info.password !== "")
            args.password = root.info.password;
        Backend.preview("pdf_page", args, (r, e) => {
            if (!life.alive)
                return;
            const ok = !e && r && typeof r.png === "string" && r.png !== "";
            const upd = Object.assign({}, root.thumbs);
            upd[key] = {
                status: ok ? "ready" : "error",
                png: ok ? String(r.png) : ""
            };
            root.thumbs = upd;
        });
    }

    // Retry a locked PDF with a password (the inline prompt calls this too).
    function unlock(password: string): void {
        if (password === "" || priv.unlocking)
            return;
        priv.unlocking = true;
        priv.load(password);
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        const params = {
            pageops: root.pageOps()
        };
        if (typeof root.info?.password === "string" && root.info.password !== "")
            params.password = root.info.password;
        root.apply(Object.assign({}, extra, params));
    }

    applyLabel: I18n.t("dialog.pages.apply")
    applyIcon: "view_module"
    // Blank pages alone do not make a PDF: the backend needs at least one page of the document (pdf.err.no_pages_left).
    canApply: root.ready && root.stats.kept > 0 && root.dirty
    footerHint: {
        if (!root.ready)
            return "";
        if (root.stats.kept === 0)
            return I18n.t("dialog.pages.none_left");
        let t = I18n.tn("dialog.pages.count", root.count);
        if (root.stats.deleted > 0)
            t += " · " + I18n.tn("dialog.pages.deleted", root.stats.deleted);
        if (root.stats.added > 0)
            t += " · " + I18n.tn("dialog.pages.added", root.stats.added);
        if (!root.dirty)
            t += " · " + I18n.t("dialog.pages.unchanged");
        return t;
    }
    footerHintError: root.ready && root.stats.kept === 0
    implicitWidth: Theme.s(1000)
    implicitHeight: Theme.s(680)

    Component.onCompleted: {
        priv.load("");
        Qt.callLater(() => grid.forceActiveFocus());
    }
    Component.onDestruction: priv.life.alive = false

    ListModel {
        id: pageModel
    }

    QtObject {
        id: priv

        readonly property var life: ({
                alive: true
            })
        property var history: []
        property double lastKeyHistory: 0
        property int nextUid: 0
        property var initialOps: []
        property bool unlocking: false
        property bool wrongPassword: false
        // Drag state.
        property int pressUid: -1
        property point pressPos: Qt.point(0, 0)
        property int dragUid: -1
        property point dragPos: Qt.point(0, 0)
        readonly property bool dragging: priv.dragUid >= 0

        function keyHistory(): void {
            const now = Date.now();
            if (now - priv.lastKeyHistory > 800)
                root.pushHistory();
            priv.lastKeyHistory = now;
        }

        function sizeOf(src: int): var {
            const sizes = Array.isArray(root.info?.sizes) ? root.info.sizes : [];
            const s = sizes[src];
            if (Array.isArray(s) && Number(s[0]) > 0 && Number(s[1]) > 0)
                return [Number(s[0]), Number(s[1])];
            return [595.28, 841.89];
        }

        function thumbDpi(src: int): int {
            const s = priv.sizeOf(src);
            return Math.round(DU.clamp(260 * 72 / Math.max(s[0], s[1]), 18, 96));
        }

        function buildInitial(): void {
            const list = [];
            for (let i = 0; i < root.originalCount; i++) {
                const s = priv.sizeOf(i);
                list.push({
                    uid: ++priv.nextUid,
                    src: i,
                    rotate: 0,
                    spin: 0,
                    pw: s[0],
                    ph: s[1]
                });
            }
            root.restoreRows(list);
            priv.initialOps = root.pageOps();
        }

        function load(password: string): void {
            const path = root.file?.path ?? "";
            if (path === "") {
                root.info = {
                    status: "error",
                    error: I18n.t("dialog.preview.failed")
                };
                return;
            }
            const life = priv.life;
            root.info = Object.assign({}, root.info ?? {}, {
                status: "loading",
                error: ""
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
                    priv.wrongPassword = password !== "";
                    root.info = {
                        status: password !== "" ? "locked" : "error",
                        error: String(e?.message ?? I18n.t("dialog.preview.failed"))
                    };
                    return;
                }
                if (r.needs_password === true) {
                    priv.wrongPassword = password !== "" || r.wrong_password === true;
                    root.info = {
                        status: "locked",
                        error: ""
                    };
                    return;
                }
                const pages = Math.max(0, Math.round(DU.num(r.pages) ?? 0));
                if (pages === 0) {
                    root.info = {
                        status: "error",
                        error: I18n.t("dialog.pdf.no_pages")
                    };
                    return;
                }
                priv.wrongPassword = false;
                root.thumbs = ({});
                root.info = {
                    status: "ready",
                    error: "",
                    pages: pages,
                    sizes: Array.isArray(r.sizes) ? r.sizes : [],
                    password: password
                };
                priv.history = [];
                priv.buildInitial();
                root.selection = [];
                root.changed();
            });
        }

        function startDrag(uid: int): void {
            root.pushHistory();
            priv.dragUid = uid;
            if (!root.isSelected(uid)) {
                root.selection = [uid];
                root.anchorUid = uid;
            }
        }

        // p: pointer in grid (viewport) coordinates. The slot is computed from the grid geometry, not with
        // GridView.indexAt(): that one hit-tests the (animating) delegates and makes the page oscillate.
        function dragTo(p: point): void {
            priv.dragPos = p;
            const from = root.indexOfUid(priv.dragUid);
            if (from < 0 || pageModel.count === 0)
                return;
            const cols = Math.max(1, Math.floor(grid.width / Math.max(1, grid.cellWidth)));
            const col = Math.min(cols - 1, Math.floor(DU.clamp(p.x, 0, grid.width - 1) / Math.max(1, grid.cellWidth)));
            const row = Math.floor(Math.max(0, p.y + grid.contentY - grid.originY) / Math.max(1, grid.cellHeight));
            const to = Math.min(pageModel.count - 1, row * cols + col);
            if (to !== from) {
                pageModel.move(from, to, 1);
                root.revision++;
            }
        }

        function endDrag(): void {
            if (!priv.dragging)
                return;
            priv.dragUid = -1;
            const h = priv.history;
            root.changed();
            // Dropped where it started: no undo step.
            if (h.length > 0 && DU.same(h[h.length - 1].rows.map(r => r.uid), root.rows().map(r => r.uid))) {
                const copy = h.slice();
                copy.pop();
                priv.history = copy;
            }
        }
    }

    // ------------------------------------------------------------------ toolbar
    Item {
        id: toolbar

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Theme.s(48)
        visible: root.ready

        Row {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacing.extraSmall

            ToolButton {
                icon: "rotate_left"
                tooltip: I18n.t("dialog.pages.rotate_left")
                disabled: root.selection.length === 0
                onClicked: root.rotatePages(root.selection.slice(), -90)
            }
            ToolButton {
                icon: "rotate_right"
                tooltip: I18n.t("dialog.pages.rotate_right")
                disabled: root.selection.length === 0
                onClicked: root.rotatePages(root.selection.slice(), 90)
            }
            ToolButton {
                icon: "content_copy"
                tooltip: I18n.t("dialog.pages.duplicate")
                disabled: root.selection.length === 0
                onClicked: root.duplicatePages(root.selection.slice())
            }
            ToolButton {
                icon: "delete"
                danger: true
                tooltip: I18n.t("dialog.pages.delete")
                disabled: root.selection.length === 0
                onClicked: root.deletePages(root.selection.slice())
            }

            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(1, Math.round(Theme.scale))
                height: Theme.s(24)
                color: Theme.palette.m3outlineVariant
            }

            ToolButton {
                icon: "note_add"
                tooltip: I18n.t("dialog.pages.insert_blank")
                onClicked: root.insertBlank()
            }
            ToolButton {
                icon: "swap_vert"
                tooltip: root.selection.length > 1 ? I18n.t("dialog.pages.reverse_selected") : I18n.t("dialog.pages.reverse")
                disabled: root.count < 2
                onClicked: root.reverseOrder()
            }
            ToolButton {
                icon: root.selection.length === root.count && root.count > 0 ? "deselect" : "select_all"
                tooltip: root.selection.length === root.count && root.count > 0 ? I18n.t("dialog.pages.select_none") : I18n.t("dialog.pages.select_all")
                disabled: root.count === 0
                onClicked: {
                    if (root.selection.length === root.count)
                        root.selection = [];
                    else
                        root.selectAll();
                }
            }
        }

        StyledText {
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacing.small
            anchors.verticalCenter: parent.verticalCenter
            text: root.selection.length > 0 ? I18n.tn("dialog.pages.selected", root.selection.length) : I18n.t("dialog.pages.hint")
            elide: Text.ElideRight
            width: Math.min(implicitWidth, parent.width / 2)
            horizontalAlignment: Text.AlignRight
            font.pointSize: Theme.font.size.small
            color: Theme.palette.m3onSurfaceVariant
        }
    }

    // ------------------------------------------------------------------ grid
    StyledRect {
        id: gridFrame

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: toolbar.visible ? toolbar.bottom : parent.top
        anchors.topMargin: toolbar.visible ? Theme.spacing.small : 0
        anchors.bottom: optionsBox.visible ? optionsBox.top : parent.bottom
        anchors.bottomMargin: optionsBox.visible ? Theme.spacing.medium : 0
        radius: Theme.rounding.large
        color: Theme.palette.m3surfaceContainerLow

        // Click on empty space clears the selection.
        MouseArea {
            anchors.fill: parent
            enabled: root.ready
            onClicked: {
                root.selection = [];
                grid.forceActiveFocus();
            }
        }

        GridView {
            id: grid

            readonly property int columns: Math.max(2, Math.floor(width / Theme.s(150)))

            anchors.fill: parent
            anchors.margins: Theme.padding.medium
            clip: true
            visible: root.ready
            model: pageModel
            cellWidth: Math.floor(width / columns)
            cellHeight: Math.round(cellWidth * 1.25) + Theme.s(26)
            boundsBehavior: Flickable.StopAtBounds
            interactive: !priv.dragging
            cacheBuffer: Math.round(cellHeight * 2)
            activeFocusOnTab: true
            keyNavigationEnabled: false

            Keys.onPressed: event => {
                const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
                const alt = (event.modifiers & Qt.AltModifier) !== 0;
                let handled = true;
                switch (event.key) {
                case Qt.Key_Left:
                    alt ? root.moveSelected(-1) : root.moveFocus(-1);
                    break;
                case Qt.Key_Right:
                    alt ? root.moveSelected(1) : root.moveFocus(1);
                    break;
                case Qt.Key_Up:
                    alt ? root.moveSelected(-grid.columns) : root.moveFocus(-grid.columns);
                    break;
                case Qt.Key_Down:
                    alt ? root.moveSelected(grid.columns) : root.moveFocus(grid.columns);
                    break;
                case Qt.Key_A:
                    if (ctrl)
                        root.selectAll();
                    else
                        handled = false;
                    break;
                case Qt.Key_D:
                    if (ctrl)
                        root.duplicatePages(root.selection.slice());
                    else
                        handled = false;
                    break;
                case Qt.Key_Delete:
                case Qt.Key_Backspace:
                    root.deletePages(root.selection.slice());
                    break;
                case Qt.Key_R:
                    if (ctrl || alt)
                        handled = false;
                    else
                        root.rotatePages(root.selection.slice(), (event.modifiers & Qt.ShiftModifier) ? -90 : 90);
                    break;
                case Qt.Key_L:
                    if (ctrl || alt)
                        handled = false;
                    else
                        root.rotatePages(root.selection.slice(), -90);
                    break;
                default:
                    handled = false;
                }
                event.accepted = handled;
            }

            // Clicks on the empty space between / after the pages clear the selection (the GridView itself takes the
            // press, so the frame's MouseArea never sees it). Below the delegates (they are created with z 1).
            MouseArea {
                y: grid.originY
                z: -1
                width: grid.width
                height: Math.max(grid.height, grid.contentHeight)
                enabled: root.ready && !priv.dragging
                onClicked: {
                    root.selection = [];
                    grid.forceActiveFocus();
                }
            }

            move: Transition {
                Anim {
                    properties: "x,y"
                    type: Anim.FastSpatial
                }
            }
            moveDisplaced: Transition {
                Anim {
                    properties: "x,y"
                    type: Anim.FastSpatial
                }
            }
            displaced: Transition {
                Anim {
                    properties: "x,y"
                    type: Anim.FastSpatial
                }
            }

            delegate: Item {
                id: cell

                required property int index
                required property int uid
                required property int src
                required property int rotate
                required property int spin
                required property real pw
                required property real ph

                readonly property bool isSelected: root.isSelected(cell.uid)
                readonly property bool isDragged: priv.dragUid === cell.uid
                readonly property var thumb: cell.src >= 0 ? (root.thumbs[String(cell.src)] ?? null) : null
                readonly property real side: width - Theme.s(16)

                width: grid.cellWidth
                height: grid.cellHeight

                Component.onCompleted: root.requestThumb(cell.src)

                HoverHandler {
                    id: hover
                }

                StyledRect {
                    id: card

                    anchors.fill: parent
                    anchors.margins: Theme.s(4)
                    radius: Theme.rounding.medium
                    color: cell.isSelected ? Theme.palette.m3secondaryContainer : (hover.hovered ? Theme.palette.m3surfaceContainerHigh : "transparent")
                    opacity: cell.isDragged ? 0.35 : 1
                    border.width: cell.isSelected ? Math.max(2, Math.round(2 * Theme.scale)) : 0
                    border.color: Theme.palette.m3primary
                }

                // Page (rotated as it will be saved).
                Item {
                    id: pageBox

                    x: Theme.s(8)
                    y: Theme.s(8)
                    width: cell.side
                    height: cell.side
                    opacity: cell.isDragged ? 0.35 : 1

                    PageFace {
                        anchors.centerIn: parent
                        src: cell.src
                        pw: cell.pw
                        ph: cell.ph
                        side: cell.side
                        spin: cell.spin
                        thumb: cell.thumb
                    }
                }

                StyledText {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: pageBox.bottom
                    anchors.topMargin: Theme.s(4)
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    text: {
                        const pos = I18n.formatCount(cell.index + 1);
                        if (cell.src < 0)
                            return pos + " · " + I18n.t("dialog.pages.blank");
                        if (cell.src !== cell.index)
                            return pos + " · " + I18n.t("dialog.pages.from", {
                                page: I18n.formatCount(cell.src + 1)
                            });
                        return pos;
                    }
                    font.pointSize: Theme.font.size.small
                    font.weight: cell.isSelected ? Font.Medium : Font.Normal
                    color: cell.isSelected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurfaceVariant
                }

                MouseArea {
                    id: cellMouse

                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    preventStealing: true
                    cursorShape: priv.dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor

                    onPressed: mouse => {
                        grid.forceActiveFocus();
                        priv.pressUid = cell.uid;
                        priv.pressPos = cellMouse.mapToItem(grid, mouse.x, mouse.y);
                    }
                    onPositionChanged: mouse => {
                        if (!cellMouse.pressed || priv.pressUid !== cell.uid)
                            return;
                        const p = cellMouse.mapToItem(grid, mouse.x, mouse.y);
                        if (!priv.dragging) {
                            if (Math.abs(p.x - priv.pressPos.x) + Math.abs(p.y - priv.pressPos.y) < Theme.s(8))
                                return;
                            priv.startDrag(cell.uid);
                        }
                        priv.dragTo(p);
                    }
                    onReleased: mouse => {
                        if (priv.dragging)
                            priv.endDrag();
                        else if (priv.pressUid === cell.uid)
                            root.select(cell.uid, mouse.modifiers);
                        priv.pressUid = -1;
                    }
                    onCanceled: {
                        priv.endDrag();
                        priv.pressUid = -1;
                    }
                    onDoubleClicked: root.rotatePages([cell.uid], 90)
                }

                // Per-page actions (hover).
                Row {
                    anchors.horizontalCenter: pageBox.horizontalCenter
                    anchors.bottom: pageBox.bottom
                    anchors.bottomMargin: Theme.s(4)
                    spacing: Theme.s(2)
                    visible: (hover.hovered || cell.isSelected) && !priv.dragging && cell.side > Theme.s(90)

                    Repeater {
                        model: [
                            {
                                icon: "rotate_left",
                                action: "left"
                            },
                            {
                                icon: "rotate_right",
                                action: "right"
                            },
                            {
                                icon: "content_copy",
                                action: "dup"
                            },
                            {
                                icon: "delete",
                                action: "del"
                            }
                        ]

                        IconButton {
                            id: pageAction

                            required property var modelData

                            type: ButtonBase.Filled
                            isRound: true
                            danger: pageAction.modelData.action === "del"
                            icon: String(pageAction.modelData.icon)
                            iconSize: Theme.font.iconSize.small
                            inactiveColour: pageAction.danger ? Theme.palette.m3errorContainer : Theme.palette.m3inverseSurface
                            inactiveOnColour: pageAction.danger ? Theme.palette.m3onErrorContainer : Theme.palette.m3inverseOnSurface
                            tooltip: {
                                switch (pageAction.modelData.action) {
                                case "left":
                                    return I18n.t("dialog.pages.rotate_left");
                                case "right":
                                    return I18n.t("dialog.pages.rotate_right");
                                case "dup":
                                    return I18n.t("dialog.pages.duplicate");
                                default:
                                    return I18n.t("dialog.pages.delete");
                                }
                            }
                            onClicked: {
                                const t = root.targets(cell.uid);
                                switch (pageAction.modelData.action) {
                                case "left":
                                    root.rotatePages(t, -90);
                                    break;
                                case "right":
                                    root.rotatePages(t, 90);
                                    break;
                                case "dup":
                                    root.duplicatePages(t);
                                    break;
                                default:
                                    root.deletePages(t);
                                }
                            }
                        }
                    }
                }
            }
        }

        // Ghost of the dragged page.
        Item {
            id: ghost

            readonly property int row: priv.dragging ? root.indexOfUid(priv.dragUid) : -1
            readonly property var page: root.rowAt(ghost.row, root.revision)
            readonly property real side: Math.max(Theme.s(40), grid.cellWidth - Theme.s(32))

            x: grid.x + priv.dragPos.x - width / 2
            y: grid.y + priv.dragPos.y - height / 2
            width: ghost.side
            height: ghost.side
            visible: priv.dragging && ghost.page !== null
            z: 10

            Elevation {
                anchors.centerIn: parent
                width: ghostFace.paintedWidth
                height: ghostFace.paintedHeight
                level: 3
                radius: Theme.rounding.extraSmall
            }

            PageFace {
                id: ghostFace

                anchors.centerIn: parent
                src: ghost.page?.src ?? -1
                pw: ghost.page?.pw ?? 595
                ph: ghost.page?.ph ?? 842
                side: ghost.side
                spin: ghost.page?.spin ?? 0
                thumb: (ghost.page?.src ?? -1) >= 0 ? (root.thumbs[String(ghost.page.src)] ?? null) : null
            }
        }

        // Auto-scroll while dragging near the top / bottom edge.
        Timer {
            interval: 30
            repeat: true
            running: priv.dragging
            onTriggered: {
                const edge = Theme.s(48);
                const y = priv.dragPos.y;
                let dy = 0;
                if (y < edge)
                    dy = -Math.ceil((edge - y) / 3);
                else if (y > grid.height - edge)
                    dy = Math.ceil((y - (grid.height - edge)) / 3);
                if (dy === 0)
                    return;
                const maxY = Math.max(0, grid.contentHeight - grid.height);
                const ny = DU.clamp(grid.contentY + dy, 0, maxY);
                if (ny !== grid.contentY) {
                    grid.contentY = ny;
                    priv.dragTo(priv.dragPos);
                }
            }
        }

        StyledText {
            anchors.centerIn: parent
            visible: root.ready && root.count === 0
            text: I18n.t("dialog.pages.none_left")
            font.pointSize: Theme.font.size.normal
            color: Theme.palette.m3onSurfaceVariant
        }

        PreviewState {
            anchors.fill: parent
            loading: root.info?.status === "loading" && !priv.unlocking
            error: root.info?.status === "error" ? (String(root.info?.error ?? "") || I18n.t("dialog.preview.failed")) : ""
            icon: "picture_as_pdf"
            onRetry: priv.load("")
        }

        PasswordPrompt {
            id: passwordPrompt

            anchors.fill: parent
            visible: root.locked || priv.unlocking
            wrong: priv.wrongPassword
            busy: priv.unlocking
            onSubmitted: password => root.unlock(password)
            onVisibleChanged: {
                if (passwordPrompt.visible)
                    Qt.callLater(() => passwordPrompt.focusField());
            }
        }
    }

    // ------------------------------------------------------------------ options
    Item {
        id: optionsBox

        anchors.left: parent.left
        anchors.bottom: parent.bottom
        width: Math.min(parent.width, Theme.s(520))
        height: options.implicitHeight
        visible: options.hasFields && root.ready

        ParamForm {
            id: options

            anchors.fill: parent
            node: root.node
            files: root.files
            host: root.host
            embedded: true
            focus: false
            exclude: root.optionExcludes(["pageops", "password", "per_file"])
        }
    }

    // A page drawn at its saved rotation: thumbnail (or a white sheet for blank pages) fitted in a side × side box.
    component PageFace: Item {
        id: face

        property int src: -1
        property real pw: 595
        property real ph: 842
        property real side: 100
        property int spin: 0
        property var thumb: null
        readonly property real aspect: face.pw > 0 && face.ph > 0 ? face.pw / face.ph : 0.707
        readonly property real paintedWidth: (Math.abs(face.spin) % 180 === 90) ? sheet.height : sheet.width
        readonly property real paintedHeight: (Math.abs(face.spin) % 180 === 90) ? sheet.width : sheet.height

        width: face.side
        height: face.side

        Rectangle {
            id: sheet

            anchors.centerIn: parent
            width: face.aspect >= 1 ? face.side : Math.round(face.side * face.aspect)
            height: face.aspect >= 1 ? Math.round(face.side / face.aspect) : face.side
            color: "white"
            radius: Math.round(2 * Theme.scale)
            antialiasing: true
            rotation: face.spin
            border.width: Math.max(1, Math.round(Theme.scale))
            border.color: Qt.rgba(0, 0, 0, 0.12)

            Behavior on rotation {
                Anim {
                    type: Anim.DefaultSpatial
                }
            }

            Image {
                anchors.fill: parent
                anchors.margins: Math.max(1, Math.round(Theme.scale))
                visible: face.src >= 0
                source: face.src >= 0 && face.thumb?.status === "ready" ? Backend.fileUrl(String(face.thumb?.png ?? "")) : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                smooth: true
                mipmap: true
            }

            CircularProgress {
                anchors.centerIn: parent
                width: Theme.s(24)
                height: Theme.s(24)
                visible: face.src >= 0 && (face.thumb === null || face.thumb?.status === "loading")
                indeterminate: true
                strokeWidth: Math.max(2, Math.round(3 * Theme.scale))
            }

            MaterialIcon {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: 1
                visible: face.src < 0 || face.thumb?.status === "error"
                text: face.src < 0 ? "note_add" : "broken_image"
                size: Theme.font.iconSize.large
                color: Qt.rgba(0, 0, 0, 0.25)
            }
        }
    }

    component ToolButton: IconButton {
        type: ButtonBase.Text
        iconSize: Theme.font.iconSize.medium
    }
}
