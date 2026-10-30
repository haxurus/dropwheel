// IconButton — square icon button in Caelestia style (filled / tonal / text / outlined), radius morph on press,
// optional toggle with animated icon fill. See ButtonBase for the shared API (type, checked, isToggle, disabled,
// danger, isRound, tooltip, clicked(), trigger(), internalChecked, onColour, activeFocusOnTab…).
//
//   property string icon       Material Symbols name
//   property real iconSize     point size (default Theme.font.iconSize.medium = 18 pt × scale)
//   readonly property MaterialIcon label
//
//   IconButton { icon: "close"; type: IconButton.Text; tooltip: I18n.t("ui.close"); onClicked: … }

import QtQuick
import "../services"

ButtonBase {
    id: root

    property alias icon: iconLabel.text
    readonly property alias label: iconLabel
    property real iconSize: Theme.font.iconSize.medium

    padding: type === ButtonBase.Text ? Theme.padding.extraSmall : Theme.padding.small

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
        if (type === ButtonBase.Filled)
            return Theme.palette.m3onPrimary;
        if (type === ButtonBase.Tonal)
            return Theme.palette.m3onSecondary;
        return Theme.palette.m3primary;
    }
    inactiveOnColour: {
        if (danger) {
            if (type === ButtonBase.Text || type === ButtonBase.Outlined)
                return Theme.palette.m3error;
            return type === ButtonBase.Filled && !isToggle ? Theme.palette.m3onError : Theme.palette.m3onErrorContainer;
        }
        if (!isToggle && type === ButtonBase.Filled)
            return Theme.palette.m3onPrimary;
        return type === ButtonBase.Tonal ? Theme.palette.m3onSecondaryContainer : Theme.palette.m3onSurfaceVariant;
    }

    implicitWidth: implicitHeight
    implicitHeight: {
        // Keep the size even so the glyph is centred on whole pixels.
        const h = Math.ceil(iconLabel.implicitHeight + root.padding * 2);
        return h % 2 !== 0 ? h + 1 : h;
    }

    MaterialIcon {
        id: iconLabel

        anchors.centerIn: parent
        anchors.verticalCenterOffset: 1
        color: root.onColour
        size: root.iconSize
        fill: !root.isToggle || root.internalChecked ? 1 : 0

        Behavior on fill {
            Anim {
                type: Anim.DefaultEffects
            }
        }
    }
}
