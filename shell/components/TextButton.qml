// TextButton — labelled button in Caelestia style (filled / tonal / text / outlined) with an optional leading icon.
// See ButtonBase for the shared API (type, checked, isToggle, disabled, danger, isRound, tooltip, clicked(), …).
//
//   property string text        label
//   property string icon        optional leading Material Symbols icon
//   property real fontSize      default Theme.font.size.normal (13 pt × scale)
//   readonly property StyledText label
//
//   TextButton { text: I18n.t("ui.apply"); type: TextButton.Filled; onClicked: … }

import QtQuick
import "../services"

ButtonBase {
    id: root

    property alias text: textLabel.text
    property string icon: ""
    property real fontSize: Theme.font.size.normal
    readonly property alias label: textLabel

    horizontalPadding: Theme.padding.large
    verticalPadding: Theme.padding.small + Math.round(2 * Theme.scale)
    isRound: true

    activeColour: {
        if (danger)
            return Theme.palette.m3error;
        return type === ButtonBase.Filled ? Theme.palette.m3primary : Theme.palette.m3secondary;
    }
    inactiveColour: {
        if (danger)
            return type === ButtonBase.Filled && !isToggle ? Theme.palette.m3error : Theme.palette.m3errorContainer;
        if (!isToggle && type === ButtonBase.Filled)
            return Theme.palette.m3primary;
        return type === ButtonBase.Filled ? Theme.tPalette.m3surfaceContainer : Theme.palette.m3secondaryContainer;
    }
    activeOnColour: {
        if (danger)
            return type === ButtonBase.Text || type === ButtonBase.Outlined ? Theme.palette.m3error : Theme.palette.m3onError;
        if (type === ButtonBase.Text || type === ButtonBase.Outlined)
            return Theme.palette.m3primary;
        return type === ButtonBase.Filled ? Theme.palette.m3onPrimary : Theme.palette.m3onSecondary;
    }
    inactiveOnColour: {
        if (danger) {
            if (type === ButtonBase.Text || type === ButtonBase.Outlined)
                return Theme.palette.m3error;
            return type === ButtonBase.Filled && !isToggle ? Theme.palette.m3onError : Theme.palette.m3onErrorContainer;
        }
        if (!isToggle && type === ButtonBase.Filled)
            return Theme.palette.m3onPrimary;
        if (type === ButtonBase.Text || type === ButtonBase.Outlined)
            return Theme.palette.m3primary;
        return type === ButtonBase.Filled ? Theme.palette.m3onSurface : Theme.palette.m3onSecondaryContainer;
    }

    Accessible.name: root.tooltip !== "" ? root.tooltip : textLabel.text

    implicitWidth: row.implicitWidth + horizontalPadding * 2
    implicitHeight: Math.ceil(Math.max(textLabel.implicitHeight, leadIcon.visible ? leadIcon.implicitHeight : 0) + verticalPadding * 2)

    Row {
        id: row

        anchors.centerIn: parent
        spacing: Theme.spacing.small

        MaterialIcon {
            id: leadIcon

            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: 1
            visible: root.icon !== ""
            text: root.icon
            color: root.onColour
            size: root.fontSize * 1.25
            fill: 1
        }

        StyledText {
            id: textLabel

            anchors.verticalCenter: parent.verticalCenter
            color: root.onColour
            font.pointSize: root.fontSize
            font.weight: Font.Medium
        }
    }
}
