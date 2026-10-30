pragma ComponentBehavior: Bound

// Wheel — the visual Dropwheel and its navigation model (SPEC §2.3). WheelWindow hosts it and feeds it pointer
// positions (DropArea in drag mode, MouseArea in click mode) and key events; it never talks to the backend itself.
//
// Layout (all sizes × the wheel scale): the disc (backdrop radius 140 = outer 132 + 8) is centred in the item; up to 8
// Segments between radius 52 and 132, segment 0 centred at 12 o'clock, clockwise; CenterHub (radius 48) in the
// middle; the breadcrumb chip of the open sub-ring above the disc; page dots and the label pill below it. The item's
// implicit size leaves room on both sides of the disc for the page dots and the tallest pill, so the window that
// hosts it can centre the disc on the cursor. Near a screen edge part of the item can be off the screen
// (visibleArea): the labels then stay inside the visible part, flipping to the other side of the disc if needed.
//
// INPUTS
//   property var menu            Menu JSON (SPEC §5.3) or null; a new menu resets the navigation
//   property string mode         "drag" | "click"   (spring-loading only in drag mode)
//   property bool loading        a menu request is pending (spinner in the hub)
//   property string errorText    non-empty → error state (hub error icon, message in the pill)
//   property string errorDetails extra line under the error message
//   property bool empty          click mode without files: the hub is a drop target ("Trascina qui dei file")
//   property bool dropActive     a drag is over the disc (empty target highlights, pill says "release")
//   property int springMs        spring-loading delay for groups / Back (config wheel.spring_ms, default 450)
//   property real maxScale       upper limit for the wheel scale (Theme.scale), so the disc fits the screen
//   property rect visibleArea    the part of the item that is on the screen, in item coordinates (default: all of it)
// STATE (read-only unless noted)
//   items                        nodes of the ring currently shown (front ring)
//   stack                        entered groups (sub-rings), [] at the top level
//   pageIndex                    index into `pages` (pages with at least one item)
//   highlight                    highlighted segment index or -1;  centreHot: the hub is hovered
//   centreShown                  the hub shows Back / Cancel (centreHot, and for Cancel the pointer has left the hub
//                                at least once since open(): the wheel opens centred on the pointer)
//   labelsBelow                  the dots and the pill are below the disc (false: flipped above it, no room below)
//   hoverNode                    node under the highlight or null
//   disc                         Item covering the disc (use it for the window mask: RegionShape.Ellipse)
//   pillItem, crumbItem, dotsItem   the label items, for layout checks
//   centerX, centerY, innerRadius, outerRadius, hubRadius, discRadius, edgeMargin (kept from the screen edges),
//   fitUnits (screen side / fitUnits = the largest scale whose disc still fits)
//   closing                      the close animation is running
// SIGNALS
//   activated(var node, bool options)   an action was chosen (drop, click, Enter, 1–8); options = Alt / right-click:
//                                       node is then a copy with dialog "form" (open the parameter form)
//   cancelled()                  drop / click on the hub at the top level (also on the empty drop target), Esc at
//                                the top level
//   clickModeRequested()         keep the wheel open in click mode (drop on a group, on an unavailable action,
//                                or while the menu is still loading)
//   retryRequested()             error state: click on the hub / Enter
//   closeFinished()              close() animation finished
// FUNCTIONS
//   hitTest(x, y) → {zone: "centre" | "segment" | "ring" | "outside", index, radius, angle}   (item coordinates)
//   pointerMoved(x, y)  pointerLeft()  dropAt(x, y)  clickAt(x, y, button)  scrollBy(angleDelta)
//   moveHighlight(delta)  activateIndex(i, options)  activateHighlighted(options)  enterGroup(node, select)
//   back() → bool        switchPage(dir)   resetNavigation()   showTransient(text)
//   handleKeyPress(event) / handleKeyRelease(event) → bool (click-mode keyboard, SPEC §2.3)
//   open()               new session: resets navigation / pointer state, open animation (scale 0.45 → 1 + fade,
//                        expressiveFastSpatial)
//   close(toHud, dx, dy) close animation; toHud shrinks towards (dx, dy) (direction of the HUD) — emits closeFinished

import QtQuick
import QtQuick.Shapes
import "../services"
import "../components"
import "WheelText.js" as WT

