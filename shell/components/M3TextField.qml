pragma ComponentBehavior: Bound

// M3TextField — M3 outlined text field: floating label, placeholder, password mode with reveal toggle,
// prefix/suffix text, leading/trailing icon, clear button, multi-line mode and validation with error text.
// It is a FocusScope: forceActiveFocus() focuses the editor.
//
//   property string text             two-way text
//   property string label            floating label
//   property string placeholderText  hint inside the empty field (shown while focused when a label is set)
//   property bool password           masked input + "visibility" toggle
//   property bool multiline          TextEdit with wrapping (minLines..maxLines tall, then scrolls)
//   property int minLines: 3, maxLines: 8
//   property bool readOnly, disabled
//   property bool mono               monospace font (paths, code, hex)
//   property string leadingIcon, trailingIcon, prefixText, suffixText ("px", "%", "dpi"…)
//   property bool clearButton        shows an × button when not empty
//   property string supportingText   helper text below the field
//   property string errorText        external error (e.g. from the backend); forces the error state
//   property var validator           RegExp, or function(text) → true | false | "error message"
//   property bool required           empty → "Required"
//   property bool showErrors         show validation errors before the user leaves the field (form submit)
//   property int maximumLength, inputMethodHints, horizontalAlignment
//   readonly property bool valid             passes required + validator and errorText is empty
//   readonly property string validationMessage   current validation problem ("" when valid)
//   readonly property bool hasError          error currently displayed
//   readonly property var editor             the TextInput (single line) or TextEdit (multi-line)
//   signal accepted()   Enter in a single-line field (only when valid)
//   signal editingFinished()   signal textEdited()   (user edit; `text` already holds the new value)
//   function validate() → bool   marks the field as touched (shows the error) and returns `valid`
//   function selectAll(), clear(), focusInput()

import QtQuick
import "../services"

