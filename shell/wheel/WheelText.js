// WheelText.js — translations for the wheel / HUD that keep working while the daemon is down.
//
// The shell's I18n service gets every string from the daemon (hello.strings) and only has a built-in fallback for the
// shared ui.* keys. The wheel and the HUD are visible exactly when the daemon may be missing (menu error, failed job
// placeholder), so their own wheel.* / hud.* strings are mirrored here from src/dropwheel/locales/<lang>/ui_wheel.json
// (tests/test_ui_wheel.py keeps the copies identical). Lookup order: daemon strings → this table (it/en) → the key.
//
//   import "WheelText.js" as WT            (from shell/wheel)     import "../wheel/WheelText.js" as WT   (shell/hud)
//   WT.tr(I18n, "wheel.cancel")            WT.tr(I18n, "hud.size_delta", {before: …, after: …, delta: …})
//   WT.trn(I18n, "hud.outputs_more", n)    plural: "<key>.one" when n == 1 else "<key>.other", args.count = n

.pragma library

const EN = {
    "wheel.a11y.name": "Dropwheel action wheel",
    "wheel.cancel": "Cancel",
    "wheel.drop_on_wheel": "Drop on an action",
    "wheel.release_to_open": "Release to open",
    "wheel.menu_error": "Could not load the actions",
    "wheel.retry_hint": "Click the centre or press Enter to retry",
    "wheel.no_actions": "No actions for these files",
    "wheel.install_copied": "Install command copied",
    "wheel.install_copy_failed": "Could not copy the install command",
    "wheel.click_to_copy": "Click the action to copy the command",
    "wheel.options_hint": "Right-click or Alt+Enter for options",
    "wheel.page_hint": "Shift: switch page",
    "wheel.empty_hint": "Esc or click the centre to close",
    "hud.a11y.name": "Dropwheel jobs",
    "hud.cancel": "Stop",
    "hud.dismiss": "Dismiss",
    "hud.open": "Open",
    "hud.reveal": "Show in folder",
    "hud.copy": "Copy",
    "hud.copy_files": "Copy files",
    "hud.copy_text": "Copy text",
    "hud.copy_link": "Copy link",
    "hud.copy_image": "Copy image",
    "hud.copy_details": "Copy details",
    "hud.copied": "Copied",
    "hud.copy_failed": "Could not copy",
    "hud.undo": "Undo",
    "hud.undoing": "Undoing…",
    "hud.undone": "Undone",
    "hud.expand": "Expand",
    "hud.details": "Details",
    "hud.hide_details": "Hide details",
    "hud.copy_install": "Copy install command",
    "hud.install_copied": "Install command copied",
    "hud.stop_share": "Stop sharing",
    "hud.share_stopped": "Sharing stopped",
    "hud.sharing": "Sharing on the local network",
    "hud.queued": "Queued",
    "hud.running": "Working…",
    "hud.cancelling": "Stopping…",
    "hud.cancelled": "Stopped",
    "hud.no_output": "Nothing was produced",
    "hud.size_delta": "{before} → {after} ({delta})",
    "hud.size_same": "{size} (unchanged)",
    "hud.outputs_more.one": "+{count} more file",
    "hud.outputs_more.other": "+{count} more files",
    "hud.files_more.one": "{name} and {count} other",
    "hud.files_more.other": "{name} and {count} others",
    "hud.warnings_more.one": "+{count} more warning",
    "hud.warnings_more.other": "+{count} more warnings",
    "hud.hidden_jobs.one": "{count} more job",
    "hud.hidden_jobs.other": "{count} more jobs",
    "hud.clear_finished": "Clear finished",
    "hud.drag_hint": "Drag to another app, click to open",
    "hud.info_more.one": "+{count} more field",
    "hud.info_more.other": "+{count} more fields"
};

