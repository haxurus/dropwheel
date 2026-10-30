pragma Singleton
pragma ComponentBehavior: Bound

// I18n — translated UI strings for the shell.
//
// Strings come from Backend.strings (every locales/<lang>/*.json fragment merged by the daemon, delivered by the
// hello handshake). Until the daemon answers (or when it is down) a built-in table with the essential ui.* strings
// is used, in Italian or English according to $LC_ALL / $LC_MESSAGES / $LANG. Lookup order:
//   Backend.strings[key] → built-in table for `lang` → built-in English → the key itself.
// Calling t() inside a binding re-evaluates it when the strings arrive.
//
// PROPERTIES (read-only)
//   lang       "it" | "en"   (daemon language once known, else detected from the environment)
//   strings    var           Backend.strings
//   ready      bool          true once the daemon delivered its strings
//
// FUNCTIONS
//   t(key, args)             text with {name} placeholders replaced from args ({name: value}); unknown
//                            placeholders are left untouched
//   tn(key, count, args)     plural helper: uses "<key>.one" when count == 1, else "<key>.other";
//                            args.count is set to count
//   has(key)                 true when the key exists in the daemon strings or in the built-in table
//   formatNumber(value, decimals)   locale decimal separator ("1,5" in Italian)
//   formatSize(bytes)        GLib-style SI size: "980 bytes", "1.5 kB", "2.4 MB" ("2,4 MB" in Italian)
//   formatDuration(seconds)  "0:42", "3:05", "1:02:03"
//   formatPercent(fraction)  0.42 → "42%" ("42 %" is not used: Italian UI style is "42%")
//   formatCount(n)           integer with thousands separators ("12.345" in Italian, "12,345" in English)
//
// Shared keys provided by locales/<lang>/ui_base.json (reuse them instead of duplicating):
//   ui.ok ui.cancel ui.apply ui.close ui.back ui.more ui.open ui.open_with ui.show_in_folder ui.copy ui.copied
//   ui.copy_path ui.copy_install ui.undo ui.undone ui.retry ui.details ui.expand ui.collapse ui.dismiss ui.save
//   ui.reset ui.clear ui.search ui.loading ui.none ui.select ui.no_options ui.drop_here ui.files_count.one/.other
//   ui.install_hint ui.unavailable ui.field.{required,invalid,range,integer,number,show_password,hide_password}
//   ui.color.* ui.backend.{starting,restarting,unavailable} ui.error.{timeout,backend_exited,unexpected,
//   invalid_request,no_job} ui.job.{queued,running,done,failed,cancelled,cancel,cancelling,undo_failed} ui.size.*
// Other UI fragments should use their own prefixes (wheel.*, hud.*, dialog.*) to avoid collisions with ui.*.

import QtQuick
import Quickshell

