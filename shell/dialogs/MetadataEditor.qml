pragma ComponentBehavior: Bound

// MetadataEditor — dialog "metadata": the file's metadata groups (Backend.preview "metadata": exiftool -G1 groups, or
// Pillow / PyMuPDF / mutagen / ffprobe fallbacks) with a search field, inline editing of editable values (counted as
// you type; Enter confirms, Esc goes back to the value the field started from), ⊖ to remove a tag (click again to keep
// it), a "Remove all metadata" switch and a summary of what will change. Multi-file: filmstrip, each file keeps its own
// edits; the job runs only on the files with edits (targetFiles).
// Params (SPEC §5.6): metadata {set: {tag: value}, remove: [tags], remove_all: bool} (tags are the fully qualified
// ids from the preview, e.g. "IFD0:Artist"), per_file {path: {metadata}} when files differ, plus the simple params of
// the action (Options).

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    property var metas: ({})
    property var edits: []
    property string query: ""

    readonly property var meta: root.metas[root.currentFile?.path ?? ""] ?? null
    readonly property bool ready: root.meta?.status === "ready"
    readonly property var edit: root.edits[root.currentIndex] ?? priv.emptyEdit()
    readonly property var groups: Array.isArray(root.meta?.groups) ? root.meta.groups : []
    readonly property var originals: {
        const out = {};
        for (const g of root.groups) {
            for (const it of (Array.isArray(g?.items) ? g.items : []))
                out[root.itemId(g, it)] = {
                    group: String(g?.name ?? ""),
                    key: String(it?.key ?? ""),
                    value: String(it?.value ?? ""),
                    editable: it?.editable === true,
                    removable: (it?.removable ?? it?.editable) === true
                };
        }
        return out;
    }
    readonly property var rows: {
        const q = root.query.trim().toLowerCase();
        const out = [];
        for (const g of root.groups) {
            const name = String(g?.name ?? "");
            const items = (Array.isArray(g?.items) ? g.items : []).filter(it => {
                if (q === "")
                    return true;
                return String(it?.key ?? "").toLowerCase().indexOf(q) !== -1 || String(it?.value ?? "").toLowerCase().indexOf(q) !== -1 || name.toLowerCase().indexOf(q) !== -1;
            });
            if (items.length === 0)
                continue;
            out.push({
                type: "group",
                name: name,
                count: items.length,
                id: "group:" + name,
                key: "",
                value: "",
                editable: false,
                removable: false
            });
            for (const it of items)
                out.push({
                    type: "item",
                    name: name,
                    count: 0,
                    id: root.itemId(g, it),
                    key: String(it?.key ?? ""),
                    value: String(it?.value ?? ""),
                    editable: it?.editable === true,
                    removable: (it?.removable ?? it?.editable) === true
                });
        }
        return out;
    }
    readonly property int itemCount: root.groups.reduce((n, g) => n + (Array.isArray(g?.items) ? g.items.length : 0), 0)
    readonly property bool anyRemovable: Object.keys(root.originals).some(k => root.originals[k].removable)
    readonly property var changes: priv.changesOf(root.edit, root.originals)
    readonly property int changeCount: root.edits.reduce((n, e) => n + Object.keys(e?.set ?? {}).length + (e?.remove?.length ?? 0) + (e?.removeAll ? 1 : 0), 0)
    readonly property int editedFiles: root.edits.filter(e => priv.hasEdit(e)).length

    function itemId(g: var, it: var): string {
        const id = it?.id;
        if (typeof id === "string" && id !== "")
            return id;
        const gname = String(g?.name ?? "");
        return gname !== "" ? gname + ":" + String(it?.key ?? "") : String(it?.key ?? "");
    }

    function isRemoved(id: string): bool {
        const o = root.originals[id];
        if (root.edit.removeAll && o?.removable === true && root.edit.set[id] === undefined)
            return true;
        return root.edit.remove.indexOf(id) !== -1;
    }

    function currentValue(id: string, original: string): string {
        const v = root.edit.set[id];
        return v === undefined ? original : String(v);
    }

    function setEdit(next: var): void {
        const all = root.edits.slice();
        while (all.length < root.fileCount)
            all.push(priv.emptyEdit());
        all[root.currentIndex] = next;
        root.edits = all;
        root.dirty = root.changeCount > 0;
    }

    function setValue(id: string, value: string): void {
        priv.writeValue(id, value, false);
    }

    // setValue from the inline field while typing: one undo step per burst of typing in a field.
    function typeValue(id: string, value: string): void {
        priv.writeValue(id, value, true);
    }

    function toggleRemove(id: string): void {
        const o = root.originals[id];
        if (!o || !o.removable)
            return;
        root.pushHistory();
        const next = DU.copy(root.edit);
        if (root.isRemoved(id)) {
            next.remove = next.remove.filter(r => r !== id);
            if (next.removeAll) {
                // Keep this one while everything else goes: re-set it to its current value.
                next.set[id] = root.currentValue(id, o.value);
            }
        } else {
            delete next.set[id];
            if (!next.removeAll)
                next.remove.push(id);
        }
        root.setEdit(next);
    }

    function setRemoveAll(value: bool): void {
        if (value === root.edit.removeAll)
            return;
        root.pushHistory();
        const next = DU.copy(root.edit);
        next.removeAll = value;
        if (value) {
            next.remove = [];
            // Values the user changed stay (they are written after the wipe); untouched values go.
            for (const id of Object.keys(next.set)) {
                if (!(root.originals[id]?.editable))
                    delete next.set[id];
            }
        }
        root.setEdit(next);
    }

    function pushHistory(): void {
        const h = priv.history.slice(-49);
        h.push(DU.copy(root.edits));
        priv.history = h;
    }

    function undo(): bool {
        if (priv.history.length === 0)
            return false;
        const h = priv.history.slice();
        root.edits = h.pop();
        priv.history = h;
        root.dirty = root.changeCount > 0;
        return true;
    }

    function reset(): void {
        if (root.changeCount === 0)
            return;
        root.pushHistory();
        root.edits = root.fileList.map(() => priv.emptyEdit());
        root.dirty = false;
    }

    // Files without edits stay out of the job: for the backend they would be failures ("no metadata change").
    function targetFiles(): var {
        return root.fileList.filter((f, i) => priv.hasEdit(root.edits[i]));
    }

    function requestApply(): void {
        if (!root.canApply || root.busy)
            return;
        const extra = options.hasFields ? options.collectParams() : {};
        if (extra === null)
            return;
        const entries = [];
        for (let i = 0; i < root.fileCount; i++) {
            const e = root.edits[i];
            if (!priv.hasEdit(e))
                continue;
            entries.push({
                path: root.fileList[i].path,
                params: {
                    metadata: {
                        set: DU.copy(e.set),
                        remove: e.remove.slice(),
                        remove_all: e.removeAll === true
                    }
                }
            });
        }
        if (entries.length === 0)
            return;
        const out = DU.copy(entries[0].params);
        if (entries.length > 1 && entries.some(en => !DU.same(en.params, entries[0].params))) {
            const per = {};
            for (const en of entries)
                per[en.path] = en.params;
            out.per_file = per;
        }
        root.apply(Object.assign({}, extra, out));
    }

    applyLabel: I18n.t("dialog.metadata.apply")
    applyIcon: "save"
    canApply: root.changeCount > 0
    footerHint: {
        if (root.changeCount === 0)
            return root.ready ? I18n.t("dialog.metadata.no_changes") : "";
        const t = I18n.tn("dialog.metadata.changes", root.changeCount);
        if (root.editedFiles === root.fileCount)
            return t;
        return t + " · " + I18n.t("dialog.metadata.edited_files", {
            edited: root.editedFiles,
            total: root.fileCount
        });
    }
    implicitWidth: Theme.s(1000)
    implicitHeight: Theme.s(660)

    Component.onCompleted: {
        root.edits = root.fileList.map(() => priv.emptyEdit());
        priv.ensure(root.currentFile);
        Qt.callLater(() => searchField.focusInput());
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
        property string typingId: ""
        property double lastTyped: 0

        function emptyEdit(): var {
            return {
                set: ({}),
                remove: [],
                removeAll: false
            };
        }

        function hasEdit(e: var): bool {
            return !!e && (Object.keys(e.set ?? {}).length > 0 || (e.remove?.length ?? 0) > 0 || e.removeAll === true);
        }

        function writeValue(id: string, value: string, typing: bool): void {
            const o = root.originals[id];
            if (!o || !o.editable || root.currentValue(id, o.value) === value)
                return;
            const now = Date.now();
            if (!typing || priv.typingId !== id || now - priv.lastTyped > 800)
                root.pushHistory();
            priv.typingId = typing ? id : "";
            priv.lastTyped = now;
            const next = DU.copy(root.edit);
            // While everything is removed, an entry in `set` is also what keeps the tag: it stays at the original value.
            if (value === o.value && !next.removeAll)
                delete next.set[id];
            else
                next.set[id] = value;
            next.remove = next.remove.filter(r => r !== id);
            root.setEdit(next);
        }

        function setMeta(path: string, value: var): void {
            const next = Object.assign({}, root.metas);
            next[path] = value;
            root.metas = next;
        }

        function ensure(file: var): void {
            const path = file?.path ?? "";
            if (path === "")
                return;
            const m = root.metas[path];
            if (m && m.status !== "error")
                return;
            priv.setMeta(path, {
                status: "loading"
            });
            const life = priv.life;
            Backend.preview("metadata", {
                path: path
            }, (r, e) => {
                if (!life.alive)
                    return;
                if (e || !r) {
                    priv.setMeta(path, {
                        status: "error",
                        error: String(e?.message ?? I18n.t("dialog.preview.failed"))
                    });
                    return;
                }
                priv.setMeta(path, {
                    status: "ready",
                    groups: Array.isArray(r.groups) ? r.groups : [],
                    editable: r.editable === true,
                    source: String(r.source ?? ""),
                    install: String(r.install ?? ""),
                    warnings: Array.isArray(r.warnings) ? r.warnings : []
                });
            });
        }

        function changesOf(edit: var, originals: var): var {
            const out = [];
            if (edit?.removeAll)
                out.push({
                    kind: "all",
                    text: I18n.t("dialog.metadata.change_all")
                });
            for (const id of Object.keys(edit?.set ?? {})) {
                const o = originals[id];
                out.push({
                    kind: "set",
                    text: I18n.t("dialog.metadata.change_set", {
                        key: o?.key ?? id,
                        old: priv.short(o?.value ?? ""),
                        value: priv.short(String(edit.set[id]))
                    })
                });
            }
            for (const id of (edit?.remove ?? [])) {
                const o = originals[id];
                out.push({
                    kind: "remove",
                    text: I18n.t("dialog.metadata.change_remove", {
                        key: o?.key ?? id,
                        group: o?.group ?? ""
                    })
                });
            }
            return out;
        }

        function short(text: string): string {
            const t = String(text ?? "").replace(/\s+/g, " ");
            if (t === "")
                return "∅";
            return t.length > 40 ? t.slice(0, 39) + "…" : t;
        }
    }

    // ------------------------------------------------------------------ list
    Item {
        id: main

        anchors.left: parent.left
        anchors.right: side.left
        anchors.rightMargin: Theme.spacing.large
        anchors.top: parent.top
        anchors.bottom: strip.visible ? strip.top : parent.bottom
        anchors.bottomMargin: strip.visible ? Theme.spacing.medium : 0

        M3TextField {
            id: searchField

            anchors.left: parent.left
            anchors.right: parent.right
            leadingIcon: "search"
            label: I18n.t("dialog.metadata.search")
            clearButton: true
            text: root.query
            onTextChanged: root.query = searchField.text
            // Enter only filters (the list already follows the text): it must not save and close. Ctrl+Enter applies.
            Keys.onReturnPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
            Keys.onEnterPressed: event => event.accepted = (event.modifiers & Qt.ControlModifier) === 0
        }

        StyledRect {
            id: listFrame

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: searchField.bottom
            anchors.topMargin: Theme.spacing.medium
            anchors.bottom: parent.bottom
            radius: Theme.rounding.large
            color: Theme.palette.m3surfaceContainerLow

            ListView {
                id: list

                anchors.fill: parent
                anchors.margins: Theme.padding.small
                clip: true
                visible: root.ready
                model: root.rows
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: false
                activeFocusOnTab: true

                delegate: Item {
                    id: row

                    required property var modelData
                    required property int index
                    readonly property bool header: row.modelData.type === "group"
                    readonly property string itemId: String(row.modelData.id)
                    readonly property bool removed: !row.header && root.isRemoved(row.itemId)
                    readonly property bool changed: !row.header && root.edit.set[row.itemId] !== undefined
                    readonly property string value: row.header ? "" : root.currentValue(row.itemId, String(row.modelData.value))

                    width: ListView.view.width
                    height: {
                        if (row.header)
                            return Theme.s(38);
                        if (row.modelData.editable && !row.removed)
                            return Theme.s(36);
                        return Math.max(Theme.s(34), valueText.implicitHeight + Theme.s(12));
                    }

                    // Group header.
                    Row {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.padding.small
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: Theme.s(6)
                        spacing: Theme.spacing.small
                        visible: row.header

                        StyledText {
                            id: groupName

                            text: String(row.modelData.name)
                            font.pointSize: Theme.font.size.normal
                            font.weight: Font.DemiBold
                            color: Theme.palette.m3primary
                        }

                        StyledText {
                            anchors.baseline: groupName.baseline
                            text: String(row.modelData.count)
                            font.pointSize: Theme.font.size.small
                            color: Theme.palette.m3onSurfaceVariant
                        }
                    }

                    // Item row.
                    StyledRect {
                        anchors.fill: parent
                        visible: !row.header
                        radius: Theme.rounding.small
                        color: row.changed ? Qt.alpha(Theme.palette.m3primary, 0.10) : (rowHover.hovered ? Qt.alpha(Theme.palette.m3onSurface, 0.05) : "transparent")

                        HoverHandler {
                            id: rowHover
                        }

                        StyledText {
                            id: keyText

                            anchors.left: parent.left
                            anchors.leftMargin: Theme.padding.small
                            anchors.top: parent.top
                            anchors.topMargin: Theme.s(7)
                            width: Math.round(parent.width * 0.36)
                            text: String(row.modelData.key)
                            elide: Text.ElideRight
                            font.pointSize: Theme.font.size.small
                            font.strikeout: row.removed
                            color: row.removed ? Theme.palette.m3outline : Theme.palette.m3onSurfaceVariant

                            Tooltip {
                                text: row.itemId
                                delay: 900
                            }
                        }

                        // Read-only value (selectable for copying).
                        TextEdit {
                            id: valueText

                            anchors.left: keyText.right
                            anchors.leftMargin: Theme.spacing.medium
                            anchors.right: removeButton.left
                            anchors.rightMargin: Theme.spacing.small
                            anchors.top: parent.top
                            anchors.topMargin: Theme.s(6)
                            visible: !(row.modelData.editable && !row.removed)
                            readOnly: true
                            selectByMouse: true
                            text: row.value
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText
                            font.family: Theme.font.family.sans
                            font.pointSize: Theme.font.size.small
                            font.strikeout: row.removed
                            color: row.removed ? Theme.palette.m3outline : Theme.palette.m3onSurface
                            selectionColor: Theme.palette.m3primary
                            selectedTextColor: Theme.palette.m3onPrimary
                        }

                        // Inline editor for editable values.
                        StyledRect {
                            id: editBox

                            anchors.left: keyText.right
                            anchors.leftMargin: Theme.spacing.medium - Theme.s(6)
                            anchors.right: removeButton.left
                            anchors.rightMargin: Theme.spacing.small
                            anchors.verticalCenter: parent.verticalCenter
                            height: Theme.s(28)
                            visible: row.modelData.editable && !row.removed
                            radius: Theme.rounding.extraSmall
                            color: input.activeFocus ? Theme.palette.m3surfaceContainerHighest : "transparent"
                            border.width: Math.max(1, Math.round(Theme.scale))
                            border.color: input.activeFocus ? Theme.palette.m3primary : (rowHover.hovered ? Theme.palette.m3outlineVariant : "transparent")

                            TextInput {
                                id: input

                                // The value when the field got the focus: Esc goes back to it.
                                property string entryValue: ""

                                anchors.fill: parent
                                anchors.leftMargin: Theme.s(6)
                                anchors.rightMargin: Theme.s(6)
                                verticalAlignment: TextInput.AlignVCenter
                                clip: true
                                selectByMouse: true
                                text: row.value
                                font.family: Theme.font.family.sans
                                font.pointSize: Theme.font.size.small
                                color: Theme.palette.m3onSurface
                                selectionColor: Theme.palette.m3primary
                                selectedTextColor: Theme.palette.m3onPrimary

                                function commit(): void {
                                    root.setValue(row.itemId, input.text);
                                    // Follow the model again (undo, reset, file switch), whatever typing did.
                                    input.text = Qt.binding(() => row.value);
                                }

                                onActiveFocusChanged: {
                                    if (input.activeFocus)
                                        input.entryValue = row.value;
                                }
                                // Counted while typing: Apply must enable as soon as there is a change, and a row that
                                // scrolls out of the list (delegate destroyed) must not lose its text.
                                onTextEdited: root.typeValue(row.itemId, input.text)
                                onEditingFinished: input.commit()
                                Keys.onReturnPressed: event => {
                                    input.commit();
                                    list.forceActiveFocus();
                                    event.accepted = true;
                                }
                                Keys.onEnterPressed: event => {
                                    input.commit();
                                    list.forceActiveFocus();
                                    event.accepted = true;
                                }
                                Keys.onEscapePressed: event => {
                                    if (input.text !== input.entryValue) {
                                        input.text = input.entryValue;
                                        input.commit();
                                        event.accepted = true;
                                    } else {
                                        event.accepted = false;
                                    }
                                }
                            }

                            MaterialIcon {
                                anchors.right: parent.right
                                anchors.rightMargin: Theme.s(4)
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.verticalCenterOffset: 1
                                visible: !input.activeFocus && rowHover.hovered
                                text: "edit"
                                size: Theme.font.iconSize.small
                                color: Theme.palette.m3onSurfaceVariant
                            }
                        }

                        IconButton {
                            id: removeButton

                            anchors.right: parent.right
                            anchors.rightMargin: Theme.spacing.extraSmall
                            anchors.verticalCenter: parent.verticalCenter
                            type: ButtonBase.Text
                            visible: !row.header
                            disabled: !row.modelData.removable
                            opacity: row.modelData.removable ? 1 : 0
                            danger: !row.removed
                            icon: row.removed ? "undo" : "do_not_disturb_on"
                            iconSize: Theme.font.iconSize.small
                            tooltip: row.modelData.removable ? (row.removed ? I18n.t("dialog.metadata.keep") : I18n.t("dialog.metadata.remove")) : ""
                            onClicked: root.toggleRemove(row.itemId)
                        }
                    }
                }
            }

            StyledText {
                anchors.centerIn: parent
                width: parent.width - Theme.s(48)
                visible: root.ready && root.rows.length === 0
                text: root.itemCount === 0 ? I18n.t("dialog.metadata.empty") : I18n.t("dialog.metadata.no_results", {
                    query: root.query.trim()
                })
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                font.pointSize: Theme.font.size.normal
                color: Theme.palette.m3onSurfaceVariant
            }

            PreviewState {
                anchors.fill: parent
                loading: root.meta?.status === "loading"
                error: root.meta?.status === "error" ? (String(root.meta?.error ?? "") || I18n.t("dialog.preview.failed")) : ""
                icon: "info"
                loadingText: I18n.t("dialog.metadata.loading")
                onRetry: priv.ensure(root.currentFile)
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

            // Remove all
            StyledRect {
                width: parent.width
                height: removeAllCol.implicitHeight + Theme.padding.large * 2
                radius: Theme.rounding.large
                color: root.edit.removeAll ? Theme.palette.m3errorContainer : Theme.palette.m3surfaceContainerHigh

                Column {
                    id: removeAllCol

                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Theme.padding.large
                    spacing: Theme.spacing.small

                    Item {
                        width: parent.width
                        height: Math.max(removeAllText.implicitHeight, removeAllSwitch.implicitHeight)

                        StyledText {
                            id: removeAllText

                            anchors.left: parent.left
                            anchors.right: removeAllSwitch.left
                            anchors.rightMargin: Theme.spacing.medium
                            anchors.verticalCenter: parent.verticalCenter
                            text: I18n.t("dialog.metadata.remove_all")
                            wrapMode: Text.Wrap
                            font.pointSize: Theme.font.size.normal
                            font.weight: Font.Medium
                            color: root.edit.removeAll ? Theme.palette.m3onErrorContainer : Theme.palette.m3onSurface
                        }

                        M3Switch {
                            id: removeAllSwitch

                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            checked: root.edit.removeAll === true
                            disabled: !root.ready
                            onToggled: {
                                root.setRemoveAll(removeAllSwitch.checked);
                                // toggle() assigned `checked`: bind it again (undo, reset, file switch).
                                removeAllSwitch.checked = Qt.binding(() => root.edit.removeAll === true);
                            }
                        }
                    }

                    StyledText {
                        width: parent.width
                        text: I18n.t("dialog.metadata.remove_all_note")
                        wrapMode: Text.Wrap
                        font.pointSize: Theme.font.size.small
                        color: root.edit.removeAll ? Theme.palette.m3onErrorContainer : Theme.palette.m3onSurfaceVariant
                    }
                }
            }

            // Summary of changes
            Column {
                width: parent.width
                spacing: Theme.spacing.small

                StyledText {
                    text: I18n.t("dialog.metadata.summary")
                    font.pointSize: Theme.font.size.normal
                    font.weight: Font.Medium
                }

                StyledText {
                    width: parent.width
                    visible: root.changes.length === 0
                    text: I18n.t("dialog.metadata.no_changes")
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }

                Repeater {
                    model: root.changes.slice(0, 40)

                    Row {
                        id: changeRow

                        required property var modelData

                        width: parent.width
                        spacing: Theme.spacing.small

                        MaterialIcon {
                            text: changeRow.modelData.kind === "set" ? "edit" : (changeRow.modelData.kind === "all" ? "delete_sweep" : "do_not_disturb_on")
                            size: Theme.font.iconSize.small
                            color: changeRow.modelData.kind === "set" ? Theme.palette.m3primary : Theme.palette.m3error
                        }

                        StyledText {
                            width: parent.width - Theme.font.iconSize.small * 1.6 - parent.spacing
                            text: String(changeRow.modelData.text)
                            wrapMode: Text.Wrap
                            font.pointSize: Theme.font.size.small
                        }
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.changes.length > 40
                    text: I18n.t("dialog.metadata.more_changes", {
                        count: root.changes.length - 40
                    })
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }
            }

            // Source / limits
            Column {
                width: parent.width
                spacing: Theme.spacing.small
                visible: root.ready

                StyledText {
                    width: parent.width
                    text: I18n.t("dialog.metadata.source", {
                        source: String(root.meta?.source ?? "") || "—",
                        count: I18n.formatCount(root.itemCount)
                    })
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }

                StyledText {
                    width: parent.width
                    visible: root.meta?.editable !== true
                    text: I18n.t("dialog.metadata.read_only")
                    wrapMode: Text.Wrap
                    font.pointSize: Theme.font.size.small
                    color: Theme.palette.m3onSurfaceVariant
                }

                Row {
                    width: parent.width
                    spacing: Theme.spacing.small
                    visible: String(root.meta?.install ?? "") !== ""

                    StyledText {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - copyInstall.width - parent.spacing
                        text: I18n.t("dialog.metadata.install", {
                            command: String(root.meta?.install ?? "")
                        })
                        wrapMode: Text.Wrap
                        font.pointSize: Theme.font.size.small
                        color: Theme.palette.m3onSurfaceVariant
                    }

                    IconButton {
                        id: copyInstall

                        anchors.verticalCenter: parent.verticalCenter
                        type: ButtonBase.Text
                        icon: "content_copy"
                        iconSize: Theme.font.iconSize.small
                        tooltip: I18n.t("ui.copy_install")
                        onClicked: Backend.util("copy_text", {
                            text: String(root.meta?.install ?? "")
                        }, null)
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
                    exclude: root.optionExcludes(["metadata", "per_file"])
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
        edited: root.fileList.map((f, i) => priv.hasEdit(root.edits[i]))
        onSelected: index => root.currentIndex = index
    }
}