const IT = {
    "wheel.a11y.name": "Ruota delle azioni di Dropwheel",
    "wheel.cancel": "Annulla",
    "wheel.drop_on_wheel": "Rilascia su un'azione",
    "wheel.release_to_open": "Rilascia per aprire",
    "wheel.menu_error": "Impossibile caricare le azioni",
    "wheel.retry_hint": "Fai clic al centro o premi Invio per riprovare",
    "wheel.no_actions": "Nessuna azione per questi file",
    "wheel.install_copied": "Comando di installazione copiato",
    "wheel.install_copy_failed": "Impossibile copiare il comando di installazione",
    "wheel.click_to_copy": "Fai clic sull'azione per copiare il comando",
    "wheel.options_hint": "Clic destro o Alt+Invio per le opzioni",
    "wheel.page_hint": "Maiusc: cambia pagina",
    "wheel.empty_hint": "Esc o clic al centro per chiudere",
    "hud.a11y.name": "Operazioni di Dropwheel",
    "hud.cancel": "Interrompi",
    "hud.dismiss": "Chiudi",
    "hud.open": "Apri",
    "hud.reveal": "Mostra nella cartella",
    "hud.copy": "Copia",
    "hud.copy_files": "Copia i file",
    "hud.copy_text": "Copia testo",
    "hud.copy_link": "Copia link",
    "hud.copy_image": "Copia immagine",
    "hud.copy_details": "Copia dettagli",
    "hud.copied": "Copiato",
    "hud.copy_failed": "Impossibile copiare",
    "hud.undo": "Annulla",
    "hud.undoing": "Annullamento…",
    "hud.undone": "Annullato",
    "hud.expand": "Espandi",
    "hud.details": "Dettagli",
    "hud.hide_details": "Nascondi dettagli",
    "hud.copy_install": "Copia comando di installazione",
    "hud.install_copied": "Comando di installazione copiato",
    "hud.stop_share": "Interrompi condivisione",
    "hud.share_stopped": "Condivisione interrotta",
    "hud.sharing": "Condivisione sulla rete locale",
    "hud.queued": "In coda",
    "hud.running": "In corso…",
    "hud.cancelling": "Interruzione…",
    "hud.cancelled": "Interrotto",
    "hud.no_output": "Nessun file prodotto",
    "hud.size_delta": "{before} → {after} ({delta})",
    "hud.size_same": "{size} (invariato)",
    "hud.outputs_more.one": "+{count} altro file",
    "hud.outputs_more.other": "+{count} altri file",
    "hud.files_more.one": "{name} e un altro",
    "hud.files_more.other": "{name} e altri {count}",
    "hud.warnings_more.one": "+{count} altro avviso",
    "hud.warnings_more.other": "+{count} altri avvisi",
    "hud.hidden_jobs.one": "{count} altra operazione",
    "hud.hidden_jobs.other": "{count} altre operazioni",
    "hud.clear_finished": "Chiudi completate",
    "hud.drag_hint": "Trascinalo in un'altra app, fai clic per aprirlo",
    "hud.info_more.one": "+{count} altro campo",
    "hud.info_more.other": "+{count} altri campi"
};

function format(text, args) {
    if (!args || typeof args !== "object")
        return text;
    return text.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => {
        if (!Object.prototype.hasOwnProperty.call(args, name))
            return match;
        const v = args[name];
        return (v === undefined || v === null) ? match : String(v);
    });
}

function fallback(lang, key) {
    const table = lang === "it" ? IT : EN;
    if (typeof table[key] === "string")
        return table[key];
    return typeof EN[key] === "string" ? EN[key] : null;
}

function tr(i18n, key, args) {
    const k = String(key);
    const text = i18n ? i18n.t(k, args) : k;
    if (text !== k)
        return text;
    const f = fallback(i18n ? i18n.lang : "en", k);
    return f === null ? text : format(f, args);
}

function trn(i18n, key, count, args) {
    const n = Number(count);
    const a = Object.assign({}, (args && typeof args === "object") ? args : {});
    a.count = (i18n && isFinite(n)) ? i18n.formatCount(n) : String(count);
    return tr(i18n, String(key) + (n === 1 ? ".one" : ".other"), a);
}
