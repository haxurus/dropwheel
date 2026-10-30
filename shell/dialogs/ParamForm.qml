pragma ComponentBehavior: Bound

// ParamForm — generic parameter form (dialog "form", Alt+Enter on quick actions) and the embedded "Options" block of
// the visual editors. Renders node.params (Param JSON, SPEC §5.3) by type:
//   int / float → numeric field (unit suffix, min/max validation)      slider → M3Slider with value read-out
//   bool → switch row     select → chips (≤ 4 short options) or M3Dropdown     text / password / textarea → field
//   color → ColorPicker   time → hh:mm:ss.mmm field (sent as seconds)   timerange → start/end fields ({start, end})
//   pages → page-selection field ("1-3,5,8-z", "odd", "x4": the backend's syntax, checked against the page count)
//   path / dir → field + picker button (zenity / kdialog through the DialogSurface host, when installed)
// Structured types (rect, boxes, pageops, metadata, adjust, background) are left to the visual editors.
// visible_if {"other": value | [values]} hides a field (hidden values are not sent); defaults come from Param.default.
//
// EditorBase API plus:
//   property var exclude           param names not to render (embedded mode)
//   property bool embedded         no scrolling, no description / empty-state text (inside another editor)
//   readonly property var fields   rendered Param JSON list
//   readonly property bool hasFields
//   readonly property bool valid   every visible field passes validation
//   property var values            {name: value} current values (typed: numbers, bools, strings, {start, end})
//   function collectParams() → {name: value} for the visible fields, or null when invalid (errors are then shown)
//   function requestApply()        collectParams() → apply(params)
//   function reset()               back to the defaults
//   function undo() → false        (fields have their own Ctrl+Z)
//   function setField(name, value, error)

import QtQuick
import "../services"
import "../components"
import "DialogUtils.js" as DU

