// PreviewState — loading / error overlay for editor previews: an indeterminate spinner while loading, otherwise an
// icon, the error text and an optional "Retry" button. Invisible when neither loading nor failed.
//
//   property bool loading
//   property string error         message ("" = no error)
//   property string icon          error icon (default "broken_image")
//   property bool retryable       show the Retry button (default true)
//   property string loadingText   optional caption under the spinner
//   signal retry()

import QtQuick
import "../services"
import "../components"

Item {
    id: root

    property bool loading: false
    property string error: ""
    property string icon: "broken_image"
    property bool retryable: true
    property string loadingText: ""

    signal retry

    visible: root.loading || root.error !== ""

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.s(32), Theme.s(360))
        spacing: Theme.spacing.medium
        visible: root.loading

        CircularProgress {
            anchors.horizontalCenter: parent.horizontalCenter
            width: Theme.s(40)
            height: Theme.s(40)
            indeterminate: true
            running: root.loading && root.visible
        }

        StyledText {
            width: parent.width
            visible: root.loadingText !== ""
            text: root.loadingText
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            color: Theme.palette.m3onSurfaceVariant
        }
    }

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.s(32), Theme.s(360))
        spacing: Theme.spacing.medium
        visible: !root.loading && root.error !== ""

        MaterialIcon {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.icon
            size: Theme.font.iconSize.extraLarge
            color: Theme.palette.m3onSurfaceVariant
        }

        StyledText {
            width: parent.width
            text: root.error
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.pointSize: Theme.font.size.normal
            color: Theme.palette.m3onSurfaceVariant
        }

        TextButton {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: root.retryable
            type: ButtonBase.Tonal
            icon: "refresh"
            text: I18n.t("ui.retry")
            onClicked: root.retry()
        }
    }
}
