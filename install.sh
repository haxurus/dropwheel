#!/usr/bin/env bash
# Dropwheel — user-local installer (no root, nothing outside your home directory).
#
#   ./install.sh                 install into ~/.local, then offer the Hyprland setup and start
#   ./install.sh --help          all options
#
# Layout (see docs/SPEC.md §8):
#   ~/.local/share/dropwheel/python/dropwheel   Python package
#   ~/.local/share/dropwheel/shell/             Quickshell shell (launched by path)
#   ~/.local/share/dropwheel/integrations/      Hyprland, file-manager and desktop files
#   ~/.local/bin/dropwheel                      launcher (python3 -m dropwheel, PYTHONPATH set)
#   ~/.local/share/applications/dropwheel.desktop, ~/.local/share/icons/hicolor/…/dropwheel.svg
#
# The script never runs sudo: missing packages are listed with the command to install them.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly SCRIPT_DIR

# --------------------------------------------------------------------------------------------
# Language, colours, messages
# --------------------------------------------------------------------------------------------

_locale=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
if [[ $_locale == it* ]]; then IT=1; else IT=0; fi

# t "italiano" "english" → the text in the session language
t() { if (( IT )); then printf '%s' "$1"; else printf '%s' "$2"; fi; }

if [[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; then
    C_RESET=$'\e[0m' C_BOLD=$'\e[1m' C_RED=$'\e[1;31m' C_GREEN=$'\e[1;32m'
    C_YELLOW=$'\e[1;33m' C_BLUE=$'\e[1;34m' C_DIM=$'\e[2m'
else
    C_RESET='' C_BOLD='' C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_DIM=''
fi

step() { printf '\n%s==>%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; exit 1; }
cmd()  { printf '      %s$ %s%s\n' "$C_DIM" "$*" "$C_RESET"; }

usage() {
    if (( IT )); then
        cat <<'EOF'
Uso: ./install.sh [opzioni]

Installa Dropwheel per l'utente corrente (in ~/.local), senza permessi di root.

Opzioni:
  --prefix DIR         cartella di installazione (predefinita: ~/.local)
  --python PERCORSO    interprete Python da usare (predefinito: /usr/bin/python3)
  --no-hyprland        non proporre "dropwheel setup hyprland"
  --no-start           non avviare Dropwheel al termine
  --no-file-managers   non installare le azioni per Nautilus, Dolphin e Nemo
  --no-mime-guard      non proteggere le applicazioni predefinite dei tipi di file
  -y, --yes            rispondi sì a tutte le domande
  --uninstall [...]    esegue ./uninstall.sh con le opzioni che seguono (es. --purge)
  -h, --help           mostra questo aiuto

Dopo l'installazione: "dropwheel doctor" controlla gli strumenti opzionali.
EOF
    else
        cat <<'EOF'
Usage: ./install.sh [options]

Installs Dropwheel for the current user (into ~/.local), without root.

Options:
  --prefix DIR         installation prefix (default: ~/.local)
  --python PATH        Python interpreter to use (default: /usr/bin/python3)
  --no-hyprland        do not offer "dropwheel setup hyprland"
  --no-start           do not start Dropwheel at the end
  --no-file-managers   do not install the Nautilus, Dolphin and Nemo actions
  --no-mime-guard      do not protect the default applications of file types
  -y, --yes            answer yes to every question
  --uninstall [...]    run ./uninstall.sh with the options that follow (e.g. --purge)
  -h, --help           show this help

After installing, "dropwheel doctor" checks the optional tools.
EOF
    fi
}

# ask "question" <default y|n> <answer when not interactive y|n> → status 0 = yes
ask() {
    local question=$1 default=$2 noninteractive=$3 hint reply
    if (( ASSUME_YES )); then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        [[ $noninteractive == y ]]
        return
    fi
    if [[ $default == y ]]; then hint=$(t '[S/n]' '[Y/n]'); else hint=$(t '[s/N]' '[y/N]'); fi
    while true; do
        printf '  %s?%s %s %s ' "$C_YELLOW" "$C_RESET" "$question" "$hint"
        read -r reply || reply=""
        reply=${reply,,}
        case $reply in
            '') [[ $default == y ]]; return ;;
            s|si|sì|y|yes) return 0 ;;
            n|no) return 1 ;;
            *) info "$(t 'Rispondi s oppure n.' 'Please answer y or n.')" ;;
        esac
    done
}

# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------

TMP_PATHS=()
cleanup() {
    local p
    for p in "${TMP_PATHS[@]+"${TMP_PATHS[@]}"}"; do
        [[ -n $p && -e $p ]] && rm -rf -- "$p"
    done
    return 0
}
trap cleanup EXIT

# Single-quote a string for POSIX sh.
sq() {
    local s=${1//\'/\'\\\'\'}
    printf "'%s'" "$s"
}

# Quote one argument for the Exec key of a desktop entry (Desktop Entry Spec, "Exec key").
desktop_exec_arg() {
    local s=$1 pct='%'
    if [[ $s =~ ^[A-Za-z0-9._/+@-]+$ ]]; then
        printf '%s' "$s"
        return
    fi
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//\`/\\\`}
    s=${s//\$/\\\$}
    s=${s//"$pct"/%%}
    s=${s//\\/\\\\}      # string-level escaping applies on top of the Exec quoting
    printf '"%s"' "$s"
}

# Write content to a file atomically (temporary file in the same directory + rename).
write_file() {
    local dst=$1 content=$2 mode=${3:-0644} dir tmp
    dir=$(dirname -- "$dst")
    mkdir -p -- "$dir"
    tmp=$(mktemp "$dir/.dropwheel-install.XXXXXX")
    TMP_PATHS+=("$tmp")
    printf '%s' "$content" > "$tmp"
    chmod "$mode" "$tmp"
    mv -f -- "$tmp" "$dst"
}

# Copy <src_parent>/<name> to <dest_parent>/<name>, replacing the old copy only once the new
# one is complete. Byte-code and editor files are skipped.
sync_dir() {
    local src_parent=$1 name=$2 dest_parent=$3 stage old
    mkdir -p -- "$dest_parent"
    stage=$(mktemp -d "$dest_parent/.${name}.new.XXXXXX")
    TMP_PATHS+=("$stage")
    tar -C "$src_parent" --exclude=__pycache__ --exclude='*.pyc' --exclude='.qmlls.ini' \
        --exclude='*.qmlc' --exclude='*~' -cf - "$name" | tar -C "$stage" -xf -
    old="$dest_parent/.$name.old"
    rm -rf -- "$old"
    if [[ -e $dest_parent/$name || -L $dest_parent/$name ]]; then
        mv -- "$dest_parent/$name" "$old"
    fi
    if ! mv -- "$stage/$name" "$dest_parent/$name"; then
        # Put the previous copy back so that a failed update leaves a working install.
        if [[ -e $old ]]; then
            mv -- "$old" "$dest_parent/$name" || true
        fi
        die "$(t 'Impossibile installare' 'Cannot install') $dest_parent/$name"
    fi
    rm -rf -- "$stage" "$old"
}

# Copy a launcher file (desktop entry, service menu, Nemo action) pointing it at the absolute
# path of the dropwheel command, so that it works even when ~/.local/bin is not in PATH.
#   install_launcher <src> <dst> <mode> <exec-style: desktop|shell>
install_launcher() {
    local src=$1 dst=$2 mode=$3 style=$4 exec_cmd plain line out=""
    if [[ $style == desktop ]]; then
        exec_cmd=$(desktop_exec_arg "$DW_BIN")
    else
        exec_cmd=$(sq "$DW_BIN")
    fi
    plain=${DW_BIN//\\/\\\\}
    while IFS= read -r line || [[ -n $line ]]; do
        case $line in
            'Exec=dropwheel '*) line="Exec=$exec_cmd ${line#Exec=dropwheel }" ;;
            'Exec=dropwheel') line="Exec=$exec_cmd" ;;
            'TryExec=dropwheel') line="TryExec=$plain" ;;
            'Dependencies=dropwheel;') line="Dependencies=$plain;" ;;
        esac
        out+=$line$'\n'
    done < "$src"
    write_file "$dst" "$out" "$mode"
    MANIFEST_PATHS+=("$dst")
}

pkg_installed() {
    pacman -Qq -- "$1" >/dev/null 2>&1
}

# "a/b" = install a, but b also satisfies the need.
alternative_installed() {
    local alt
    local -a alts
    IFS='/' read -r -a alts <<< "$1"
    for alt in "${alts[@]}"; do
        pkg_installed "$alt" && return 0
    done
    return 1
}

in_hyprland_session() {
    [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} && -n ${WAYLAND_DISPLAY:-} ]]
}