FocusScope {
    id: root

    property string text: ""
    property string label: ""
    property string placeholderText: ""
    property bool password: false
    property bool multiline: false
    property int minLines: 3
    property int maxLines: 8
    property bool readOnly: false
    property bool disabled: false
    property bool mono: false
    property string leadingIcon: ""
    property string trailingIcon: ""
    property string prefixText: ""
    property string suffixText: ""
    property bool clearButton: false
    property string supportingText: ""
    property string errorText: ""
    property var validator: null
    property bool required: false
    property bool showErrors: false
    property int maximumLength: 32767
    property int inputMethodHints: Qt.ImhNone
    property int horizontalAlignment: TextInput.AlignLeft

    readonly property string validationMessage: {
        const t = root.text;
        if (root.required && t.trim() === "")
            return I18n.t("ui.field.required");
        if (t === "" || root.validator === null || root.validator === undefined)
            return "";
        const v = root.validator;
        if (v instanceof RegExp) {
            v.lastIndex = 0;
            return v.test(t) ? "" : I18n.t("ui.field.invalid");
        }
        if (typeof v === "function") {
            try {
                const r = v(t);
                if (r === true || r === undefined || r === null || r === "")
                    return "";
                if (r === false)
                    return I18n.t("ui.field.invalid");
                return String(r);
            } catch (e) {
                return I18n.t("ui.field.invalid");
            }
        }
        return "";
    }
    readonly property bool valid: root.validationMessage === "" && root.errorText === ""
    readonly property bool hasError: root.errorText !== "" || ((priv.touched || root.showErrors) && root.validationMessage !== "")
    readonly property var editor: root.multiline ? edit : field

    signal accepted
    signal editingFinished
    signal textEdited

    function validate(): bool {
        priv.touched = true;
        return root.valid;
    }

    function selectAll(): void {
        root.editor.selectAll();
    }

    function clear(): void {
        root.text = "";
        root.textEdited();
    }

    function focusInput(): void {
        root.editor.forceActiveFocus();
    }

    enabled: !disabled
    implicitWidth: Math.round(240 * Theme.scale)
    implicitHeight: frame.implicitHeight

    onTextChanged: {
        if (field.text !== root.text)
            field.text = root.text;
        if (edit.text !== root.text)
            edit.text = root.text;
    }
    Component.onCompleted: {
        field.text = root.text;
        edit.text = root.text;
    }

    QtObject {
        id: priv

        property bool touched: false
        property bool reveal: false
        readonly property bool focused: field.activeFocus || edit.activeFocus
        readonly property string fontFamily: root.mono ? Theme.font.family.mono : Theme.font.family.sans
        readonly property real lineHeight: Math.ceil(fontMetrics.lineSpacing)
        readonly property real vPad: Theme.padding.medium + Math.round(2 * Theme.scale)
        readonly property real leftInset: (root.leadingIcon !== "" ? leading.width + Theme.padding.medium + Theme.spacing.small : Theme.padding.large) + (root.prefixText !== "" ? prefix.implicitWidth + Theme.spacing.extraSmall : 0)
        readonly property real rightInset: trailingRow.width > 0 ? trailingRow.width + Theme.padding.small + Theme.spacing.extraSmall : Theme.padding.large
    }

    FontMetrics {
        id: fontMetrics

        font.family: priv.fontFamily
        font.pointSize: Theme.font.size.normal
    }

    FieldFrame {
        id: frame

        anchors.left: parent.left
        anchors.right: parent.right
        label: root.label
        focused: priv.focused
        populated: root.text !== "" || (root.placeholderText !== "" && priv.focused)
        hovered: hoverArea.containsMouse
        error: root.hasError
        disabled: root.disabled
        supportingText: root.supportingText
        errorText: root.errorText !== "" ? root.errorText : root.validationMessage
        labelLeft: root.leadingIcon !== "" ? leading.width + Theme.padding.medium + Theme.spacing.small : Theme.padding.large
        labelAtTop: root.multiline
        boxHeight: {
            if (!root.multiline)
                return Math.round(48 * Theme.scale);
            const lines = Math.max(root.minLines, Math.min(root.maxLines, edit.lineCount));
            return Math.ceil(lines * priv.lineHeight + priv.vPad * 2);
        }
    }

    // Clicking anywhere in the box focuses the editor.
    MouseArea {
        id: hoverArea

        x: frame.box.x
        y: frame.box.y
        width: frame.box.width
        height: frame.box.height
        hoverEnabled: true
        cursorShape: root.readOnly ? Qt.ArrowCursor : Qt.IBeamCursor
        onPressed: event => {
            root.editor.forceActiveFocus();
            event.accepted = false;
        }
    }

    MaterialIcon {
        id: leading

        x: Theme.padding.medium
        y: root.multiline ? priv.vPad : Math.round((frame.box.height - height) / 2) + 1
        visible: root.leadingIcon !== ""
        text: root.leadingIcon
        size: Theme.font.iconSize.medium
        color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3onSurfaceVariant
    }

    StyledText {
        id: prefix

        x: (root.leadingIcon !== "" ? leading.width + Theme.padding.medium + Theme.spacing.small : Theme.padding.large)
        y: root.multiline ? priv.vPad : Math.round((frame.box.height - height) / 2)
        visible: root.prefixText !== "" && (frame.populated || root.label === "")
        text: root.prefixText
        font.family: priv.fontFamily
        font.pointSize: Theme.font.size.normal
        color: Theme.palette.m3onSurfaceVariant
    }

    TextInput {
        id: field

        visible: !root.multiline
        enabled: !root.multiline
        focus: !root.multiline
        activeFocusOnTab: !root.multiline
        x: priv.leftInset
        width: Math.max(0, frame.box.width - priv.leftInset - priv.rightInset)
        height: frame.box.height
        y: frame.box.y
        verticalAlignment: TextInput.AlignVCenter
        horizontalAlignment: root.horizontalAlignment
        clip: true
        readOnly: root.readOnly
        selectByMouse: true
        persistentSelection: false
        maximumLength: root.maximumLength
        inputMethodHints: root.inputMethodHints | (root.password ? Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase : 0)
        echoMode: root.password && !priv.reveal ? TextInput.Password : TextInput.Normal
        color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3onSurface
        selectionColor: Qt.alpha(Theme.palette.m3primary, 0.4)
        selectedTextColor: Theme.palette.m3onSurface
        font.family: priv.fontFamily
        font.pointSize: Theme.font.size.normal
        font.variableAxes: root.mono ? ({}) : Theme.font.axes
        cursorDelegate: cursorComponent

        onTextChanged: {
            if (!root.multiline && root.text !== text)
                root.text = text;
        }
        // TextInput emits textEdited BEFORE textChanged: sync first, or handlers read the previous keystroke's text.
        onTextEdited: {
            if (root.text !== text)
                root.text = text;
            root.textEdited();
        }
        onAccepted: {
            priv.touched = true;
            if (root.valid)
                root.accepted();
        }
        onEditingFinished: {
            priv.touched = true;
            root.editingFinished();
        }
    }

    Flickable {
        id: editFlick

        visible: root.multiline
        x: priv.leftInset
        y: frame.box.y + priv.vPad
        width: Math.max(0, frame.box.width - priv.leftInset - priv.rightInset)
        height: Math.max(0, frame.box.height - priv.vPad * 2)
        contentWidth: width
        contentHeight: edit.contentHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        function ensureVisible(r: rect): void {
            if (contentY >= r.y)
                contentY = r.y;
            else if (contentY + height <= r.y + r.height)
                contentY = r.y + r.height - height;
        }

        TextEdit {
            id: edit

            width: editFlick.width
            enabled: root.multiline
            focus: root.multiline
            activeFocusOnTab: root.multiline
            wrapMode: TextEdit.Wrap
            readOnly: root.readOnly
            selectByMouse: true
            textFormat: TextEdit.PlainText
            inputMethodHints: root.inputMethodHints
            color: root.disabled ? Qt.alpha(Theme.palette.m3onSurface, 0.38) : Theme.palette.m3onSurface
            selectionColor: Qt.alpha(Theme.palette.m3primary, 0.4)
            selectedTextColor: Theme.palette.m3onSurface
            font.family: priv.fontFamily
            font.pointSize: Theme.font.size.normal
            font.variableAxes: root.mono ? ({}) : Theme.font.axes
            cursorDelegate: cursorComponent

            onTextChanged: {
                if (root.multiline && root.text !== text) {
                    if (root.maximumLength > 0 && text.length > root.maximumLength) {
                        remove(root.maximumLength, text.length);
                        return;
                    }
                    root.text = text;
                    root.textEdited();
                }
            }
            onCursorRectangleChanged: editFlick.ensureVisible(cursorRectangle)
            onEditingFinished: {
                priv.touched = true;
                root.editingFinished();
            }
        }
    }

    StyledText {
        id: placeholder

        x: priv.leftInset
        y: root.multiline ? editFlick.y : Math.round((frame.box.height - height) / 2)
        width: Math.max(0, frame.box.width - priv.leftInset - priv.rightInset)
        visible: root.text === "" && root.placeholderText !== "" && (root.label === "" || priv.focused)
        text: root.placeholderText
        elide: Text.ElideRight
        font.family: priv.fontFamily
        font.pointSize: Theme.font.size.normal
        color: Qt.alpha(Theme.palette.m3onSurfaceVariant, root.disabled ? 0.38 : 0.8)
    }

    Row {
        id: trailingRow

        anchors.right: parent.right
        anchors.rightMargin: Theme.padding.small
        y: root.multiline ? Theme.padding.small : Math.round((frame.box.height - height) / 2)
        spacing: 0

        StyledText {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.suffixText !== "" && (frame.populated || root.label === "")
            text: root.suffixText
            rightPadding: Theme.spacing.small
            font.family: priv.fontFamily
            font.pointSize: Theme.font.size.normal
            color: Theme.palette.m3onSurfaceVariant
        }

        IconButton {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.clearButton && root.text !== "" && !root.readOnly && !root.disabled
            type: ButtonBase.Text
            icon: "close"
            iconSize: Theme.font.iconSize.small
            tooltip: I18n.t("ui.clear")
            onClicked: {
                root.clear();
                root.focusInput();
            }
        }

        IconButton {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.password && !root.multiline
            type: ButtonBase.Text
            icon: priv.reveal ? "visibility_off" : "visibility"
            iconSize: Theme.font.iconSize.small
            tooltip: priv.reveal ? I18n.t("ui.field.hide_password") : I18n.t("ui.field.show_password")
            onClicked: priv.reveal = !priv.reveal
        }

        Item {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.trailingIcon !== "" || root.hasError
            implicitWidth: trailingIconLabel.implicitWidth + Theme.padding.small
            implicitHeight: trailingIconLabel.implicitHeight

            MaterialIcon {
                id: trailingIconLabel

                anchors.centerIn: parent
                anchors.verticalCenterOffset: 1
                text: root.hasError ? "error" : root.trailingIcon
                fill: root.hasError ? 1 : 0
                size: Theme.font.iconSize.medium
                color: root.hasError ? Theme.palette.m3error : Theme.palette.m3onSurfaceVariant
            }
        }
    }

    Component {
        id: cursorComponent

        Rectangle {
            id: cursor

            property bool blinkOn: true
            readonly property var owner: parent // the TextInput or TextEdit that created this cursor

            width: Math.max(1, Math.round(1.5 * Theme.scale))
            height: owner ? owner.cursorRectangle.height : 0
            radius: width / 2
            color: root.hasError ? Theme.palette.m3error : Theme.palette.m3primary
            visible: owner !== null && owner.activeFocus && !root.readOnly && blinkOn

            Timer {
                interval: 530
                repeat: true
                running: cursor.owner !== null && cursor.owner.activeFocus && !root.readOnly
                onTriggered: cursor.blinkOn = !cursor.blinkOn
                onRunningChanged: cursor.blinkOn = true
            }

            Connections {
                target: cursor.owner

                function onCursorPositionChanged(): void {
                    cursor.blinkOn = true;
                }
            }
        }
    }
}
