--[[
  Dropwheel — integrazione con Hyprland (>= 0.57, configurazione Lua) e Caelestia
  ================================================================================

  Cosa fa questo file
  -------------------
  1. Collega i tasti Shift (sinistro e destro) alla scorciatoia globale
     "dropwheel:shift": premendo Shift MENTRE trascini dei file compare la ruota.
     Il tasto continua ad arrivare anche all'applicazione (non_consuming), quindi
     Shift si comporta come sempre in tutti gli altri casi.
  2. Collega SUPER + ALT + D (modificabile qui sotto) alla scorciatoia globale
     "dropwheel:open": apre la ruota sotto il puntatore con i file copiati negli
     appunti (o un'area vuota su cui trascinare dei file).
  3. Aggiunge le regole dei layer per le superfici di Dropwheel:
       - dropwheel-wheel, dropwheel-hud → sfocatura dello sfondo
         (blur + ignore_alpha 0.3) e nessuna animazione di Hyprland (le animazioni
         le disegna Dropwheel stesso);
       - dropwheel-dialog (a schermo intero, con un proprio velo) e dropwheel-hotzone
         (l'angolo attivo di 6×6 px) → nessuna animazione e nessuna sfocatura.
  4. Avvia Dropwheel all'avvio di Hyprland con "dropwheel start".

  Come si attiva
  --------------
  Con Caelestia basta eseguire una volta:

      dropwheel setup hyprland

  che copia questo file in ~/.config/caelestia/dropwheel.lua e aggiunge a
  ~/.config/caelestia/hypr-user.lua la riga:

      require("dropwheel")

  (Caelestia aggiunge già ~/.config/caelestia/?.lua a package.path, quindi
  require lo trova da solo.) Senza Caelestia, copia il file in ~/.config/hypr/ e
  aggiungi a ~/.config/hypr/hyprland.lua:

      package.path = package.path .. ";" .. os.getenv("HOME") .. "/.config/hypr/?.lua"
      require("dropwheel")

  Nota: l'evento "hyprland.start" scatta UNA sola volta, al login, e non quando
  la configurazione viene ricaricata. La prima volta avvia Dropwheel a mano con
  "dropwheel start" (oppure esci e rientra nella sessione).

  Il modulo restituisce una tabella (vedi in fondo) con gli handle delle
  scorciatoie e delle regole, utile per disattivarle da Lua:

      local dropwheel = require("dropwheel")
      dropwheel.set_enabled(false)   -- disattiva scorciatoie e regole
]]

-- ════════════════════════════════════════════════════════════════════════════
--  IMPOSTAZIONI — modifica solo questa sezione
-- ════════════════════════════════════════════════════════════════════════════

-- Scorciatoia che apre la ruota senza trascinare nulla (usa i file negli appunti).
-- Sintassi di Hyprland: modificatori prima, tasto per ultimo, separati da " + ".
-- Esempi: "SUPER + ALT + D", "SUPER + D", "CTRL + ALT + W".
-- Metti nil per non creare questa scorciatoia.
local OPEN_KEY = "SUPER + ALT + D"

-- Tasti che, premuti durante un trascinamento, aprono la ruota.
-- Servono due scorciatoie distinte per Shift sinistro e destro.
-- Lascia la tabella vuota ({}) per usare solo l'angolo attivo e OPEN_KEY.
local SHIFT_KEYS = { "Shift_L", "Shift_R" }

-- Avvia Dropwheel automaticamente quando parte Hyprland.
local AUTOSTART = true

-- Sfoca lo sfondo dietro la ruota e l'HUD.
local BLUR = true

-- Comando da usare per "dropwheel". nil = rilevamento automatico:
-- ~/.local/bin/dropwheel (installazione con install.sh) se esiste, altrimenti
-- "dropwheel" cercato nel PATH (pacchetto di sistema: /usr/bin/dropwheel).
-- Il valore viene eseguito con sh -c: racchiudi tra apici i percorsi con spazi.
-- Esempio: local DROPWHEEL_CMD = "/opt/dropwheel/bin/dropwheel"
local DROPWHEEL_CMD = nil

-- ════════════════════════════════════════════════════════════════════════════
--  Da qui in poi non serve modificare nulla
-- ════════════════════════════════════════════════════════════════════════════

local M = {
  binds = {},    -- handle restituiti da hl.bind
  rules = {},    -- handle restituiti da hl.layer_rule
  errors = {},   -- messaggi di errore raccolti durante il caricamento
  open_key = OPEN_KEY,
  shift_keys = SHIFT_KEYS,
  command = nil, -- comando "dropwheel" effettivamente usato
}

-- Lingua delle descrizioni mostrate da "hyprctl binds": italiano se la sessione
-- è in italiano, altrimenti inglese.
local function session_is_italian()
  if type(os) ~= "table" or type(os.getenv) ~= "function" then
    return false
  end
  local lang = os.getenv("LC_ALL")
  if lang == nil or lang == "" then lang = os.getenv("LC_MESSAGES") end
  if lang == nil or lang == "" then lang = os.getenv("LANG") end
  return type(lang) == "string" and lang:sub(1, 2) == "it"
end

local IT = session_is_italian()

local function tr(italian, english)
  if IT then return italian end
  return english
end

-- Racchiude una stringa tra apici singoli per sh -c (gli apici interni diventano '\'').
local function shell_quote(text)
  local escaped = text:gsub("'", "'\\''")
  return "'" .. escaped .. "'"
end

-- true se il file esiste ed è leggibile (io può mancare in ambienti ridotti).
local function file_exists(path)
  if type(io) ~= "table" or type(io.open) ~= "function" then
    return false
  end
  local ok, handle = pcall(io.open, path, "r")
  if ok and handle then
    pcall(function() handle:close() end)
    return true
  end
  return false
end

local function detect_command()
  if type(DROPWHEEL_CMD) == "string" and DROPWHEEL_CMD ~= "" then
    return DROPWHEEL_CMD
  end
  local home = nil
  if type(os) == "table" and type(os.getenv) == "function" then
    home = os.getenv("HOME")
  end
  if type(home) == "string" and home ~= "" then
    local user_bin = home .. "/.local/bin/dropwheel"
    if file_exists(user_bin) then
      return shell_quote(user_bin)
    end
  end
  return "dropwheel"
end

-- Registra un errore senza interrompere il resto della configurazione di Hyprland:
-- un errore Lua qui fermerebbe anche le righe successive di hypr-user.lua.
local function report(what, err)
  local message = "Dropwheel: " .. what .. ": " .. tostring(err)
  table.insert(M.errors, message)
  if type(print) == "function" then
    pcall(print, message)
  end
end

-- Crea una scorciatoia che invia la scorciatoia globale "dropwheel:<nome>".
-- Tutto avviene dentro pcall, compresa la creazione del dispatcher.
local function add_global_bind(keys, shortcut, opts)
  local ok, handle = pcall(function()
    return hl.bind(keys, hl.dsp.global(shortcut), opts)
  end)
  if not ok then
    report(tr("scorciatoia non valida", "invalid keybind") .. " \"" .. tostring(keys) .. "\"", handle)
    return
  end
  if handle ~= nil then
    table.insert(M.binds, handle)
  end
end

local function add_layer_rule(rule)
  local ok, handle = pcall(hl.layer_rule, rule)
  if not ok then
    report(tr("regola dei layer non valida", "invalid layer rule") .. " \"" .. tostring(rule.name) .. "\"", handle)
    return
  end
  if handle ~= nil then
    table.insert(M.rules, handle)
  end
end

--- Attiva o disattiva tutte le scorciatoie e le regole di Dropwheel.
--- @param enabled boolean
function M.set_enabled(enabled)
  local on = enabled and true or false
  for _, handle in ipairs(M.binds) do
    pcall(function() handle:set_enabled(on) end)
  end
  for _, handle in ipairs(M.rules) do
    pcall(function() handle:set_enabled(on) end)
  end
end

-- Fuori da Hyprland (per esempio nei test) la tabella hl non esiste: niente da fare.
if type(hl) ~= "table" then
  return M
end

M.command = detect_command()

-- ── 1. Shift durante il trascinamento ───────────────────────────────────────
-- hl.dsp.global invia sia la pressione sia il rilascio alla GlobalShortcut di
-- Quickshell (appid "dropwheel", nome "shift"); non fa nulla se Dropwheel non è
-- in esecuzione. non_consuming: l'applicazione riceve comunque Shift.
-- ignore_mods: funziona anche se durante il trascinamento tieni premuto Ctrl o Alt.
-- Se nella configurazione esiste una scorciatoia "SHIFT + qualcosa", Hyprland può
-- rimandare questa al rilascio di Shift (risoluzione degli accordi): Dropwheel
-- gestisce entrambi i casi.
for _, key in ipairs(SHIFT_KEYS) do
  add_global_bind(key, "dropwheel:shift", {
    non_consuming = true,
    ignore_mods = true,
    description = tr(
      "Dropwheel: Shift durante il trascinamento apre la ruota",
      "Dropwheel: Shift while dragging opens the wheel"
    ),
  })
end

-- ── 2. Apertura manuale ─────────────────────────────────────────────────────
if type(OPEN_KEY) == "string" and OPEN_KEY ~= "" then
  add_global_bind(OPEN_KEY, "dropwheel:open", {
    description = tr(
      "Dropwheel: apri la ruota con i file negli appunti",
      "Dropwheel: open the wheel with the files in the clipboard"
    ),
  })
end

-- ── 3. Regole dei layer ─────────────────────────────────────────────────────
-- I namespace sono confrontati con una regex ancorata (RE2 FullMatch), quindi non
-- toccano i layer di Caelestia (caelestia-*).
add_layer_rule({
  name = "dropwheel-surfaces",
  match = { namespace = "dropwheel-(wheel|hud)" },
  blur = BLUR and true or false,
  ignore_alpha = 0.3,
  no_anim = true,
})

-- Il dialogo è un layer a schermo intero con un proprio velo semitrasparente e una
-- propria animazione: niente sfocatura (sfocherebbe tutto lo schermo) e niente
-- animazione di Hyprland. L'angolo attivo è un quadratino di 6×6 px: niente animazione.
add_layer_rule({
  name = "dropwheel-plain",
  match = { namespace = "dropwheel-(dialog|hotzone)" },
  no_anim = true,
})

-- ── 4. Avvio automatico ─────────────────────────────────────────────────────
-- hl.exec_cmd non blocca (esegue il comando con sh -c in background); l'output
-- finisce in /dev/null: per i log usa "qs log -p <cartella shell>".
if AUTOSTART then
  local start_cmd = M.command .. " start"
  local ok, err = pcall(hl.on, "hyprland.start", function()
    hl.exec_cmd(start_cmd)
  end)
  if not ok then
    report(tr("avvio automatico non registrato", "autostart not registered"), err)
  end
end

-- Se qualcosa è andato storto lo segnala con una notifica, senza bloccare Hyprland.
if #M.errors > 0 and type(hl.exec_cmd) == "function" then
  local body = table.concat(M.errors, "\n")
  pcall(hl.exec_cmd, "notify-send -a Dropwheel -u critical "
    .. shell_quote(tr("Dropwheel: errore nella configurazione di Hyprland",
                      "Dropwheel: Hyprland configuration error"))
    .. " " .. shell_quote(body))
end

return M
