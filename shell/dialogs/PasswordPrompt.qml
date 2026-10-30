// PasswordPrompt — inline unlock prompt for password-protected PDFs, shown over an editor's preview area.
//
//   property bool wrong       the last password was rejected (error under the field)
//   property bool busy        a check is running (button disabled, spinner icon)
//   property string message   text above the field (default: "This PDF is protected by a password")
//   signal submitted(string password)
//   function focusField()

import QtQuick
import "../services"
import "../components"

Item {
    id: root

    property bool wrong: false
    property bool busy: false
    property string message: I18n.t("dialog.pdf.locked")

    signal submitted(string password)

    function focusField(): void {
        field.focusInput();
    }

    function submit(): void {
        if (field.text === "" || root.busy)
            return;
        priv.edited = false;
        root.submitted(field.text);
    }

    onWrongChanged: {
        priv.edited = false;
        if (root.wrong)
            field.selectAll();
    }

    QtObject {
        id: priv

        // The user typed since the last rejection: hide the error (without touching the bound `wrong`).
        property bool edited: false
    }

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.s(32), Theme.s(360))
        spacing: Theme.spacing.medium

        MaterialIcon {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "lock"
            size: Theme.font.iconSize.extraLarge
            color: Theme.palette.m3primary
        }

        StyledText {
            width: parent.width
            text: root.message
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.pointSize: Theme.font.size.normal
            color: Theme.palette.m3onSurfaceVariant
        }

        M3TextField {
            id: field

            width: parent.width
            password: true
            leadingIcon: "key"
            label: I18n.t("dialog.pdf.password")
            errorText: root.wrong && !priv.edited ? I18n.t("dialog.pdf.wrong_password") : ""
            disabled: root.busy
            onTextEdited: priv.edited = true
            // Enter unlocks; it must not reach the dialog (which would apply).
            Keys.onReturnPressed: event => {
                root.submit();
                event.accepted = true;
            }
            Keys.onEnterPressed: event => {
                root.submit();
                event.accepted = true;
            }
        }

        TextButton {
            anchors.horizontalCenter: parent.horizontalCenter
            type: ButtonBase.Filled
            icon: root.busy ? "hourglass_top" : "lock_open"
            text: I18n.t("dialog.pdf.unlock")
            disabled: field.text === "" || root.busy
            onClicked: root.submit()
        }
    }
}