# --------------------------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------------------------

PREFIX="${HOME:?HOME is not set}/.local"
PYTHON_BIN=""
DO_HYPRLAND=1
DO_START=1
DO_FILE_MANAGERS=1
DO_MIME_GUARD=1
ASSUME_YES=0

while (( $# )); do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --prefix) [[ $# -ge 2 ]] || die "$(t '--prefix richiede una cartella' '--prefix needs a directory')"; PREFIX=$2; shift 2 ;;
        --prefix=*) PREFIX=${1#*=}; shift ;;
        --python) [[ $# -ge 2 ]] || die "$(t '--python richiede un percorso' '--python needs a path')"; PYTHON_BIN=$2; shift 2 ;;
        --python=*) PYTHON_BIN=${1#*=}; shift ;;
        --no-hyprland) DO_HYPRLAND=0; shift ;;
        --no-start) DO_START=0; shift ;;
        --no-file-managers) DO_FILE_MANAGERS=0; shift ;;
        --no-mime-guard) DO_MIME_GUARD=0; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        --uninstall)
            shift
            uninstall_args=(--prefix "$PREFIX")
            (( ASSUME_YES )) && uninstall_args+=(--yes)
            exec bash "$SCRIPT_DIR/uninstall.sh" "${uninstall_args[@]}" "$@"
            ;;
        *) die "$(t 'Opzione sconosciuta' 'Unknown option'): $1 ($(t 'vedi --help' 'see --help'))" ;;
    esac
done

