pragma ComponentBehavior: Bound

// M3Dropdown — M3 exposed dropdown menu (outlined field + popup list). The popup is drawn inside the same window:
// it is re-parented to the window's root item on open (keep it inside your window's input mask), closes on outside
// click, Escape or Tab, and is fully keyboard navigable (↑ ↓ Home End PageUp PageDown, Enter/Space to pick,
// type-ahead by first letters). It is a FocusScope with activeFocusOnTab.
//
//   property var options       [{value, label, icon?, description?}] | [[value, label]] | [plain values]
//                              (Param JSON options from the backend work as-is)
//   property var value         selected value (compared by value, numbers/strings loosely)
//   property string label      floating label
//   property string placeholderText   shown when nothing is selected (default I18n "Select…")
//   property string supportingText, errorText, leadingIcon
//   property bool disabled
//   property int maxVisibleItems  default 6 (then the list scrolls)
//   readonly property var model          normalised options [{value, label, icon, description}]
//   readonly property int currentIndex   index of `value` in model (-1 when none)
//   readonly property string currentText
//   readonly property bool open
//   signal activated(var value, int index)   user picked an option (`value` is already updated)
//   function openPopup(), closePopup(), select(index)

import QtQuick
import "../services"

FocusScope {
    id: root

    property var options: []
    property var value: undefined
    property string label: ""
    property string placeholderText: I18n.t("ui.select")
    property string supportingText: ""
    property string errorText: ""
    property string leadingIcon: ""
    property bool disabled: false
    property int maxVisibleItems: 6

    readonly property var model: {
        const src = root.options;
        const out = [];
        const n = src?.length ?? 0;
        for (let i = 0; i < n; i++) {
            const o = src[i];
            if (o === undefined || o === null)
                continue;
            if (Array.isArray(o)) {
                out.push({
                    value: o[0],
                    label: String(o.length > 1 ? o[1] : o[0]),
                    icon: "",
                    description: ""
                });
            } else if (typeof o === "object") {
                const v = o.value !== undefined ? o.value : o.id;
                out.push({
                    value: v,
                    label: String(o.label ?? o.text ?? v ?? ""),
                    icon: String(o.icon ?? ""),
                    description: String(o.description ?? o.help ?? "")
                });
            } else {
                out.push({
                    value: o,
                    label: String(o),
                    icon: "",
                    description: ""
                });
            }
        }
        return out;
    }
    readonly property int currentIndex: {
        const m = root.model;
        for (let i = 0; i < m.length; i++) {
            if (priv.same(m[i].value, root.value))
                return i;
        }
        return -1;
    }
    readonly property string currentText: root.currentIndex >= 0 ? root.model[root.currentIndex].label : ""
    readonly property bool open: priv.open

    signal activated(var value, int index)

    function openPopup(): void {
        if (root.disabled || priv.open || root.model.length === 0)
            return;
        const overlay = priv.overlayRoot();
        if (!overlay)
            return;
        if (popup.parent !== overlay)
            popup.parent = overlay;
        priv.highlight = root.currentIndex >= 0 ? root.currentIndex : 0;
        priv.place();
        priv.open = true;
        root.forceActiveFocus();
        list.positionViewAtIndex(priv.highlight, ListView.Contain);
    }

    function closePopup(): void {
        priv.open = false;
    }

    function select(index: int): void {
        if (index < 0 || index >= root.model.length)
            return;
        root.value = root.model[index].value;
        priv.open = false;
        root.activated(root.value, index);
    }

    enabled: !disabled
    activeFocusOnTab: true
    implicitWidth: Math.round(240 * Theme.scale)
    implicitHeight: frame.implicitHeight

    onActiveFocusChanged: {
        if (!activeFocus)
            priv.open = false;
    }

    Keys.onPressed: event => {
        const count = root.model.length;
        if (!priv.open) {
            if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Down || event.key === Qt.Key_Up || event.key === Qt.Key_F4) {
                root.openPopup();
                event.accepted = true;
            }
            return;
        }
        switch (event.key) {
        case Qt.Key_Escape:
            root.closePopup();
            event.accepted = true;
            return;
        case Qt.Key_Tab:
        case Qt.Key_Backtab:
            root.closePopup();
            return; // let the focus move on
        case Qt.Key_Up:
            priv.moveHighlight(priv.highlight - 1);
            break;
        case Qt.Key_Down:
            priv.moveHighlight(priv.highlight + 1);
            break;
        case Qt.Key_Home:
            priv.moveHighlight(0);
            break;
        case Qt.Key_End:
            priv.moveHighlight(count - 1);
            break;
        case Qt.Key_PageUp:
            priv.moveHighlight(priv.highlight - root.maxVisibleItems);
            break;
        case Qt.Key_PageDown:
            priv.moveHighlight(priv.highlight + root.maxVisibleItems);
            break;
        case Qt.Key_Return:
        case Qt.Key_Enter:
        case Qt.Key_Space:
            root.select(priv.highlight);
            break;
        default:
            if (event.text && event.text.length === 1 && event.text.trim() !== "" && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) {
                priv.typeAhead(event.text);
                break;
            }
            return;
        }
        event.accepted = true;
    }

    QtObject {
        id: priv

        property bool open: false
        property int highlight: -1
        property string typed: ""
        property bool above: false

        function same(a: var, b: var): bool {
            if (a === b)
                return true;
            if (a === undefined || a === null || b === undefined || b === null)
                return false;
            if (typeof a === "object" || typeof b === "object") {
                try {
                    return JSON.stringify(a) === JSON.stringify(b);
                } catch (e) {
                    return false;
                }
            }
            return String(a) === String(b);
        }

        function overlayRoot(): Item {
            let p = root.parent;
            while (p && p.parent)
                p = p.parent;
            return p;
        }

        function moveHighlight(i: int): void {
            const count = root.model.length;
            if (count === 0)
                return;
            highlight = Math.max(0, Math.min(count - 1, i));
            list.positionViewAtIndex(highlight, ListView.Contain);
        }

        function typeAhead(ch: string): void {
            typed += ch.toLowerCase();
            typeAheadReset.restart();
            const m = root.model;
            const start = typed.length === 1 ? highlight + 1 : highlight;
            for (let k = 0; k < m.length; k++) {
                const i = (start + k + m.length) % m.length;
                if (m[i].label.toLowerCase().startsWith(typed)) {
                    moveHighlight(i);
                    return;
                }
            }
        }

        function place(): void {
            const overlay = popup.parent;
            if (!overlay)
                return;
            const gap = Theme.spacing.extraSmall;
            const margin = Theme.spacing.small;
            const tl = frame.box.mapToItem(overlay, 0, 0);
            const h = card.implicitHeight;
            const below = overlay.height - (tl.y + frame.box.height + gap) - margin;
            const aboveSpace = tl.y - gap - margin;
            above = below < h && aboveSpace > below;
            card.width = Math.min(Math.max(frame.box.width, Math.round(160 * Theme.scale)), overlay.width - margin * 2);
            card.x = Math.round(Math.max(margin, Math.min(overlay.width - card.width - margin, tl.x)));
            const avail = above ? aboveSpace : below;
            card.height = Math.max(Math.min(h, avail), Math.min(h, priv.itemHeight + Theme.padding.extraSmall * 2));
            card.y = Math.round(above ? tl.y - gap - card.height : tl.y + frame.box.height + gap);
        }

        readonly property real itemHeight: Math.round(40 * Theme.scale)
    }

    Timer {
        id: typeAheadReset

        interval: 700
        onTriggered: priv.typed = ""
    }

    FieldFrame {
        id: frame

        anchors.left: parent.left
        anchors.right: parent.right
        label: root.label
        focused: root.activeFocus || priv.open
        populated: root.currentIndex >= 0 || root.label === ""
        hovered: fieldArea.containsMouse
        error: root.errorText !== ""
        disabled: root.disabled
        supportingText: root.supportingText
        errorText: root.errorText
        labelLeft: root.leadingIcon !== "" ? leading.width + Theme.padding.medium + Theme.spacing.small : Theme.padding.large
    }

    MouseArea {
        id: fieldArea

        x: frame.box.x
        y: frame.box.y
        width: frame.box.width
        height: frame.box.height
        hoverEnabled: true
        cursorShape: root.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
        onClicked: {
            root.forceActiveFocus();
            if (priv.open)
                root.closePopup();
            else
                root.openPopup();
        }
    }

    MaterialIcon {
        id: leading

        x: Theme.padding.medium
        y: Math.round((frame.box.height - height) / 2) + 1
        visible: root.leadingIcon !== ""
        text: root.leadingIcon
        size: Theme.font.iconSize.medium
        color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3onSurfaceVariant
    }

    MaterialIcon {
        id: currentIcon

        x: frame.labelLeft
        y: Math.round((frame.box.height - height) / 2) + 1
        visible: root.currentIndex >= 0 && root.model[root.currentIndex].icon !== ""
        text: visible ? root.model[root.currentIndex].icon : ""
        size: Theme.font.iconSize.small
        color: Theme.palette.m3onSurfaceVariant
    }

    StyledText {
        id: valueText

        x: frame.labelLeft + (currentIcon.visible ? currentIcon.width + Theme.spacing.small : 0)
        width: Math.max(0, frame.box.width - x - arrow.width - Theme.padding.medium * 2)
        y: Math.round((frame.box.height - height) / 2)
        text: root.currentIndex >= 0 ? root.currentText : (root.label === "" || priv.open || root.activeFocus ? root.placeholderText : "")
        elide: Text.ElideRight
        font.pointSize: Theme.font.size.normal
        color: {
            if (root.disabled)
                return Qt.alpha(Theme.palette.m3onSurface, 0.38);
            return root.currentIndex >= 0 ? Theme.palette.m3onSurface : Qt.alpha(Theme.palette.m3onSurfaceVariant, 0.8);
        }
    }

    MaterialIcon {
        id: arrow

        anchors.right: parent.right
        anchors.rightMargin: Theme.padding.medium
        y: Math.round((frame.box.height - height) / 2) + 1
        text: "arrow_drop_down"
        size: Theme.font.iconSize.large
        rotation: priv.open ? 180 : 0
        color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : (priv.open ? Theme.palette.m3primary : Theme.palette.m3onSurfaceVariant)

        Behavior on rotation {
            Anim {
                type: Anim.DefaultEffects
            }
        }
    }

    // Popup layer: re-parented to the window root while open.
    Item {
        id: popup

        width: parent ? parent.width : 0
        height: parent ? parent.height : 0
        z: 100000
        visible: priv.open || card.opacity > 0

        MouseArea {
            anchors.fill: parent
            enabled: priv.open
            acceptedButtons: Qt.AllButtons
            onPressed: event => {
                root.closePopup();
                event.accepted = true;
            }
            onWheel: event => event.accepted = true
        }

        Elevation {
            x: card.x
            y: card.y
            width: card.width
            height: card.height
            radius: card.radius
            level: 2
            opacity: card.opacity
        }

        StyledRect {
            id: card

            readonly property real implicitListHeight: Math.min(root.model.length, Math.max(1, root.maxVisibleItems)) * priv.itemHeight

            implicitHeight: implicitListHeight + Theme.padding.extraSmall * 2
            radius: Theme.rounding.medium
            color: Theme.palette.m3surfaceContainer
            opacity: priv.open ? 1 : 0
            clip: true
            transform: Scale {
                origin.y: priv.above ? card.height : 0
                yScale: priv.open ? 1 : 0.85
                Behavior on yScale {
                    Anim {
                        type: Anim.FastSpatial
                    }
                }
            }

            Behavior on opacity {
                Anim {
                    type: Anim.FastEffects
                }
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.AllButtons
                hoverEnabled: true
                onWheel: event => event.accepted = false
            }

            ListView {
                id: list

                anchors.fill: parent
                anchors.margins: Theme.padding.extraSmall
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: root.model
                currentIndex: priv.highlight
                highlightFollowsCurrentItem: false
                interactive: contentHeight > height

                delegate: Item {
                    id: option

                    required property var modelData
                    required property int index
                    readonly property bool selected: option.index === root.currentIndex
                    readonly property bool highlighted: option.index === priv.highlight

                    width: ListView.view.width
                    height: priv.itemHeight

                    StyledRect {
                        id: optionBg

                        anchors.fill: parent
                        radius: Theme.rounding.small
                        color: option.selected ? Theme.palette.m3secondaryContainer : "transparent"

                        StateLayer {
                            color: option.selected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurface
                            manualHoverOverride: option.highlighted
                            onEntered: priv.highlight = option.index
                            onClicked: root.select(option.index)
                        }
                    }

                    Row {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.leftMargin: Theme.padding.medium
                        anchors.rightMargin: Theme.padding.medium
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacing.medium

                        MaterialIcon {
                            id: optionIcon

                            anchors.verticalCenter: parent.verticalCenter
                            anchors.verticalCenterOffset: 1
                            visible: option.modelData.icon !== ""
                            text: option.modelData.icon
                            size: Theme.font.iconSize.small
                            color: option.selected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurfaceVariant
                        }

                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            width: Math.max(0, parent.width - (optionIcon.visible ? optionIcon.width + parent.spacing : 0) - (checkIcon.visible ? checkIcon.width + parent.spacing : 0))

                            StyledText {
                                width: parent.width
                                text: option.modelData.label
                                elide: Text.ElideRight
                                font.pointSize: Theme.font.size.normal
                                color: option.selected ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurface
                            }

                            StyledText {
                                width: parent.width
                                visible: text !== ""
                                text: option.modelData.description
                                elide: Text.ElideRight
                                font.pointSize: Theme.font.size.small
                                color: option.selected ? Qt.alpha(Theme.palette.m3onSecondaryContainer, 0.8) : Theme.palette.m3onSurfaceVariant
                            }
                        }

                        MaterialIcon {
                            id: checkIcon

                            anchors.verticalCenter: parent.verticalCenter
                            anchors.verticalCenterOffset: 1
                            visible: option.selected
                            text: "check"
                            size: Theme.font.iconSize.small
                            color: Theme.palette.m3onSecondaryContainer
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: popup.parent
        enabled: priv.open

        function onWidthChanged(): void {
            priv.place();
        }

        function onHeightChanged(): void {
            priv.place();
        }
    }
}
