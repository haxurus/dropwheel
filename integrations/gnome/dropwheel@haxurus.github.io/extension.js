// Dropwheel — GNOME Shell extension (GNOME 45 or later).
//
// GNOME on Wayland does not tell ordinary programs where the pointer is or which modifiers are
// held. Dropwheel's window (dropwheel.qtshell.gnome) asks this extension instead, about forty
// times a second, through one D-Bus method on the session bus:
//
//   io.github.haxurus.Dropwheel.GetPointer() → (x, y, mods)
//
// x and y are global stage coordinates, mods is the Clutter modifier mask (Shift = 1, buttons
// 1–3 = 256, 512, 1024). No key is ever recorded: only the current state, when asked.

import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.haxurus.Dropwheel';
const OBJECT_PATH = '/io/github/haxurus/Dropwheel';
const IFACE = `
<node>
  <interface name="io.github.haxurus.Dropwheel">
    <method name="GetPointer">
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
      <arg type="u" direction="out" name="mods"/>
    </method>
  </interface>
</node>`;

export default class DropwheelExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            null, null, null);
    }

    disable() {
        if (this._owner) {
            Gio.bus_unown_name(this._owner);
            this._owner = 0;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    GetPointer() {
        const [x, y, mods] = global.get_pointer();
        return [Math.round(x), Math.round(y), mods >>> 0];
    }
}