PREFIX=${PREFIX/#\~/$HOME}
[[ $PREFIX == /* ]] || PREFIX="$PWD/$PREFIX"
PREFIX=${PREFIX%/}

BIN_DIR="$PREFIX/bin"
DATA_DIR="$PREFIX/share/dropwheel"
PY_DIR="$DATA_DIR/python"
APP_DIR="$PREFIX/share/applications"
ICON_ROOT="$PREFIX/share/icons/hicolor"
ICON_DIR="$ICON_ROOT/scalable/apps"
MANIFEST="$DATA_DIR/install-manifest.txt"
DW_BIN="$BIN_DIR/dropwheel"
XDG_DATA_HOME_DIR=${XDG_DATA_HOME:-$HOME/.local/share}
XDG_CONFIG_HOME_DIR=${XDG_CONFIG_HOME:-$HOME/.config}
MANIFEST_PATHS=()

# --------------------------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------------------------

printf '%sDropwheel%s — %s\n' "$C_BOLD" "$C_RESET" "$(t 'installazione per l'"'"'utente' 'user-local installation')"

[[ $(uname -s) == Linux ]] || die "$(t 'Dropwheel funziona solo su Linux (Hyprland).' 'Dropwheel only runs on Linux (Hyprland).')"
if (( EUID == 0 )); then
    die "$(t 'Non eseguire install.sh come root: installa per il tuo utente. Per un pacchetto di sistema usa "makepkg -si".' \
             'Do not run install.sh as root: it installs for your user. For a system package use "makepkg -si".')"
fi

for required in src/dropwheel/__init__.py src/dropwheel/__main__.py shell/shell.qml \
                integrations/hyprland/dropwheel.lua integrations/desktop/dropwheel.desktop \
                assets/dropwheel.svg uninstall.sh; do
    [[ -e $SCRIPT_DIR/$required ]] || die "$(t 'File mancante nel sorgente' 'Missing file in the source tree'): $required"
done

if [[ -z $PYTHON_BIN ]]; then
    if [[ -x /usr/bin/python3 ]]; then
        PYTHON_BIN=/usr/bin/python3
    else
        PYTHON_BIN=$(command -v python3 || true)
    fi
fi
[[ -n $PYTHON_BIN && -x $PYTHON_BIN ]] || die "$(t 'Python 3 non trovato: sudo pacman -S python' 'Python 3 not found: sudo pacman -S python')"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    die "$(t 'Serve Python 3.11 o più recente' 'Python 3.11 or newer is required'): $("$PYTHON_BIN" -V 2>&1)"
fi

# --------------------------------------------------------------------------------------------
# Dependencies (pacman -Qq; nothing is installed automatically)
# --------------------------------------------------------------------------------------------

REQUIRED_PKGS=(python python-pillow quickshell hyprland wl-clipboard libnotify
               ttf-material-symbols-variable glib2 xdg-utils)

# "Italian label|English label|packages" ("a/b" = suggest a, b also counts as installed)
OPTIONAL_GROUPS=(
    "Immagini|Images|python-pillow-heif python-pillow-jpegxl-plugin imagemagick librsvg oxipng pngquant jpegoptim libjpeg-turbo gifsicle gifski"
    "Metadati|Metadata|perl-image-exiftool mat2"
    "PDF|PDF|python-pymupdf python-pypdf qpdf ghostscript poppler"
    "OCR e codici QR|OCR and QR codes|tesseract tesseract-data-ita tesseract-data-eng zbar python-qrcode"
    "Documenti|Documents|libreoffice-fresh/libreoffice-still pandoc-cli typst python-openpyxl python-charset-normalizer python-yaml python-tomli-w python-pygments"
    "Audio e video|Audio and video|ffmpeg python-mutagen whisper-cpp"
    "Archivi|Archives|7zip unrar"
    "Font|Fonts|python-fonttools python-brotli"
    "Condivisione e sistema|Sharing and system|kdeconnect zenity gnupg trash-cli udisks2"
)
AUR_SUGGESTED=(ocrmypdf python-pdf2docx)

missing_required=()
missing_optional=()
missing_aur=()

step "$(t 'Controllo le dipendenze' 'Checking dependencies')"
if command -v pacman >/dev/null 2>&1; then
    for p in "${REQUIRED_PKGS[@]}"; do
        pkg_installed "$p" || missing_required+=("$p")
    done
    if (( ${#missing_required[@]} == 0 )); then
        ok "$(t 'Dipendenze obbligatorie presenti' 'All required dependencies are installed')"
    else
        warn "$(t 'Dipendenze obbligatorie mancanti' 'Missing required dependencies'): ${missing_required[*]}"
        cmd "sudo pacman -S --needed ${missing_required[*]}"
    fi

    printed_header=0
    for group in "${OPTIONAL_GROUPS[@]}"; do
        IFS='|' read -r label_it label_en pkgs <<< "$group"
        group_missing=()
        for p in $pkgs; do
            alternative_installed "$p" || group_missing+=("${p%%/*}")
        done
        (( ${#group_missing[@]} )) || continue
        if (( ! printed_header )); then
            info "$(t 'Funzioni opzionali non ancora disponibili (installa solo quello che ti serve):' \
                      'Optional features not available yet (install only what you need):')"
            printed_header=1
        fi
        printf '      %s%-24s%s %s\n' "$C_BOLD" "$(t "$label_it" "$label_en")" "$C_RESET" "${group_missing[*]}"
        missing_optional+=("${group_missing[@]}")
    done
    if (( ${#missing_optional[@]} )); then
        cmd "sudo pacman -S --needed ${missing_optional[*]}"
    else
        ok "$(t 'Tutte le dipendenze opzionali consigliate sono presenti' 'All suggested optional dependencies are installed')"
    fi

    for p in "${AUR_SUGGESTED[@]}"; do
        pkg_installed "$p" || missing_aur+=("$p")
    done
    if (( ${#missing_aur[@]} )); then
        aur_helper=paru
        if ! command -v paru >/dev/null 2>&1 && command -v yay >/dev/null 2>&1; then
            aur_helper=yay
        fi
        info "$(t 'Dall'"'"'AUR (OCR dei PDF, PDF → Word):' 'From the AUR (PDF OCR, PDF → Word):')"
        cmd "$aur_helper -S ${missing_aur[*]}"
    fi
    if ! command -v rembg >/dev/null 2>&1; then
        info "$(t 'Rimozione dello sfondo (facoltativa, scarica un modello AI):' 'Background removal (optional, downloads an AI model):')"
        cmd "pipx install \"rembg[cpu,cli]\""
    fi

    if pkg_installed dropwheel; then
        warn "$(t 'Il pacchetto di sistema "dropwheel" è già installato: questa installazione utente avrà la precedenza (~/.local/share/dropwheel). Di solito ne basta una.' \
                  'The "dropwheel" system package is installed too: this user install takes precedence (~/.local/share/dropwheel). Usually one is enough.')"
        ask "$(t 'Continuare comunque?' 'Continue anyway?')" n y || exit 1
    fi
else
    warn "$(t 'pacman non trovato: controllo delle dipendenze saltato (Dropwheel è pensato per Arch Linux).' \
              'pacman not found: dependency check skipped (Dropwheel targets Arch Linux).')"
fi

if ! "$PYTHON_BIN" -c 'import PIL' >/dev/null 2>&1; then
    warn "$(t 'Pillow non è importabile da' 'Pillow cannot be imported by') $PYTHON_BIN"
fi
if ! command -v qs >/dev/null 2>&1; then
    warn "$(t 'Quickshell (comando qs) non trovato: la ruota non potrà partire finché non lo installi.' \
              'Quickshell (qs command) not found: the wheel cannot start until you install it.')"
fi

if (( ${#missing_required[@]} )); then
    ask "$(t 'Mancano dipendenze obbligatorie. Installo comunque i file di Dropwheel?' \
             'Required dependencies are missing. Install the Dropwheel files anyway?')" y y || exit 1
fi

# --------------------------------------------------------------------------------------------
# Stop a running copy (it is restarted at the end)
# --------------------------------------------------------------------------------------------

UPGRADE=0
[[ -x $DW_BIN ]] && UPGRADE=1
if (( UPGRADE && DO_START )) && in_hyprland_session; then
    step "$(t 'Arresto la versione in esecuzione' 'Stopping the running version')"
    if command -v timeout >/dev/null 2>&1; then
        timeout 15 "$DW_BIN" stop >/dev/null 2>&1 || true
    else
        "$DW_BIN" stop >/dev/null 2>&1 || true
    fi
fi

# --------------------------------------------------------------------------------------------
# MIME guard, part 1: remember the current default applications
# --------------------------------------------------------------------------------------------
# ~/.local/share/applications is searched before /usr/share/applications, so a new desktop
# entry listing many MIME types can become the default "open" application for types that
# have no explicit default. Remember the current defaults to restore them afterwards.

declare -A PREVIOUS_DEFAULT=()
GUARD_TYPES=()
if (( DO_MIME_GUARD )) && command -v xdg-mime >/dev/null 2>&1; then
    mapfile -t GUARD_TYPES < <(sed -n 's/^MimeType=//p' "$SCRIPT_DIR/integrations/desktop/dropwheel.desktop" \
                               | tr ';' '\n' | grep -v '\*' | grep -v '^$' || true)
    step "$(t 'Annoto le applicazioni predefinite dei tipi di file' 'Recording the default applications of file types')"
    for mime in "${GUARD_TYPES[@]}"; do
        PREVIOUS_DEFAULT[$mime]=$(xdg-mime query default "$mime" 2>/dev/null || true)
    done
    ok "$(t "${#GUARD_TYPES[@]} tipi controllati" "${#GUARD_TYPES[@]} types checked")"
fi

# --------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------

step "$(t 'Installo i file in' 'Installing files into') $PREFIX"
mkdir -p -- "$BIN_DIR" "$DATA_DIR" "$PY_DIR" "$APP_DIR" "$ICON_DIR"

sync_dir "$SCRIPT_DIR/src" dropwheel "$PY_DIR"
"$PYTHON_BIN" -m compileall -q "$PY_DIR/dropwheel" >/dev/null 2>&1 || warn "$(t 'compilazione dei .pyc non riuscita (non grave)' 'byte-compiling failed (harmless)')"
ok "$(t 'pacchetto Python' 'Python package') → $PY_DIR/dropwheel"

sync_dir "$SCRIPT_DIR" shell "$DATA_DIR"
ok "$(t 'shell Quickshell' 'Quickshell shell') → $DATA_DIR/shell"

sync_dir "$SCRIPT_DIR" integrations "$DATA_DIR"
chmod 0755 "$DATA_DIR/integrations/nautilus/Dropwheel"
ok "$(t 'integrazioni' 'integrations') → $DATA_DIR/integrations"

install -m 0755 "$SCRIPT_DIR/uninstall.sh" "$DATA_DIR/uninstall.sh"

wrapper="#!/bin/sh
# dropwheel launcher — generated by Dropwheel install.sh (user-local install).
# DROPWHEEL-USER-WRAPPER (uninstall.sh removes this file only if it contains this marker)
# Re-run install.sh to regenerate it.
if [ -z \"\${DROPWHEEL_DATA:-}\" ]; then
    DROPWHEEL_DATA=$(sq "$DATA_DIR")
fi
export DROPWHEEL_DATA
PYTHONPATH=$(sq "$PY_DIR")\"\${PYTHONPATH:+:\$PYTHONPATH}\"
export PYTHONPATH
# -P: with -m Python would look in the current directory first, and that is often a folder of
# downloads (file managers run this launcher from the folder being shown).
exec $(sq "$PYTHON_BIN") -P -m dropwheel \"\$@\"
"
write_file "$DW_BIN" "$wrapper" 0755
MANIFEST_PATHS+=("$DW_BIN")
ok "$(t 'comando' 'command') → $DW_BIN"

install_launcher "$SCRIPT_DIR/integrations/desktop/dropwheel.desktop" "$APP_DIR/dropwheel.desktop" 0644 desktop
ok "$(t 'voce del menu applicazioni' 'application menu entry') → $APP_DIR/dropwheel.desktop"

install -Dm0644 "$SCRIPT_DIR/assets/dropwheel.svg" "$ICON_DIR/dropwheel.svg"
MANIFEST_PATHS+=("$ICON_DIR/dropwheel.svg")
touch "$ICON_ROOT" 2>/dev/null || true
if [[ -f $ICON_ROOT/icon-theme.cache ]] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$ICON_ROOT" >/dev/null 2>&1 || true
fi
ok "$(t 'icona' 'icon') → $ICON_DIR/dropwheel.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$APP_DIR" >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------------------------
# File managers
# --------------------------------------------------------------------------------------------

if (( DO_FILE_MANAGERS )); then
    step "$(t 'Integrazione con i file manager' 'File manager integration')"
    fm_done=0
    if command -v nautilus >/dev/null 2>&1; then
        install -Dm0755 "$SCRIPT_DIR/integrations/nautilus/Dropwheel" "$XDG_DATA_HOME_DIR/nautilus/scripts/Dropwheel"
        MANIFEST_PATHS+=("$XDG_DATA_HOME_DIR/nautilus/scripts/Dropwheel")
        ok "Nautilus: $(t 'tasto destro → Script → Dropwheel' 'right click → Scripts → Dropwheel')"
        fm_done=1
    fi
    if command -v dolphin >/dev/null 2>&1; then
        # Per-user service menus must be executable, otherwise Dolphin ignores them.
        install_launcher "$SCRIPT_DIR/integrations/dolphin/dropwheel.desktop" \
            "$XDG_DATA_HOME_DIR/kio/servicemenus/dropwheel.desktop" 0755 desktop
        ok "Dolphin: $(t 'tasto destro → Apri in Dropwheel' 'right click → Open in Dropwheel')"
        fm_done=1
    fi
    if command -v nemo >/dev/null 2>&1; then
        install_launcher "$SCRIPT_DIR/integrations/nemo/dropwheel.nemo_action" \
            "$XDG_DATA_HOME_DIR/nemo/actions/dropwheel.nemo_action" 0644 shell
        ok "Nemo: $(t 'tasto destro → Apri in Dropwheel' 'right click → Open in Dropwheel')"
        fm_done=1
    fi
    if command -v thunar >/dev/null 2>&1; then
        if grep -qs 'dropwheel open' "$XDG_CONFIG_HOME_DIR/Thunar/uca.xml"; then
            ok "Thunar: $(t 'azione già presente in uca.xml' 'action already present in uca.xml')"
        else
            info "Thunar: $(t 'il file delle azioni è gestito da Caelestia, quindi non lo modifico. Per aggiungere "Apri in Dropwheel":' \
                              'its actions file is managed by Caelestia, so it is left untouched. To add "Open in Dropwheel":')"
            cmd "dropwheel setup thunar --apply"
            info "$(t 'oppure segui' 'or follow') $DATA_DIR/integrations/thunar/README.md"
        fi
        fm_done=1
    fi
    (( fm_done )) || info "$(t 'Nessun file manager supportato trovato (Nautilus, Dolphin, Nemo, Thunar).' \
                              'No supported file manager found (Nautilus, Dolphin, Nemo, Thunar).')"
fi

# --------------------------------------------------------------------------------------------
# MIME guard, part 2: restore defaults that the new desktop entry took over
# --------------------------------------------------------------------------------------------

if (( ${#GUARD_TYPES[@]} )); then
    restored=0
    for mime in "${GUARD_TYPES[@]}"; do
        before=${PREVIOUS_DEFAULT[$mime]:-}
        [[ -n $before && $before != dropwheel.desktop ]] || continue
        after=$(xdg-mime query default "$mime" 2>/dev/null || true)
        if [[ $after == dropwheel.desktop ]]; then
            if xdg-mime default "$before" "$mime" 2>/dev/null; then
                restored=$((restored + 1))
            fi
        fi
    done
    if (( restored == 1 )); then
        ok "$(t "Ripristinata 1 applicazione predefinita in ~/.config/mimeapps.list (Dropwheel resta in \"Apri con\")" \
                "Restored 1 default application in ~/.config/mimeapps.list (Dropwheel stays in \"Open with\")")"
    elif (( restored )); then
        ok "$(t "Ripristinate $restored applicazioni predefinite in ~/.config/mimeapps.list (Dropwheel resta in \"Apri con\")" \
                "Restored $restored default applications in ~/.config/mimeapps.list (Dropwheel stays in \"Open with\")")"
    fi
fi

# --------------------------------------------------------------------------------------------
# Manifest (read by uninstall.sh)
# --------------------------------------------------------------------------------------------

manifest="# Dropwheel user install manifest — files created outside $DATA_DIR
# prefix=$PREFIX
"
for p in "${MANIFEST_PATHS[@]}"; do
    manifest+="$p"$'\n'
done
write_file "$MANIFEST" "$manifest" 0644

# --------------------------------------------------------------------------------------------
# PATH
# --------------------------------------------------------------------------------------------

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        warn "$(t "$BIN_DIR non è nel PATH: il comando \"dropwheel\" non sarà trovato dal terminale." \
                  "$BIN_DIR is not in PATH: the terminal will not find the \"dropwheel\" command.")"
        info "fish: fish_add_path $BIN_DIR"
        info "bash: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc"
        info "zsh:  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc"
        ;;
esac

# --------------------------------------------------------------------------------------------
# Hyprland / Caelestia
# --------------------------------------------------------------------------------------------

# A require("dropwheel") (or dofile(".../dropwheel.lua")) that is not commented out with "--".
DW_LOAD_RE='^([^-]|-[^-])*(require[[:space:]]*\(?[[:space:]]*["'"'"']dropwheel["'"'"']|dofile[[:space:]]*\(?.*dropwheel\.lua)'
# The same for hypr-user: ~/.config/caelestia also exists with a Hyprland configuration of your
# own, and then nothing reads hypr-user.lua (cli.py: caelestia_config_active).
HYPR_USER_RE='^([^-]|-[^-])*(require[[:space:]]*\(?[[:space:]]*["'"'"']hypr-user["'"'"']|dofile[[:space:]]*\(?.*hypr-user)'

hypr_has_dropwheel() {
    local main="$XDG_CONFIG_HOME_DIR/hypr/hyprland.lua" user="$XDG_CONFIG_HOME_DIR/caelestia/hypr-user.lua"
    [[ -f $main ]] || return 1
    if grep -qE "$DW_LOAD_RE" "$main"; then
        return 0
    fi
    [[ -f $user ]] && grep -qE "$HYPR_USER_RE" "$main" && grep -qE "$DW_LOAD_RE" "$user"
}

hypr_configured=0
if (( DO_HYPRLAND )); then
    step "Hyprland"
    if hypr_has_dropwheel; then
        ok "$(t 'Dropwheel è già collegato alla configurazione di Hyprland' 'Dropwheel is already wired into the Hyprland configuration')"
        hypr_configured=1
    elif ! command -v hyprctl >/dev/null 2>&1 && [[ ! -d $XDG_CONFIG_HOME_DIR/hypr ]]; then
        info "$(t 'Hyprland non trovato: configurazione saltata.' 'Hyprland not found: setup skipped.')"
    else
        info "$(t '"dropwheel setup hyprland" copia dropwheel.lua in ~/.config/caelestia/ e aggiunge require("dropwheel") a hypr-user.lua (con backup); senza Caelestia lo copia in ~/.config/hypr/ e mostra le righe da aggiungere.' \
                  '"dropwheel setup hyprland" copies dropwheel.lua into ~/.config/caelestia/ and adds require("dropwheel") to hypr-user.lua (with a backup); without Caelestia it copies it into ~/.config/hypr/ and shows the lines to add.')"
        if ask "$(t 'Configuro adesso Shift, SUPER+ALT+D e le regole dei layer?' 'Set up Shift, SUPER+ALT+D and the layer rules now?')" y n; then
            if "$DW_BIN" setup hyprland; then
                # Without Caelestia the command only copies the module and prints the lines to add.
                if hypr_has_dropwheel; then
                    ok "$(t 'Hyprland configurato' 'Hyprland configured')"
                    hypr_configured=1
                else
                    warn "$(t 'Hyprland non è ancora collegato: aggiungi a mano le righe indicate qui sopra.' \
                              'Hyprland is not wired yet: add the lines shown above by hand.')"
                fi
            else
                warn "$(t 'Configurazione automatica non riuscita. Puoi farla a mano:' 'Automatic setup failed. You can do it by hand:')"
                cmd "cp $DATA_DIR/integrations/hyprland/dropwheel.lua ~/.config/caelestia/dropwheel.lua"
                cmd "echo 'require(\"dropwheel\")' >> ~/.config/caelestia/hypr-user.lua"
            fi
        else
            info "$(t 'Quando vuoi:' 'Whenever you like:')"
            cmd "dropwheel setup hyprland"
        fi
    fi
fi

# --------------------------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------------------------

started=0
if (( DO_START )); then
    step "$(t 'Avvio' 'Starting')"
    if ! in_hyprland_session; then
        info "$(t 'Non sei in una sessione Hyprland: avvia Dropwheel dopo il login con "dropwheel start".' \
                  'Not in a Hyprland session: start Dropwheel after logging in with "dropwheel start".')"
    elif ! command -v qs >/dev/null 2>&1; then
        warn "$(t 'Quickshell non installato: avvio saltato.' 'Quickshell is not installed: start skipped.')"
    elif "$DW_BIN" start; then
        ok "$(t 'Dropwheel è in esecuzione' 'Dropwheel is running')"
        started=1
    else
        warn "$(t 'Avvio non riuscito: controlla con "dropwheel doctor".' 'Start failed: check with "dropwheel doctor".')"
    fi
fi

# --------------------------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------------------------

step "$(t 'Fatto' 'Done')"
if (( started )); then
    start_hint=""
elif (( UPGRADE )); then
    start_hint=$(t 'Per usare la nuova versione: dropwheel stop && dropwheel start' \
                   'To use the new version: dropwheel stop && dropwheel start')
else
    start_hint=$(t 'Per avviarla: dropwheel start' 'To start it: dropwheel start')
fi
if (( IT )); then
    info "Mentre trascini dei file premi ${C_BOLD}Shift${C_RESET}: compare la ruota sotto il puntatore."
    info "Senza trascinare: copia dei file e premi ${C_BOLD}SUPER+ALT+D${C_RESET}."
    (( hypr_configured )) || info "Per le scorciatoie serve prima: dropwheel setup hyprland"
    [[ -z $start_hint ]] || info "$start_hint"
    info "Controllo degli strumenti opzionali: dropwheel doctor"
    info "Disinstallazione: $DATA_DIR/uninstall.sh"
else
    info "While dragging files press ${C_BOLD}Shift${C_RESET}: the wheel appears under the pointer."
    info "Without dragging: copy some files and press ${C_BOLD}SUPER+ALT+D${C_RESET}."
    (( hypr_configured )) || info "The shortcuts need: dropwheel setup hyprland"
    [[ -z $start_hint ]] || info "$start_hint"
    info "Check optional tools: dropwheel doctor"
    info "Uninstall: $DATA_DIR/uninstall.sh"
fi