Item {
    id: root

    // ------------------------------------------------------------------ inputs
    property var menu: null
    property string mode: "drag"
    property bool loading: false
    property string errorText: ""
    property string errorDetails: ""
    property bool empty: false
    property bool dropActive: false
    property int springMs: 450
    property real maxScale: 1000
    property rect visibleArea: Qt.rect(0, 0, root.width, root.height)

    // ------------------------------------------------------------------ geometry
    readonly property real k: Math.min(Theme.scale, root.maxScale)
    // Shrink factor for what Segment / CenterHub size from the Theme tokens (1 unless maxScale caps the scale).
    readonly property real fit: Theme.scale > 0 ? root.k / Theme.scale : 1
    readonly property real innerRadius: 52 * k
    readonly property real outerRadius: 132 * k
    readonly property real hubRadius: 48 * k
    readonly property real discRadius: outerRadius + 8 * k
    readonly property real segmentGap: 4 * k
    readonly property real edgeMargin: 4 * k
    readonly property real fitUnits: 2 * (132 + 8 + 4)
    // Wide enough for an install command on two lines of the pill.
    readonly property real sideMargin: 56 * k
    readonly property real dotsBand: Theme.spacing.small + Math.round(6 * k) + Theme.spacing.small
    // The tallest pill: padding, title, 3 detail lines + the click hint, 2 command lines, the Column spacing.
    readonly property real pillMaxHeight: 2 * Theme.padding.small + Math.ceil(titleMetrics.lineSpacing) + 4 * Math.ceil(smallMetrics.lineSpacing) + 2 * Math.ceil(monoMetrics.lineSpacing) + 3 * Math.round(2 * k)
    // On each side: dots, the tallest pill and, when the other side is off the screen, the breadcrumb past the pill.
    readonly property real verticalMargin: dotsBand + pillMaxHeight + crumb.band + Theme.spacing.extraSmall
    readonly property real centerX: width / 2
    readonly property real centerY: height / 2
    readonly property Item disc: hitDisc
    readonly property Item pillItem: pill
    readonly property Item crumbItem: crumb
    readonly property Item dotsItem: dots

    // The on-screen part of the item and the room it leaves on each side of the disc.
    readonly property real areaLeft: Math.max(0, root.visibleArea.x)
    readonly property real areaTop: Math.max(0, root.visibleArea.y)
    readonly property real areaRight: Math.min(root.width, root.visibleArea.x + root.visibleArea.width)
    readonly property real areaBottom: Math.min(root.height, root.visibleArea.y + root.visibleArea.height)
    readonly property real roomAbove: root.centerY - root.discRadius - root.areaTop
    readonly property real roomBelow: root.areaBottom - root.centerY - root.discRadius
    // Decided on the worst case, not on the current pill: the pill must not jump side while hovering.
    readonly property bool labelsBelow: root.roomBelow >= root.verticalMargin || root.roomBelow >= root.roomAbove

    implicitWidth: 2 * Math.ceil(discRadius + sideMargin)
    implicitHeight: 2 * Math.ceil(discRadius + verticalMargin)

    // ------------------------------------------------------------------ navigation state
    property int pageIndex: 0
    property var stack: []
    property int highlight: -1
    property bool centreHot: false
    // The wheel opens centred on the pointer: the hub only turns into "Cancel" once the pointer has been somewhere
    // else first (Back inside a sub-ring is always shown).
    property bool centreArmed: false
    property bool keyboardNav: false
    property int flashIndex: -1
    property real springProgress: 0
    property bool springArmed: true
    property real lastX: NaN
    property real lastY: NaN
    property real armX: 0
    property real armY: 0
    property bool altHeld: false
    property bool shiftTap: false
    property string transientText: ""
    property string pendingHighlightId: ""
    property real wheelAccumulator: 0
    readonly property bool closing: priv.closing

    // Two ring layers cross-fade on every ring change (sub-ring, back, page, new menu).
    property var ringA: []
    property var ringB: []
    property bool frontA: true
    property real morphT: 1
    property int morphDir: 0

    readonly property var pages: root.pagesOf(root.menu)
    readonly property var page: root.pages.length > 0 ? root.pages[Math.min(root.pageIndex, root.pages.length - 1)] : null
    readonly property var items: root.frontA ? root.ringA : root.ringB
    readonly property int count: root.items.length
    readonly property bool inSubRing: root.stack.length > 0
    readonly property bool ready: root.menu !== null && !root.loading && root.errorText === "" && !root.empty
    readonly property var hoverNode: root.highlight >= 0 && root.highlight < root.count ? root.items[root.highlight] : null
    readonly property bool hoverAvailable: root.hoverNode !== null && root.hoverNode.available !== false
    readonly property bool hoverDanger: root.hoverNode !== null && root.hoverNode.danger === true
    readonly property bool centreShown: root.centreHot && (root.inSubRing || root.centreArmed)

    signal activated(var node, bool options)
    signal cancelled
    signal clickModeRequested
    signal retryRequested
    signal closeFinished

    // ------------------------------------------------------------------ pill / breadcrumb texts
    readonly property string fileSummary: {
        const m = root.menu;
        if (!m)
            return "";
        const title = String(m.title ?? "");
        const sub = String(m.subtitle ?? "");
        if (title !== "" && sub !== "")
            return title + " · " + sub;
        return title !== "" ? title : sub;
    }

    readonly property string pillTitle: {
        if (root.transientText !== "")
            return root.transientText;
        if (root.loading)
            return I18n.t("ui.loading");
        if (root.errorText !== "")
            return root.errorText;
        if (root.empty && root.dropActive)
            return WT.tr(I18n, "wheel.release_to_open");
        if (root.centreShown)
            return root.inSubRing ? I18n.t("ui.back") : WT.tr(I18n, "wheel.cancel");
        if (root.empty)
            return I18n.t("ui.drop_here");
        if (root.hoverNode !== null)
            return String(root.hoverNode.label ?? root.hoverNode.id ?? "");
        if (root.menu !== null)
            return root.count === 0 ? WT.tr(I18n, "wheel.no_actions") : root.fileSummary;
        return root.mode === "drag" ? WT.tr(I18n, "wheel.drop_on_wheel") : "";
    }

    readonly property string pillDetail: {
        if (root.transientText !== "")
            return "";
        if (root.loading)
            return "";
        if (root.errorText !== "") {
            const d = root.errorDetails.split("\n").filter(l => l.trim() !== "");
            const first = d.length > 0 ? d[d.length - 1].trim() : "";
            return root.mode === "click" ? (first !== "" ? first + "\n" : "") + WT.tr(I18n, "wheel.retry_hint") : first;
        }
        const n = root.hoverNode;
        if (root.centreShown)
            return "";
        if (root.empty)
            return root.dropActive ? "" : WT.tr(I18n, "wheel.empty_hint");
        if (n === null)
            return root.ready && root.pages.length > 1 && !root.inSubRing && root.count > 0 ? WT.tr(I18n, "wheel.page_hint") : "";
        if (n.available === false)
            return String(n.hint ?? "") !== "" ? String(n.hint) : I18n.t("ui.unavailable");
        const desc = String(n.description ?? "");
        if (desc !== "" && desc !== String(n.label ?? ""))
            return desc;
        if (root.mode === "click" && n.type === "action" && n.mode === "quick" && (n.params?.length ?? 0) > 0)
            return WT.tr(I18n, "wheel.options_hint");
        return "";
    }

    readonly property string pillCode: {
        const n = root.hoverNode;
        if (root.transientText !== "" || n === null || root.centreShown || n.available !== false)
            return "";
        return String(n.install ?? "");
    }

    readonly property string breadcrumb: root.stack.map(g => String(g?.label ?? "")).filter(s => s !== "").join("  ›  ")

    // ------------------------------------------------------------------ model helpers
    function pagesOf(m: var): var {
        const p = m?.pages;
        if (!Array.isArray(p))
            return [];
        return p.filter(pg => pg && Array.isArray(pg.items) && pg.items.length > 0);
    }

    function computeItems(): var {
        const g = root.stack.length > 0 ? root.stack[root.stack.length - 1] : null;
        let src;
        if (g) {
            src = g.items;
        } else {
            const ps = root.pagesOf(root.menu);
            src = ps.length > 0 ? ps[Math.min(root.pageIndex, ps.length - 1)]?.items : [];
        }
        return Array.isArray(src) ? src.filter(n => n && typeof n === "object").slice(0, 8) : [];
    }

    function refreshRing(dir: int): void {
        const next = root.computeItems();
        root.morphDir = dir;
        if (root.frontA)
            root.ringB = next;
        else
            root.ringA = next;
        root.frontA = !root.frontA;
        root.highlight = -1;
        root.centreHot = false;
        root.flashIndex = -1;
        root.stopSpring();
        morphAnim.restart();

        if (root.pendingHighlightId !== "") {
            const wanted = root.pendingHighlightId;
            root.pendingHighlightId = "";
            for (let i = 0; i < next.length; i++) {
                if (next[i]?.id === wanted) {
                    root.highlight = i;
                    root.keyboardNav = true;
                    return;
                }
            }
        }
        if (isFinite(root.lastX) && isFinite(root.lastY)) {
            // Re-evaluate what is under the (unmoved) pointer, without arming the spring until it moves.
            root.springArmed = false;
            root.armX = root.lastX;
            root.armY = root.lastY;
            const h = root.hitTest(root.lastX, root.lastY);
            root.highlight = h.zone === "segment" ? h.index : -1;
            root.centreHot = h.zone === "centre";
        }
    }

    function resetNavigation(): void {
        root.pageIndex = 0;
        root.stack = [];
        root.pendingHighlightId = "";
        root.transientText = "";
        root.keyboardNav = false;
        root.altHeld = false;
        root.shiftTap = false;
        root.wheelAccumulator = 0;
        transientTimer.stop();
        root.refreshRing(0);
    }

    // ------------------------------------------------------------------ hit testing
    function hitTest(x: real, y: real): var {
        const dx = x - root.centerX;
        const dy = y - root.centerY;
        const r = Math.sqrt(dx * dx + dy * dy);
        const angle = Math.atan2(dy, dx) * 180 / Math.PI;
        if (!isFinite(r))
            return {
                zone: "outside",
                index: -1,
                radius: r,
                angle: 0
            };
        if (r < root.innerRadius)
            return {
                zone: "centre",
                index: -1,
                radius: r,
                angle: angle
            };
        if (r > root.discRadius + 2 * root.k)
            return {
                zone: "outside",
                index: -1,
                radius: r,
                angle: angle
            };
        const n = root.count;
        if (n <= 0)
            return {
                zone: "ring",
                index: -1,
                radius: r,
                angle: angle
            };
        const step = 360 / n;
        let rel = (angle + 90) % 360;
        if (rel < 0)
            rel += 360;
        const index = Math.floor((rel + step / 2) / step) % n;
        return {
            zone: "segment",
            index: index,
            radius: r,
            angle: angle
        };
    }

    // ------------------------------------------------------------------ pointer
    function pointerMoved(x: real, y: real): void {
        if (priv.closing)
            return;
        root.lastX = x;
        root.lastY = y;
        let rearm = false;
        if (!root.springArmed && Math.abs(x - root.armX) + Math.abs(y - root.armY) > 6 * root.k) {
            root.springArmed = true;
            rearm = true;
        }
        const h = root.hitTest(x, y);
        const nextHighlight = h.zone === "segment" ? h.index : -1;
        const nextCentre = h.zone === "centre";
        if (!nextCentre)
            root.centreArmed = true;
        if (nextHighlight !== root.highlight || nextCentre !== root.centreHot) {
            root.keyboardNav = false;
            root.highlight = nextHighlight;
            root.centreHot = nextCentre;
            root.restartSpring();
        } else if (rearm && !springAnim.running) {
            root.restartSpring();
        }
    }

    function pointerLeft(): void {
        root.lastX = NaN;
        root.lastY = NaN;
        root.centreHot = false;
        if (!root.keyboardNav)
            root.highlight = -1;
        root.stopSpring();
    }

    function dropAt(x: real, y: real): void {
        if (priv.closing)
            return;
        root.stopSpring();
        if (root.empty || root.loading || root.errorText !== "" || root.menu === null) {
            // Not ready yet: keep the wheel (now holding the dropped files) open in click mode.
            root.clickModeRequested();
            return;
        }
        const h = root.hitTest(x, y);
        if (h.zone === "segment" && h.index >= 0) {
            const node = root.items[h.index];
            root.highlight = h.index;
            if (node?.type === "group") {
                root.enterGroup(node, true);
                root.clickModeRequested();
                return;
            }
            if (node?.available === false) {
                root.flash(h.index);
                root.clickModeRequested();
                return;
            }
            if (node)
                root.activated(node, false);
            return;
        }
        root.cancelled();
    }

    function clickAt(x: real, y: real, button: int): void {
        if (priv.closing)
            return;
        const h = root.hitTest(x, y);
        if (root.errorText !== "") {
            if (h.zone === "centre" || button === Qt.LeftButton)
                root.retryRequested();
            return;
        }
        if (root.loading)
            return;
        if (root.empty) {
            // A click elsewhere does not close the drop target (the user goes and finds the files): this one does.
            if (h.zone === "centre")
                root.cancelled();
            return;
        }
        if (h.zone === "centre") {
            if (!root.back())
                root.cancelled();
            return;
        }
        if (h.zone === "segment") {
            root.highlight = h.index;
            root.activateIndex(h.index, button === Qt.RightButton);
        }
    }

    function scrollBy(delta: real): void {
        if (!root.ready || root.count === 0)
            return;
        root.wheelAccumulator += delta;
        while (Math.abs(root.wheelAccumulator) >= 120) {
            const step = root.wheelAccumulator > 0 ? -1 : 1;
            root.wheelAccumulator -= root.wheelAccumulator > 0 ? 120 : -120;
            root.moveHighlight(step);
        }
    }

    // ------------------------------------------------------------------ spring loading (drag mode)
    function springTargetValid(): bool {
        if (root.mode !== "drag" || !root.springArmed || !root.ready)
            return false;
        if (root.centreHot)
            return root.inSubRing;
        const n = root.hoverNode;
        return n !== null && n.type === "group" && Array.isArray(n.items) && n.items.length > 0;
    }

    function restartSpring(): void {
        springAnim.stop();
        root.springProgress = 0;
        if (root.springTargetValid())
            springAnim.start();
    }

    function stopSpring(): void {
        springAnim.stop();
        root.springProgress = 0;
    }

    function springFired(): void {
        if (!root.springTargetValid())
            return;
        if (root.centreHot) {
            root.back();
            return;
        }
        const n = root.hoverNode;
        if (n !== null && n.type === "group")
            root.enterGroup(n, false);
    }

    // ------------------------------------------------------------------ navigation
    function enterGroup(node: var, select: bool): void {
        if (!node || node.type !== "group" || !Array.isArray(node.items) || node.items.length === 0)
            return;
        const def = (typeof node.default === "string") ? node.default : "";
        root.pendingHighlightId = select ? (def !== "" ? def : String(node.items[0]?.id ?? "")) : "";
        root.stack = root.stack.concat([node]);
        root.refreshRing(1);
    }

    function back(): bool {
        if (root.stack.length === 0)
            return false;
        const left = root.stack[root.stack.length - 1];
        root.pendingHighlightId = root.keyboardNav || root.mode === "click" ? String(left?.id ?? "") : "";
        root.stack = root.stack.slice(0, root.stack.length - 1);
        root.refreshRing(-1);
        return true;
    }

    function switchPage(dir: int): void {
        const n = root.pages.length;
        if (n < 2 || priv.closing)
            return;
        const d = dir < 0 ? -1 : 1;
        root.pendingHighlightId = "";
        root.stack = [];
        root.pageIndex = ((root.pageIndex + d) % n + n) % n;
        root.refreshRing(d);
        const label = String(root.pages[root.pageIndex]?.label ?? "");
        if (label !== "")
            root.showTransient(label);
    }

    function moveHighlight(delta: int): void {
        const n = root.count;
        if (n === 0 || !root.ready)
            return;
        root.keyboardNav = true;
        root.centreHot = false;
        if (root.highlight < 0)
            root.highlight = delta > 0 ? 0 : n - 1;
        else
            root.highlight = ((root.highlight + delta) % n + n) % n;
        root.stopSpring();
    }

    function selectIndex(i: int): void {
        if (root.count === 0 || !root.ready)
            return;
        root.keyboardNav = true;
        root.centreHot = false;
        root.highlight = Math.max(0, Math.min(root.count - 1, i));
        root.stopSpring();
    }

    function activateHighlighted(options: bool): void {
        if (root.errorText !== "") {
            root.retryRequested();
            return;
        }
        if (!root.ready)
            return;
        if (root.centreShown) {
            if (!root.back())
                root.cancelled();
            return;
        }
        if (root.highlight < 0) {
            // Nothing selected yet: Enter selects the first segment (or the sub-ring default).
            const g = root.stack.length > 0 ? root.stack[root.stack.length - 1] : null;
            const def = typeof g?.default === "string" ? g.default : "";
            let i = 0;
            for (let j = 0; j < root.count; j++) {
                if (def !== "" && root.items[j]?.id === def) {
                    i = j;
                    break;
                }
            }
            root.selectIndex(i);
            return;
        }
        root.activateIndex(root.highlight, options);
    }

    function activateIndex(i: int, options: bool): void {
        if (!root.ready || i < 0 || i >= root.count)
            return;
        const node = root.items[i];
        if (!node)
            return;
        root.highlight = i;
        if (node.type === "group") {
            root.enterGroup(node, true);
            return;
        }
        if (node.available === false) {
            root.flash(i);
            root.copyInstall(node);
            return;
        }
        if (options) {
            const nParams = Array.isArray(node.params) ? node.params.length : 0;
            if (node.mode === "dialog") {
                root.activated(node, false);
            } else if (nParams > 0) {
                root.activated(Object.assign({}, node, {
                    dialog: "form"
                }), true);
            } else {
                root.flash(-1);
                root.showTransient(I18n.t("ui.no_options"));
            }
            return;
        }
        root.activated(node, false);
    }

    function flash(i: int): void {
        root.flashIndex = i;
        flashTimer.restart();
    }

    function showTransient(text: string): void {
        root.transientText = text;
        transientTimer.restart();
    }

    // Keep a label of the given size inside the on-screen part of the item.
    function clampX(x: real, w: real): real {
        return Math.max(root.areaLeft + root.edgeMargin, Math.min(root.areaRight - root.edgeMargin - w, x));
    }

    function clampY(y: real, h: real): real {
        return Math.max(root.areaTop + root.edgeMargin, Math.min(root.areaBottom - root.edgeMargin - h, y));
    }

    function copyInstall(node: var): void {
        const cmd = String(node?.install ?? "");
        if (cmd === "")
            return;
        Backend.util("copy_text", {
            text: cmd
        }, (result, error) => {
            root.showTransient(error ? WT.tr(I18n, "wheel.install_copy_failed") : WT.tr(I18n, "wheel.install_copied"));
        });
    }

    // ------------------------------------------------------------------ keyboard (click mode)
    function handleKeyPress(event: var): bool {
        if (priv.closing)
            return true;
        const key = event.key;
        const mods = event.modifiers;
        const alt = (mods & Qt.AltModifier) !== 0;
        if (key === Qt.Key_Shift) {
            if (!event.isAutoRepeat)
                root.shiftTap = true;
            return true;
        }
        root.shiftTap = false;
        if (key === Qt.Key_Alt || key === Qt.Key_AltGr) {
            root.altHeld = true;
            return true;
        }
        if (key === Qt.Key_Escape) {
            if (!root.back())
                root.cancelled();
            return true;
        }
        if (root.errorText !== "") {
            if (key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space)
                root.retryRequested();
            return true;
        }
        if (!root.ready)
            return true;
        if ((mods & Qt.ControlModifier) !== 0 && (key === Qt.Key_Tab || key === Qt.Key_Backtab)) {
            root.switchPage(key === Qt.Key_Backtab ? -1 : 1);
            return true;
        }
        switch (key) {
        case Qt.Key_Right:
        case Qt.Key_Down:
        case Qt.Key_Tab:
            root.moveHighlight(1);
            return true;
        case Qt.Key_Left:
        case Qt.Key_Up:
        case Qt.Key_Backtab:
            root.moveHighlight(-1);
            return true;
        case Qt.Key_Home:
            root.selectIndex(0);
            return true;
        case Qt.Key_End:
            root.selectIndex(root.count - 1);
            return true;
        case Qt.Key_PageDown:
            root.switchPage(1);
            return true;
        case Qt.Key_PageUp:
            root.switchPage(-1);
            return true;
        case Qt.Key_Backspace:
        case Qt.Key_0:
            root.back();
            return true;
        case Qt.Key_Return:
        case Qt.Key_Enter:
        case Qt.Key_Space:
            root.activateHighlighted(alt);
            return true;
        case Qt.Key_Menu:
            if (root.highlight >= 0)
                root.activateIndex(root.highlight, true);
            return true;
        }
        if (key >= Qt.Key_1 && key <= Qt.Key_8) {
            const i = key - Qt.Key_1;
            if (i < root.count) {
                root.keyboardNav = true;
                root.activateIndex(i, alt);
            }
            return true;
        }
        return false;
    }

    function handleKeyRelease(event: var): bool {
        const key = event.key;
        if (key === Qt.Key_Shift) {
            const tap = root.shiftTap && !event.isAutoRepeat;
            root.shiftTap = false;
            if (tap && root.ready)
                root.switchPage(1);
            return true;
        }
        if (key === Qt.Key_Alt || key === Qt.Key_AltGr) {
            root.altHeld = false;
            return true;
        }
        return false;
    }

    // ------------------------------------------------------------------ open / close
    function open(): void {
        priv.closing = false;
        closeAnim.stop();
        shift.x = 0;
        shift.y = 0;
        // A new session: forget the pointer, sub-ring and page of the previous one (the menu may be a cached object,
        // in which case onMenuChanged does not fire) and show the ring at once, without the morph.
        root.lastX = NaN;
        root.lastY = NaN;
        root.highlight = -1;
        root.centreHot = false;
        root.centreArmed = false;
        root.springArmed = true;
        root.resetNavigation();
        morphAnim.stop();
        root.morphT = 1;
        root.morphDir = 0;
        openAnim.restart();
    }

    function close(toHud: bool, dx: real, dy: real): void {
        if (priv.closing)
            return;
        priv.closing = true;
        root.stopSpring();
        openAnim.stop();
        const len = Math.sqrt(dx * dx + dy * dy);
        const travel = toHud && len > 0 ? Math.min(len, 90 * root.k) : 0;
        closeAnim.toScale = toHud ? 0.22 : 0.82;
        closeAnim.toX = travel > 0 ? dx / len * travel : 0;
        closeAnim.toY = travel > 0 ? dy / len * travel : 0;
        closeAnim.duration = toHud ? Theme.anim.durations.expressiveFastSpatial : Theme.anim.durations.expressiveDefaultEffects;
        closeAnim.restart();
    }

    onMenuChanged: root.resetNavigation()
    onModeChanged: root.stopSpring()
    Component.onCompleted: {
        if (root.items.length === 0 && root.menu !== null)
            root.resetNavigation();
    }

    Accessible.role: Accessible.PopupMenu
    Accessible.name: WT.tr(I18n, "wheel.a11y.name")

    Keys.onPressed: event => {
        if (root.handleKeyPress(event))
            event.accepted = true;
    }
    Keys.onReleased: event => {
        if (root.handleKeyRelease(event))
            event.accepted = true;
    }

    QtObject {
        id: priv

        property bool closing: false
    }

    FontMetrics {
        id: titleMetrics

        font.family: Theme.font.family.sans
        font.pointSize: Theme.font.size.normal
        font.weight: Font.Medium
    }

    FontMetrics {
        id: smallMetrics

        font.family: Theme.font.family.sans
        font.pointSize: Theme.font.size.small
    }

    FontMetrics {
        id: monoMetrics

        font.family: Theme.font.family.mono
        font.pointSize: Theme.font.size.small
    }

    // ------------------------------------------------------------------ animations / timers
    NumberAnimation {
        id: springAnim

        target: root
        property: "springProgress"
        from: 0
        to: 1
        duration: Math.max(100, root.springMs)
        easing.type: Easing.Linear
        onFinished: root.springFired()
    }

    Anim {
        id: morphAnim

        target: root
        property: "morphT"
        from: 0
        to: 1
        type: Anim.SlowEffects
    }

    Timer {
        id: flashTimer

        interval: 650
        onTriggered: root.flashIndex = -1
    }

    Timer {
        id: transientTimer

        interval: 1600
        onTriggered: root.transientText = ""
    }

    ParallelAnimation {
        id: openAnim

        Anim {
            target: content
            property: "scale"
            from: 0.45
            to: 1
            type: Anim.FastSpatial
        }
        Anim {
            target: content
            property: "opacity"
            from: 0
            to: 1
            type: Anim.FastSpatial
        }
    }

    SequentialAnimation {
        id: closeAnim

        property real toScale: 0.82
        property real toX: 0
        property real toY: 0
        property int duration: 200

        ParallelAnimation {
            NumberAnimation {
                target: content
                property: "scale"
                to: closeAnim.toScale
                duration: closeAnim.duration
                easing.type: Easing.BezierSpline
                easing.bezierCurve: Theme.anim.curves.emphasizedAccel
            }
            NumberAnimation {
                target: content
                property: "opacity"
                to: 0
                duration: closeAnim.duration
                easing.type: Easing.BezierSpline
                easing.bezierCurve: Theme.anim.curves.standardAccel
            }
            NumberAnimation {
                target: shift
                property: "x"
                to: closeAnim.toX
                duration: closeAnim.duration
                easing.type: Easing.BezierSpline
                easing.bezierCurve: Theme.anim.curves.emphasizedAccel
            }
            NumberAnimation {
                target: shift
                property: "y"
                to: closeAnim.toY
                duration: closeAnim.duration
                easing.type: Easing.BezierSpline
                easing.bezierCurve: Theme.anim.curves.emphasizedAccel
            }
        }
        ScriptAction {
            script: root.closeFinished()
        }
    }

    // Input/mask geometry: the full disc, never scaled by the animations.
    Item {
        id: hitDisc

        x: root.centerX - root.discRadius
        y: root.centerY - root.discRadius
        width: root.discRadius * 2
        height: root.discRadius * 2
    }

    // ------------------------------------------------------------------ visuals
    Item {
        id: content

        anchors.fill: parent
        transformOrigin: Item.Center
        opacity: 0
        scale: 0.45
        transform: Translate {
            id: shift
        }

        Elevation {
            x: backdrop.x
            y: backdrop.y
            width: backdrop.width
            height: backdrop.height
            radius: backdrop.radius
            level: 3
        }

        StyledRect {
            id: backdrop

            x: root.centerX - root.discRadius
            y: root.centerY - root.discRadius
            width: root.discRadius * 2
            height: root.discRadius * 2
            radius: root.discRadius
            color: Theme.tPalette.m3surface
            border.width: Math.max(1, Math.round(root.k))
            border.color: Qt.alpha(Theme.palette.m3outlineVariant, 0.35)
        }

        // Skeleton ring while there are no segments (loading, error, empty drop target, no actions).
        Shape {
            anchors.fill: parent
            opacity: root.count === 0 ? 1 : 0
            visible: opacity > 0
            preferredRendererType: Shape.CurveRenderer

            ShapePath {
                fillColor: Qt.alpha(Theme.palette.m3surfaceContainerHigh, root.empty && root.dropActive ? 0.9 : 0.5)
                strokeColor: root.empty ? Qt.alpha(Theme.palette.m3primary, root.dropActive ? 0.8 : 0.35) : "transparent"
                strokeWidth: root.empty ? Math.max(1, 1.5 * root.k) : 0
                strokeStyle: ShapePath.DashLine
                dashPattern: [4, 3]
                fillRule: ShapePath.OddEvenFill

                PathAngleArc {
                    centerX: root.centerX
                    centerY: root.centerY
                    radiusX: root.outerRadius
                    radiusY: root.outerRadius
                    startAngle: 0
                    sweepAngle: 360
                }
                PathAngleArc {
                    centerX: root.centerX
                    centerY: root.centerY
                    radiusX: root.innerRadius
                    radiusY: root.innerRadius
                    startAngle: 0
                    sweepAngle: 360
                }
            }

            Behavior on opacity {
                Anim {
                    type: Anim.DefaultEffects
                }
            }
        }

        Repeater {
            model: 2

            delegate: Item {
                id: ringLayer

                required property int index
                readonly property bool front: (ringLayer.index === 0) === root.frontA
                readonly property var nodes: ringLayer.index === 0 ? root.ringA : root.ringB

                anchors.fill: parent
                z: ringLayer.front ? 1 : 0
                transformOrigin: Item.Center
                opacity: ringLayer.front ? root.morphT : 1 - root.morphT
                rotation: ringLayer.front ? -12 * root.morphDir * (1 - root.morphT) : 12 * root.morphDir * root.morphT
                scale: ringLayer.front ? 0.94 + 0.06 * root.morphT : 1 + 0.04 * root.morphT
                visible: ringLayer.opacity > 0.001 && ringLayer.nodes.length > 0

                Repeater {
                    model: ringLayer.nodes

                    delegate: Segment {
                        required property var modelData

                        node: modelData
                        anchors.fill: parent
                        count: ringLayer.nodes.length
                        innerRadius: root.innerRadius
                        outerRadius: root.outerRadius
                        gap: root.segmentGap
                        cornerRadius: 10 * root.k
                        growBy: 4 * root.k
                        fit: root.fit
                        highlighted: ringLayer.front && root.highlight === index && !root.centreHot
                        flash: ringLayer.front && root.flashIndex === index
                        springProgress: ringLayer.front && root.highlight === index && !root.centreHot ? root.springProgress : 0
                        showNumber: ringLayer.front && root.mode === "click" && root.altHeld
                    }
                }
            }
        }

        CenterHub {
            x: root.centerX - width / 2
            y: root.centerY - height / 2
            z: 2
            radius: root.hubRadius
            fit: root.fit
            mode: {
                if (root.loading)
                    return "loading";
                if (root.errorText !== "")
                    return "error";
                if (root.empty && root.centreShown && !root.dropActive)
                    return "cancel";
                if (root.empty || root.menu === null)
                    return "empty";
                if (root.inSubRing)
                    return "back";
                return root.centreShown ? "cancel" : "file";
            }
            highlighted: root.centreShown || (root.empty && root.dropActive)
            springProgress: root.centreHot && root.inSubRing ? root.springProgress : 0
            dropActive: root.dropActive
            thumb: String(root.menu?.thumb ?? "")
            category: String(root.menu?.category ?? "")
            icon: String(root.menu?.icon ?? "")
            ext: (root.menu?.count ?? 0) === 1 ? String(root.menu?.files?.[0]?.ext ?? "") : ""
            count: Math.max(1, Number(root.menu?.count ?? 1) || 1)
        }

        // Breadcrumb of the open sub-ring, above the disc. Without room there it goes on the free side of the disc, or
        // past the pill when the pill took the only side there is.
        StyledRect {
            id: crumb

            readonly property real maxW: root.areaRight - root.areaLeft - 2 * root.edgeMargin
            readonly property real band: height + Theme.spacing.small
            readonly property bool besideDisc: root.labelsBelow ? root.roomAbove >= crumb.band : root.roomBelow >= crumb.band

            visible: opacity > 0
            opacity: root.inSubRing && root.breadcrumb !== "" ? 1 : 0
            z: 3
            x: root.clampX(root.centerX - width / 2, width)
            y: {
                let raw;
                if (crumb.besideDisc)
                    raw = root.labelsBelow ? root.centerY - root.discRadius - crumb.band : root.centerY + root.discRadius + Theme.spacing.small;
                else
                    raw = root.labelsBelow ? pill.y + pill.height + Theme.spacing.small : pill.y - crumb.band;
                return root.clampY(raw, height);
            }
            width: Math.min(maxW, crumbRow.implicitWidth + Theme.padding.medium * 2)
            height: crumbRow.implicitHeight + Theme.padding.extraSmall * 2
            radius: height / 2
            color: Theme.palette.m3secondaryContainer

            Row {
                id: crumbRow

                anchors.centerIn: parent
                spacing: Theme.spacing.extraSmall

                MaterialIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: 1
                    text: String(root.stack.length > 0 ? (root.stack[root.stack.length - 1]?.icon ?? "") : "") || "subdirectory_arrow_right"
                    color: Theme.palette.m3onSecondaryContainer
                    size: Theme.font.iconSize.small
                    fill: 1
                }

                StyledText {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, crumb.maxW - Theme.padding.medium * 2 - Theme.font.iconSize.small * 1.6)
                    text: root.breadcrumb
                    elide: Text.ElideLeft
                    color: Theme.palette.m3onSecondaryContainer
                    font.pointSize: Theme.font.size.small
                    font.weight: Font.DemiBold
                }
            }

            Behavior on opacity {
                Anim {
                    type: Anim.FastEffects
                }
            }
        }

        // Page dots (main / file), just below the disc (or just above it, with the pill).
        Row {
            id: dots

            visible: root.pages.length > 1 && root.ready
            x: root.centerX - width / 2
            y: root.labelsBelow ? root.centerY + root.discRadius + Theme.spacing.small : root.centerY - root.discRadius - Theme.spacing.small - Math.round(6 * root.k)
            spacing: Math.round(5 * root.k)

            Repeater {
                model: root.pages.length

                delegate: StyledRect {
                    required property int index

                    readonly property bool current: index === root.pageIndex

                    anchors.verticalCenter: parent.verticalCenter
                    width: current ? Math.round(16 * root.k) : Math.round(6 * root.k)
                    height: Math.round(6 * root.k)
                    radius: height / 2
                    color: current ? Theme.palette.m3primary : Qt.alpha(Theme.palette.m3onSurface, 0.35)

                    Behavior on width {
                        Anim {
                            type: Anim.FastSpatial
                        }
                    }
                }
            }
        }

        // Label pill: hovered item, hint, install command — or the file name and size.
        StyledRect {
            id: pill

            readonly property real maxW: root.areaRight - root.areaLeft - 2 * root.edgeMargin
            readonly property real textMaxW: maxW - 2 * Theme.padding.large
            readonly property bool multiLine: detailText.visible || codeText.visible

            visible: opacity > 0
            opacity: root.pillTitle !== "" ? 1 : 0
            z: 3
            x: root.clampX(root.centerX - width / 2, width)
            y: root.clampY(root.labelsBelow ? root.centerY + root.discRadius + root.dotsBand : root.centerY - root.discRadius - root.dotsBand - height, height)
            width: Math.min(maxW, Math.max(Math.round(64 * root.k), pillColumn.implicitWidth + 2 * Theme.padding.large))
            height: pillColumn.implicitHeight + 2 * Theme.padding.small
            radius: multiLine ? Theme.rounding.large : height / 2
            color: {
                if (root.errorText !== "" && root.transientText === "")
                    return Theme.palette.m3errorContainer;
                if (root.hoverNode !== null && root.transientText === "" && !root.centreHot && (root.hoverDanger || !root.hoverAvailable))
                    return root.hoverDanger && root.hoverAvailable ? Theme.palette.m3errorContainer : Theme.palette.m3tertiaryContainer;
                return Theme.tPalette.m3surfaceContainer;
            }
            border.width: Math.max(1, Math.round(root.k))
            border.color: Qt.alpha(Theme.palette.m3outlineVariant, 0.35)

            readonly property color onColour: {
                if (root.errorText !== "" && root.transientText === "")
                    return Theme.palette.m3onErrorContainer;
                if (root.hoverNode !== null && root.transientText === "" && !root.centreHot && (root.hoverDanger || !root.hoverAvailable))
                    return root.hoverDanger && root.hoverAvailable ? Theme.palette.m3onErrorContainer : Theme.palette.m3onTertiaryContainer;
                return Theme.palette.m3onSurface;
            }

            Column {
                id: pillColumn

                anchors.centerIn: parent
                spacing: Math.round(2 * root.k)

                StyledText {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(implicitWidth, pill.textMaxW)
                    text: root.pillTitle
                    color: pill.onColour
                    elide: Text.ElideMiddle
                    horizontalAlignment: Text.AlignHCenter
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                StyledText {
                    id: detailText

                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: text !== ""
                    width: Math.min(implicitWidth, pill.textMaxW)
                    text: root.pillDetail
                    color: pill.onColour
                    opacity: 0.8
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    font.pointSize: Theme.font.size.small
                }

                StyledText {
                    id: codeText

                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: text !== ""
                    width: Math.min(implicitWidth, pill.textMaxW)
                    text: root.pillCode
                    color: pill.onColour
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.font.family.mono
                    font.pointSize: Theme.font.size.small
                    font.variableAxes: ({})
                }

                StyledText {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: codeText.visible && root.mode === "click"
                    width: Math.min(implicitWidth, pill.textMaxW)
                    text: WT.tr(I18n, "wheel.click_to_copy")
                    color: pill.onColour
                    opacity: 0.7
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    font.pointSize: Theme.font.size.small * 0.92
                }
            }

            Behavior on opacity {
                Anim {
                    type: Anim.FastEffects
                }
            }
            Behavior on width {
                enabled: pill.opacity > 0.99
                Anim {
                    type: Anim.FastSpatial
                }
            }
        }
    }

    // Click-mode pointer input (drag-mode positions come from the window's DropArea).
    MouseArea {
        id: mouse

        anchors.fill: parent
        enabled: root.mode === "click" && !priv.closing
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: {
            if (!mouse.containsMouse)
                return Qt.ArrowCursor;
            if (root.errorText !== "" || (root.centreHot && !root.loading) || root.highlight >= 0)
                return Qt.PointingHandCursor;
            return Qt.ArrowCursor;
        }

        onPositionChanged: event => root.pointerMoved(event.x, event.y)
        onExited: root.pointerLeft()
        onClicked: event => root.clickAt(event.x, event.y, event.button)
        onWheel: event => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.angleDelta.x;
            root.scrollBy(d);
            event.accepted = true;
        }
    }
}