EditorBase {
    id: root

    property var exclude: []
    property bool embedded: false

    readonly property var fields: {
        const ex = Array.isArray(root.exclude) ? root.exclude : [];
        return root.paramList.filter(p => root.formTypes.indexOf(String(p.type ?? "")) !== -1 && ex.indexOf(p.name) === -1);
    }
    readonly property bool hasFields: root.fields.length > 0
    property var values: ({})
    property var errors: ({})
    property bool showErrors: false
    property int generation: -1 // fields are created once initValues() has computed the defaults
    readonly property bool valid: {
        const errs = root.errors;
        for (const p of root.fields) {
            if (root.isVisible(p) && typeof errs[p.name] === "string" && errs[p.name] !== "")
                return false;
        }
        return true;
    }

    function defaultValue(p: var): var {
        const d = p.default;
        const type = String(p.type ?? "text");
        switch (type) {
        case "bool":
            return d === true || d === "true" || d === 1;
        case "int":
        case "float":
            return DU.num(d);
        case "slider":
            {
                const n = DU.num(d);
                return n !== null ? n : (DU.num(p.min) ?? 0);
            }
        case "select":
            {
                const opts = Array.isArray(p.options) ? p.options : [];
                if (d !== null && d !== undefined && opts.some(o => DU.same(o?.value, d) || String(o?.value) === String(d)))
                    return d;
                return opts.length > 0 ? (opts[0]?.value ?? null) : (d ?? null);
            }
        case "color":
            return (typeof d === "string" && d !== "") ? d : "#000000";
        case "time":
            {
                const n = DU.num(d);
                return n !== null ? n : (typeof d === "string" ? (isNaN(DU.parseTime(d)) ? null : DU.parseTime(d)) : null);
            }
        case "timerange":
            {
                if (Array.isArray(d) && d.length >= 2)
                    return {
                        start: DU.num(d[0]) ?? 0,
                        end: DU.num(d[1])
                    };
                if (DU.isObject(d))
                    return {
                        start: DU.num(d.start) ?? 0,
                        end: DU.num(d.end)
                    };
                return {
                    start: 0,
                    end: root.currentFile?.duration ?? null
                };
            }
        default:
            return (d === null || d === undefined) ? "" : String(d);
        }
    }

    function looseEq(a: var, b: var): bool {
        if (DU.same(a, b))
            return true;
        if (a === null || a === undefined || b === null || b === undefined)
            return false;
        return String(a) === String(b);
    }

    function isVisible(p: var): bool {
        const cond = p?.visible_if;
        if (!DU.isObject(cond))
            return true;
        const vals = root.values;
        for (const key of Object.keys(cond)) {
            const want = cond[key];
            const have = vals[key];
            if (Array.isArray(want)) {
                if (!want.some(w => root.looseEq(w, have)))
                    return false;
            } else if (!root.looseEq(want, have)) {
                return false;
            }
        }
        return true;
    }

    function textFor(p: var, v: var): string {
        const type = String(p.type ?? "text");
        if (v === null || v === undefined)
            return "";
        if (type === "time")
            return DU.formatTime(v, true);
        if (type === "int" || type === "float") {
            const s = String(v);
            return I18n.lang === "it" ? s.replace(".", ",") : s;
        }
        return String(v);
    }

    function rangeHint(p: var): string {
        const lo = DU.num(p.min);
        const hi = DU.num(p.max);
        if (lo !== null && hi !== null)
            return I18n.t("dialog.form.range_hint", {
                min: I18n.formatNumber(lo, Math.floor(lo) === lo ? 0 : 2),
                max: I18n.formatNumber(hi, Math.floor(hi) === hi ? 0 : 2)
            });
        if (lo !== null)
            return I18n.t("dialog.form.min_hint", {
                min: I18n.formatNumber(lo, Math.floor(lo) === lo ? 0 : 2)
            });
        if (hi !== null)
            return I18n.t("dialog.form.max_hint", {
                max: I18n.formatNumber(hi, Math.floor(hi) === hi ? 0 : 2)
            });
        return "";
    }

    // Validation of a non-empty text (M3TextField validator); "" = valid.
    function validateText(p: var, text: string): string {
        const type = String(p.type ?? "text");
        const t = String(text ?? "").trim();
        if (t === "")
            return "";
        const lo = DU.num(p.min);
        const hi = DU.num(p.max);
        if (type === "int" || type === "float") {
            const n = DU.parseNumber(t, type === "int");
            if (isNaN(n))
                return type === "int" ? I18n.t("ui.field.integer") : I18n.t("ui.field.number");
            if ((lo !== null && n < lo) || (hi !== null && n > hi))
                return I18n.t("ui.field.range", {
                    min: lo !== null ? I18n.formatNumber(lo, Math.floor(lo) === lo ? 0 : 2) : "−∞",
                    max: hi !== null ? I18n.formatNumber(hi, Math.floor(hi) === hi ? 0 : 2) : "∞"
                });
            return "";
        }
        if (type === "time") {
            const s = DU.parseTime(t);
            if (isNaN(s))
                return I18n.t("dialog.form.time_invalid");
            if ((lo !== null && s < lo) || (hi !== null && s > hi))
                return I18n.t("ui.field.range", {
                    min: lo !== null ? DU.formatTime(lo, false) : "0",
                    max: hi !== null ? DU.formatTime(hi, false) : "∞"
                });
            return "";
        }
        if (type === "pages") {
            const pages = root.currentFile?.pages ?? null;
            const res = DU.validatePages(t, pages);
            if (res === "syntax")
                return I18n.t("dialog.form.pages_syntax");
            if (res === "range")
                return pages !== null ? I18n.t("dialog.form.pages_range", {
                    count: pages
                }) : I18n.t("dialog.form.pages_syntax");
            if (res === "none")
                return I18n.t("dialog.form.pages_none");
            return "";
        }
        return "";
    }

    function errorFor(p: var, text: string): string {
        if (p.required === true && String(text ?? "").trim() === "")
            return I18n.t("ui.field.required");
        return root.validateText(p, text);
    }

    function parseText(p: var, text: string): var {
        const type = String(p.type ?? "text");
        const t = String(text ?? "");
        if (type === "int" || type === "float") {
            const n = DU.parseNumber(t, type === "int");
            return isNaN(n) ? null : n;
        }
        if (type === "time") {
            const s = DU.parseTime(t);
            return isNaN(s) ? null : s;
        }
        // Sent as typed: the backend reads spaces as separators ("1 3" = pages 1 and 3, not 13).
        if (type === "pages")
            return t.trim();
        return t;
    }

    function setField(name: string, value: var, error: var): void {
        if (!DU.same(root.values[name], value)) {
            const next = Object.assign({}, root.values);
            next[name] = value;
            root.values = next;
        }
        const err = (typeof error === "string") ? error : "";
        if ((root.errors[name] ?? "") !== err) {
            const e = Object.assign({}, root.errors);
            e[name] = err;
            root.errors = e;
        }
        root.dirty = !DU.same(root.values, priv.defaults);
    }

    function initValues(): void {
        const v = {};
        for (const p of root.fields)
            v[p.name] = root.defaultValue(p);
        priv.defaults = DU.copy(v);
        root.values = v;
        root.errors = ({});
        root.showErrors = false;
        root.dirty = false;
        root.generation++;
    }

    function collectParams(): var {
        root.showErrors = true;
        if (!root.valid)
            return null;
        const out = {};
        for (const p of root.fields) {
            if (!root.isVisible(p))
                continue;
            const v = root.values[p.name];
            if (v === undefined || v === null)
                continue;
            out[p.name] = DU.copy(v);
        }
        return out;
    }

    function requestApply(): void {
        const params = root.collectParams();
        if (params !== null)
            root.apply(params);
    }

    function reset(): void {
        root.initValues();
    }

    function undo(): bool {
        return false;
    }

    wide: false
    // The button starts the job. A stand-alone action label is the verb ("Estrai in…"), without the "…" (it means
    // "opens more UI", and the form is that UI); the leaf of a group ("Converti · JPG", "Comprimi · Bilanciata") says
    // what, not what to do: "Applica".
    applyLabel: String(root.node?.title ?? "").indexOf(" · ") !== -1 ? I18n.t("ui.apply") : DU.stripEllipsis(String(root.node?.label ?? ""))
    resettable: root.hasFields
    footerHint: root.showErrors && !root.valid ? I18n.t("dialog.form.fix_errors") : ""
    footerHintError: true
    implicitWidth: Theme.s(480)
    implicitHeight: content.implicitHeight + (root.embedded ? 0 : priv.pad * 2)

    Component.onCompleted: root.initValues()

    QtObject {
        id: priv

        property var defaults: ({})
        // Inner padding of the standalone form (room for focus rings and the scrollbar).
        readonly property real pad: Theme.spacing.small + Math.round(2 * Theme.scale)
        readonly property int firstTextIndex: {
            const textual = ["int", "float", "text", "password", "time", "pages", "path", "dir", "textarea", "timerange"];
            for (let i = 0; i < root.fields.length; i++) {
                if (textual.indexOf(String(root.fields[i].type)) !== -1)
                    return i;
            }
            return -1;
        }

        function sliderUnit(u: string): string {
            if (u === "")
                return "";
            return u === "%" ? "%" : " " + u;
        }

        function useChips(p: var): bool {
            const opts = Array.isArray(p.options) ? p.options : [];
            if (opts.length === 0 || opts.length > 4)
                return false;
            return opts.every(o => String(o?.label ?? o?.value ?? "").length <= 14);
        }
    }

    ScrollArea {
        id: scroll

        anchors.fill: parent
        interactive: !root.embedded && scroll.scrollable

        Column {
            id: content

            x: root.embedded ? 0 : priv.pad
            y: root.embedded ? 0 : priv.pad
            width: scroll.width - (root.embedded ? 0 : priv.pad * 2)
            spacing: Theme.spacing.large

            StyledText {
                width: parent.width
                visible: !root.embedded && text !== ""
                text: String(root.node?.description ?? "")
                wrapMode: Text.Wrap
                font.pointSize: Theme.font.size.normal
                color: Theme.palette.m3onSurfaceVariant
            }

            StyledText {
                width: parent.width
                visible: !root.embedded && !root.hasFields
                text: I18n.t("dialog.form.no_params")
                wrapMode: Text.Wrap
                font.pointSize: Theme.font.size.normal
                color: Theme.palette.m3onSurfaceVariant
            }

            Repeater {
                model: root.generation >= 0 ? root.fields.slice() : []

                delegate: Item {
                    id: row

                    required property var modelData
                    required property int index
                    readonly property var p: row.modelData
                    readonly property string name: String(row.modelData.name)
                    readonly property string type: String(row.modelData.type ?? "text")
                    readonly property string label: String(row.modelData.label ?? row.modelData.name)
                    readonly property string help: String(row.modelData.help ?? "")
                    readonly property string unit: String(row.modelData.unit ?? "")
                    readonly property bool shown: root.isVisible(row.modelData)

                    width: content.width
                    height: loader.height
                    visible: row.shown

                    Loader {
                        id: loader

                        width: parent.width
                        sourceComponent: {
                            switch (row.type) {
                            case "bool":
                                return boolComp;
                            case "slider":
                                return sliderComp;
                            case "select":
                                return priv.useChips(row.p) ? chipsComp : dropdownComp;
                            case "color":
                                return colorComp;
                            case "timerange":
                                return rangeComp;
                            default:
                                return textComp;
                            }
                        }
                    }

                    Component {
                        id: textComp

                        Item {
                            id: textItem

                            readonly property bool pickable: (row.type === "path" || row.type === "dir") && root.host !== null && (root.host?.pickerAvailable ?? false)

                            implicitHeight: tf.implicitHeight

                            M3TextField {
                                id: tf

                                anchors.left: parent.left
                                anchors.right: pickButton.visible ? pickButton.left : parent.right
                                anchors.rightMargin: pickButton.visible ? Theme.spacing.small : 0
                                label: row.label
                                password: row.type === "password"
                                multiline: row.type === "textarea"
                                mono: row.type === "path" || row.type === "dir" || row.type === "pages" || row.type === "time"
                                suffixText: row.type === "int" || row.type === "float" ? row.unit : ""
                                placeholderText: row.type === "time" ? "00:00:00.000" : (row.type === "pages" ? I18n.t("dialog.form.pages_placeholder") : "")
                                required: row.p.required === true
                                showErrors: root.showErrors
                                clearButton: row.type === "text" || row.type === "path" || row.type === "dir"
                                supportingText: row.help !== "" ? row.help : ((row.type === "int" || row.type === "float" || row.type === "time") ? root.rangeHint(row.p) : (row.type === "pages" ? I18n.t("dialog.form.pages_help") : ""))
                                inputMethodHints: (row.type === "int" ? Qt.ImhFormattedNumbersOnly : (row.type === "float" ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone))
                                validator: t => root.validateText(row.p, t)
                                // In a visual editor's Options, Enter only confirms the value like the editor's own
                                // fields (Ctrl+Enter applies); in the standalone form it reaches DialogSurface: Apply.
                                Keys.onReturnPressed: event => event.accepted = root.embedded && (event.modifiers & Qt.ControlModifier) === 0
                                Keys.onEnterPressed: event => event.accepted = root.embedded && (event.modifiers & Qt.ControlModifier) === 0
                                onTextChanged: root.setField(row.name, root.parseText(row.p, tf.text), root.errorFor(row.p, tf.text))
                                onEditingFinished: {
                                    if (row.type === "time") {
                                        const s = DU.parseTime(tf.text);
                                        if (!isNaN(s))
                                            tf.text = DU.formatTime(s, true);
                                    }
                                }

                                Component.onCompleted: {
                                    tf.text = root.textFor(row.p, root.values[row.name]);
                                    root.setField(row.name, root.parseText(row.p, tf.text), root.errorFor(row.p, tf.text));
                                    if (!root.embedded && row.index === priv.firstTextIndex)
                                        Qt.callLater(() => tf.focusInput());
                                }
                            }

                            IconButton {
                                id: pickButton

                                anchors.right: parent.right
                                y: Math.round((Theme.s(48) - height) / 2)
                                visible: textItem.pickable
                                type: ButtonBase.Tonal
                                icon: row.type === "dir" ? "folder_open" : "draft"
                                tooltip: row.type === "dir" ? I18n.t("dialog.form.choose_folder") : I18n.t("dialog.form.choose_file")
                                onClicked: root.host.pickFile({
                                    directory: row.type === "dir",
                                    title: row.label,
                                    start: tf.text
                                }, path => {
                                    if (typeof path === "string" && path !== "")
                                        tf.text = path;
                                })
                            }
                        }
                    }

                    Component {
                        id: boolComp

                        Item {
                            implicitHeight: Math.max(boolText.implicitHeight, sw.implicitHeight)

                            Column {
                                id: boolText

                                anchors.left: parent.left
                                anchors.right: sw.left
                                anchors.rightMargin: Theme.spacing.medium
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.spacing.extraSmall

                                StyledText {
                                    width: parent.width
                                    text: row.label
                                    wrapMode: Text.Wrap
                                    font.pointSize: Theme.font.size.normal
                                }

                                StyledText {
                                    width: parent.width
                                    visible: text !== ""
                                    text: row.help
                                    wrapMode: Text.Wrap
                                    font.pointSize: Theme.font.size.small
                                    color: Theme.palette.m3onSurfaceVariant
                                }
                            }

                            M3Switch {
                                id: sw

                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                checked: root.values[row.name] === true
                                onToggled: root.setField(row.name, sw.checked, "")
                            }

                            MouseArea {
                                anchors.fill: boolText
                                cursorShape: Qt.PointingHandCursor
                                onClicked: sw.toggle()
                            }
                        }
                    }

                    Component {
                        id: sliderComp

                        Column {
                            spacing: 0

                            Item {
                                width: parent.width
                                height: sliderLabel.implicitHeight

                                StyledText {
                                    id: sliderLabel

                                    anchors.left: parent.left
                                    anchors.right: sliderValue.left
                                    anchors.rightMargin: Theme.spacing.small
                                    text: row.label
                                    elide: Text.ElideRight
                                    font.pointSize: Theme.font.size.normal
                                }

                                StyledText {
                                    id: sliderValue

                                    anchors.right: parent.right
                                    text: slider.valueText
                                    font.pointSize: Theme.font.size.normal
                                    font.weight: Font.Medium
                                    color: Theme.palette.m3primary
                                }
                            }

                            M3Slider {
                                id: slider

                                width: parent.width
                                from: DU.num(row.p.min) ?? 0
                                to: DU.num(row.p.max) ?? 100
                                stepSize: DU.num(row.p.step) ?? 1
                                unit: priv.sliderUnit(row.unit)
                                value: DU.num(root.values[row.name]) ?? slider.from
                                onMoved: root.setField(row.name, slider.value, "")
                            }

                            StyledText {
                                width: parent.width
                                visible: text !== ""
                                text: row.help
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.small
                                color: Theme.palette.m3onSurfaceVariant
                            }
                        }
                    }

                    Component {
                        id: dropdownComp

                        M3Dropdown {
                            id: dd

                            label: row.label
                            options: Array.isArray(row.p.options) ? row.p.options : []
                            value: root.values[row.name]
                            supportingText: row.help
                            onActivated: (value, index) => root.setField(row.name, value, "")
                        }
                    }

                    Component {
                        id: chipsComp

                        Column {
                            spacing: Theme.spacing.small

                            StyledText {
                                width: parent.width
                                text: row.label
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.normal
                            }

                            Flow {
                                width: parent.width
                                spacing: Theme.spacing.small

                                Repeater {
                                    model: Array.isArray(row.p.options) ? row.p.options : []

                                    Chip {
                                        id: chip

                                        required property var modelData

                                        type: Chip.Filter
                                        activeFocusOnTab: true
                                        text: String(chip.modelData?.label ?? chip.modelData?.value ?? "")
                                        selected: root.looseEq(root.values[row.name], chip.modelData?.value)
                                        onClicked: root.setField(row.name, chip.modelData?.value ?? null, "")
                                    }
                                }
                            }

                            StyledText {
                                width: parent.width
                                visible: text !== ""
                                text: row.help
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.small
                                color: Theme.palette.m3onSurfaceVariant
                            }
                        }
                    }

                    Component {
                        id: colorComp

                        Column {
                            spacing: Theme.spacing.small

                            StyledText {
                                width: parent.width
                                text: row.label
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.normal
                            }

                            ColorPicker {
                                id: picker

                                width: parent.width
                                alpha: false
                                value: String(root.values[row.name] ?? "#000000")
                                onPicked: value => root.setField(row.name, value, "")
                            }

                            StyledText {
                                width: parent.width
                                visible: text !== ""
                                text: row.help
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.small
                                color: Theme.palette.m3onSurfaceVariant
                            }
                        }
                    }

                    Component {
                        id: rangeComp

                        Column {
                            id: rangeBox

                            readonly property string endError: {
                                const s = DU.parseTime(startField.text);
                                const e = DU.parseTime(endField.text);
                                if (!isNaN(s) && !isNaN(e) && e <= s)
                                    return I18n.t("dialog.form.range_order");
                                return "";
                            }

                            function push(): void {
                                const s = DU.parseTime(startField.text);
                                const e = DU.parseTime(endField.text);
                                const err = startField.validationMessage !== "" ? startField.validationMessage : (endField.validationMessage !== "" ? endField.validationMessage : rangeBox.endError);
                                root.setField(row.name, {
                                    start: isNaN(s) ? null : s,
                                    end: isNaN(e) ? null : e
                                }, err);
                            }

                            spacing: Theme.spacing.small

                            StyledText {
                                width: parent.width
                                text: row.label
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.normal
                            }

                            Row {
                                width: parent.width
                                spacing: Theme.spacing.medium

                                M3TextField {
                                    id: startField

                                    width: (parent.width - parent.spacing) / 2
                                    label: I18n.t("dialog.trim.start")
                                    mono: true
                                    required: true
                                    showErrors: root.showErrors
                                    placeholderText: "00:00:00.000"
                                    validator: t => isNaN(DU.parseTime(t)) ? I18n.t("dialog.form.time_invalid") : ""
                                    onTextChanged: rangeBox.push()
                                }

                                M3TextField {
                                    id: endField

                                    width: (parent.width - parent.spacing) / 2
                                    label: I18n.t("dialog.trim.end")
                                    mono: true
                                    required: true
                                    showErrors: root.showErrors
                                    placeholderText: "00:00:00.000"
                                    errorText: rangeBox.endError
                                    validator: t => isNaN(DU.parseTime(t)) ? I18n.t("dialog.form.time_invalid") : ""
                                    onTextChanged: rangeBox.push()
                                }
                            }

                            StyledText {
                                width: parent.width
                                visible: text !== ""
                                text: row.help
                                wrapMode: Text.Wrap
                                font.pointSize: Theme.font.size.small
                                color: Theme.palette.m3onSurfaceVariant
                            }

                            Component.onCompleted: {
                                const v = root.values[row.name];
                                startField.text = DU.formatTime(DU.num(v?.start) ?? 0, true);
                                endField.text = DU.num(v?.end) !== null ? DU.formatTime(v.end, true) : "";
                                rangeBox.push();
                            }
                        }
                    }
                }
            }
        }
    }
}