Singleton {
    id: root

    readonly property var strings: Backend.strings ?? ({})
    readonly property bool ready: Backend.helloDone && Object.keys(root.strings).length > 0
    readonly property string lang: {
        const fromDaemon = Backend.lang;
        if (fromDaemon === "it" || fromDaemon === "en")
            return fromDaemon;
        const configured = Backend.config?.language;
        if (configured === "it" || configured === "en")
            return configured;
        return priv.envLang;
    }

    function t(key: var, args: var): string {
        const k = String(key ?? "");
        const table = root.strings;
        let text;
        if (table && typeof table[k] === "string")
            text = table[k];
        else if (typeof priv.builtin[k] === "string")
            text = priv.builtin[k];
        else if (typeof priv.en[k] === "string")
            text = priv.en[k];
        else
            text = k;
        if (args && typeof args === "object")
            text = text.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => {
                if (!Object.prototype.hasOwnProperty.call(args, name))
                    return match;
                const v = args[name];
                return (v === undefined || v === null) ? match : String(v);
            });
        return text;
    }

    function tn(key: var, count: var, args: var): string {
        const n = Number(count);
        const a = Object.assign({}, (args && typeof args === "object") ? args : {}, {
            count: isFinite(n) ? root.formatCount(n) : String(count)
        });
        return t(String(key ?? "") + (n === 1 ? ".one" : ".other"), a);
    }

    function has(key: var): bool {
        const k = String(key ?? "");
        return (root.strings && typeof root.strings[k] === "string") || typeof priv.builtin[k] === "string" || typeof priv.en[k] === "string";
    }

    function formatNumber(value: var, decimals: var): string {
        const n = Number(value);
        if (!isFinite(n))
            return "";
        const d = (typeof decimals === "number" && decimals >= 0) ? Math.min(10, Math.floor(decimals)) : 0;
        let s = n.toFixed(d);
        if (root.lang === "it")
            s = s.replace(".", ",");
        return s;
    }

    function formatCount(value: var): string {
        const n = Math.round(Number(value));
        if (!isFinite(n))
            return "";
        const neg = n < 0;
        const digits = String(Math.abs(n));
        const sep = root.lang === "it" ? "." : ",";
        let out = "";
        for (let i = 0; i < digits.length; i++) {
            if (i > 0 && (digits.length - i) % 3 === 0)
                out += sep;
            out += digits[i];
        }
        return (neg ? "-" : "") + out;
    }

    function formatSize(bytes: var): string {
        const n = Number(bytes);
        if (!isFinite(n) || n < 0)
            return "";
        if (n < 1000)
            return t(Math.round(n) === 1 ? "ui.size.byte" : "ui.size.bytes", {
                n: Math.round(n)
            });
        const units = ["kB", "MB", "GB", "TB", "PB", "EB"];
        let v = n / 1000;
        let i = 0;
        while (v >= 1000 && i < units.length - 1) {
            v /= 1000;
            i++;
        }
        // Avoid "1000.0 kB" from rounding: promote to the next unit.
        if (Number(v.toFixed(1)) >= 1000 && i < units.length - 1) {
            v /= 1000;
            i++;
        }
        return formatNumber(v, 1) + " " + units[i];
    }

    function formatDuration(seconds: var): string {
        const total = Math.floor(Number(seconds));
        if (!isFinite(total) || total < 0)
            return "";
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        const ss = s < 10 ? "0" + s : String(s);
        if (h > 0)
            return h + ":" + (m < 10 ? "0" + m : String(m)) + ":" + ss;
        return m + ":" + ss;
    }

    function formatPercent(fraction: var): string {
        const f = Number(fraction);
        if (!isFinite(f))
            return "";
        return Math.round(Math.max(0, Math.min(1, f)) * 100) + "%";
    }

    QtObject {
        id: priv

        readonly property string envLang: {
            for (const name of ["LC_ALL", "LC_MESSAGES", "LANG"]) {
                const v = Quickshell.env(name);
                if (typeof v === "string" && v !== "")
                    return v.toLowerCase().startsWith("it") ? "it" : "en";
            }
            return "en";
        }

        readonly property var builtin: root.lang === "it" ? priv.it : priv.en

        // Essential strings, kept in sync with src/dropwheel/locales/<lang>/ui_base.json (checked by
        // tests/test_ui_base.py). Everything else comes from the daemon.
        readonly property var en: ({
                "ui.ok": "OK",
                "ui.cancel": "Cancel",
                "ui.apply": "Apply",
                "ui.close": "Close",
                "ui.back": "Back",
                "ui.more": "More",
                "ui.open": "Open",
                "ui.show_in_folder": "Show in folder",
                "ui.copy": "Copy",
                "ui.copied": "Copied",
                "ui.copy_install": "Copy install command",
                "ui.undo": "Undo",
                "ui.retry": "Retry",
                "ui.details": "Details",
                "ui.dismiss": "Dismiss",
                "ui.clear": "Clear",
                "ui.loading": "Loading…",
                "ui.select": "Select…",
                "ui.no_options": "No options",
                "ui.drop_here": "Drop files here",
                "ui.files_count.one": "{count} file",
                "ui.files_count.other": "{count} files",
                "ui.install_hint": "Install with: {command}",
                "ui.unavailable": "Not available",
                "ui.field.required": "Required",
                "ui.field.invalid": "Invalid value",
                "ui.field.show_password": "Show password",
                "ui.field.hide_password": "Hide password",
                "ui.color.hex": "Hex colour",
                "ui.color.invalid": "Enter a colour like #1e88e5",
                "ui.color.eyedropper": "Pick a colour from the screen",
                "ui.color.eyedropper_missing": "Install hyprpicker to pick colours from the screen (sudo pacman -S hyprpicker)",
                "ui.color.theme": "Theme",
                "ui.color.basic": "Basic",
                "ui.backend.starting": "Starting Dropwheel…",
                "ui.backend.restarting": "Restarting the Dropwheel service…",
                "ui.backend.unavailable": "The Dropwheel service is not running",
                "ui.error.timeout": "The request timed out ({method})",
                "ui.error.backend_exited": "The Dropwheel service stopped unexpectedly",
                "ui.error.unexpected": "Unexpected error",
                "ui.error.invalid_request": "Invalid request",
                "ui.error.no_job": "The job could not be started",
                "ui.job.queued": "Queued",
                "ui.job.running": "Working…",
                "ui.job.done": "Done",
                "ui.job.failed": "Failed",
                "ui.job.cancelled": "Stopped",
                "ui.job.cancel": "Stop",
                "ui.job.cancelling": "Stopping…",
                "ui.job.undo_failed": "Could not undo",
                "ui.size.byte": "{n} byte",
                "ui.size.bytes": "{n} bytes"
            })

        readonly property var it: ({
                "ui.ok": "OK",
                "ui.cancel": "Annulla",
                "ui.apply": "Applica",
                "ui.close": "Chiudi",
                "ui.back": "Indietro",
                "ui.more": "Altro",
                "ui.open": "Apri",
                "ui.show_in_folder": "Mostra nella cartella",
                "ui.copy": "Copia",
                "ui.copied": "Copiato",
                "ui.copy_install": "Copia comando di installazione",
                "ui.undo": "Annulla",
                "ui.retry": "Riprova",
                "ui.details": "Dettagli",
                "ui.dismiss": "Chiudi",
                "ui.clear": "Cancella",
                "ui.loading": "Caricamento…",
                "ui.select": "Seleziona…",
                "ui.no_options": "Nessuna opzione",
                "ui.drop_here": "Trascina qui dei file",
                "ui.files_count.one": "{count} file",
                "ui.files_count.other": "{count} file",
                "ui.install_hint": "Installa con: {command}",
                "ui.unavailable": "Non disponibile",
                "ui.field.required": "Obbligatorio",
                "ui.field.invalid": "Valore non valido",
                "ui.field.show_password": "Mostra password",
                "ui.field.hide_password": "Nascondi password",
                "ui.color.hex": "Colore esadecimale",
                "ui.color.invalid": "Inserisci un colore come #1e88e5",
                "ui.color.eyedropper": "Preleva un colore dallo schermo",
                "ui.color.eyedropper_missing": "Installa hyprpicker per prelevare colori dallo schermo (sudo pacman -S hyprpicker)",
                "ui.color.theme": "Tema",
                "ui.color.basic": "Base",
                "ui.backend.starting": "Avvio di Dropwheel…",
                "ui.backend.restarting": "Riavvio del servizio Dropwheel…",
                "ui.backend.unavailable": "Il servizio Dropwheel non è in esecuzione",
                "ui.error.timeout": "Tempo scaduto per la richiesta ({method})",
                "ui.error.backend_exited": "Il servizio Dropwheel si è interrotto inaspettatamente",
                "ui.error.unexpected": "Errore imprevisto",
                "ui.error.invalid_request": "Richiesta non valida",
                "ui.error.no_job": "Impossibile avviare l'operazione",
                "ui.job.queued": "In coda",
                "ui.job.running": "In corso…",
                "ui.job.done": "Fatto",
                "ui.job.failed": "Non riuscito",
                "ui.job.cancelled": "Interrotto",
                "ui.job.cancel": "Interrompi",
                "ui.job.cancelling": "Interruzione…",
                "ui.job.undo_failed": "Impossibile annullare",
                "ui.size.byte": "{n} byte",
                "ui.size.bytes": "{n} byte"
            })
    }
}
