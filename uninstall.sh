#!/usr/bin/env bash
# Dropwheel — removes the user-local installation made by install.sh.
#
#   ./uninstall.sh            remove the program, keep settings, history and your data
#   ./uninstall.sh --purge    also remove ~/.config/dropwheel, its state and its cache, and the
#                             Whisper models and saved wallpapers in ~/.local/share/dropwheel
#
# Files produced by Dropwheel (converted images, PDFs, …) are never touched.
# install.sh also copies this script to <prefix>/share/dropwheel/uninstall.sh; that copy
# removes its own prefix, so it does not need --prefix.

set -euo pipefail

# --------------------------------------------------------------------------------------------
# Language, colours, messages
# --------------------------------------------------------------------------------------------

_locale=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
if [[ $_locale == it* ]]; then IT=1; else IT=0; fi

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
Uso: ./uninstall.sh [opzioni]

Rimuove l'installazione per l'utente fatta con install.sh.

Opzioni:
  --prefix DIR       cartella usata per l'installazione (predefinita: ~/.local, oppure
                     quella della copia installata di questo script)
  --purge            cancella anche impostazioni, cronologia e cache di Dropwheel,
                     i modelli Whisper e gli sfondi salvati
  --keep-hyprland    non toccare la configurazione di Hyprland
  -y, --yes          non chiedere conferme
  -h, --help         mostra questo aiuto

I file creati con Dropwheel (conversioni, PDF, …) non vengono mai cancellati.
EOF
    else
        cat <<'EOF'
Usage: ./uninstall.sh [options]

Removes the user-local installation made by install.sh.

Options:
  --prefix DIR       installation prefix that was used (default: ~/.local, or the one
                     of the installed copy of this script)
  --purge            also delete Dropwheel's settings, history and cache, the Whisper
                     models and the saved wallpapers
  --keep-hyprland    leave the Hyprland configuration alone
  -y, --yes          do not ask for confirmation
  -h, --help         show this help

Files created with Dropwheel (conversions, PDFs, …) are never deleted.
EOF
    fi
}

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

TMP_PATHS=()
cleanup() {
    local p
    for p in "${TMP_PATHS[@]+"${TMP_PATHS[@]}"}"; do
        [[ -n $p && -e $p ]] && rm -rf -- "$p"
    done
    return 0
}
trap cleanup EXIT

TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# --------------------------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------------------------

PREFIX="${HOME:?HOME is not set}/.local"
# The copy that install.sh leaves in <prefix>/share/dropwheel removes that prefix.
SELF_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $SELF_DIR == */share/dropwheel && -f $SELF_DIR/install-manifest.txt ]]; then
    PREFIX=${SELF_DIR%/share/dropwheel}
fi
PURGE=0
KEEP_HYPRLAND=0
ASSUME_YES=0

while (( $# )); do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --prefix) [[ $# -ge 2 ]] || die "$(t '--prefix richiede una cartella' '--prefix needs a directory')"; PREFIX=$2; shift 2 ;;
        --prefix=*) PREFIX=${1#*=}; shift ;;
        --purge) PURGE=1; shift ;;
        --keep-hyprland) KEEP_HYPRLAND=1; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        *) die "$(t 'Opzione sconosciuta' 'Unknown option'): $1 ($(t 'vedi --help' 'see --help'))" ;;
    esac
done

PREFIX=${PREFIX/#\~/$HOME}
[[ $PREFIX == /* ]] || PREFIX="$PWD/$PREFIX"
PREFIX=${PREFIX%/}

BIN_DIR="$PREFIX/bin"
DATA_DIR="$PREFIX/share/dropwheel"
APP_DIR="$PREFIX/share/applications"
ICON_ROOT="$PREFIX/share/icons/hicolor"
MANIFEST="$DATA_DIR/install-manifest.txt"
DW_BIN="$BIN_DIR/dropwheel"
XDG_DATA_HOME_DIR=${XDG_DATA_HOME:-$HOME/.local/share}
XDG_CONFIG_HOME_DIR=${XDG_CONFIG_HOME:-$HOME/.config}
XDG_STATE_HOME_DIR=${XDG_STATE_HOME:-$HOME/.local/state}
XDG_CACHE_HOME_DIR=${XDG_CACHE_HOME:-$HOME/.cache}

(( EUID != 0 )) || die "$(t 'Non eseguire uninstall.sh come root: rimuove l'"'"'installazione del tuo utente.' \
                            'Do not run uninstall.sh as root: it removes your user installation.')"

# Refuse anything that does not look like a Dropwheel data directory before deleting it.
case $DATA_DIR in
    */share/dropwheel) ;;
    *) die "$(t 'Cartella dati inattesa' 'Unexpected data directory'): $DATA_DIR" ;;
esac
[[ $DATA_DIR != "/share/dropwheel" && $DATA_DIR != "$HOME" ]] || die "$(t 'Cartella dati non valida' 'Invalid data directory'): $DATA_DIR"

printf '%sDropwheel%s — %s\n' "$C_BOLD" "$C_RESET" "$(t 'disinstallazione per l'"'"'utente' 'user-local uninstall')"

# whisper/ and wallpapers/ alone are the user's data left by an earlier uninstall, not an install.
installed_here=0
if grep -qs 'DROPWHEEL-USER-WRAPPER' "$DW_BIN" || [[ -e $DATA_DIR/python || -e $DATA_DIR/shell || -e $MANIFEST ]]; then
    installed_here=1
fi
system_package=0
if command -v pacman >/dev/null 2>&1 && pacman -Qq dropwheel >/dev/null 2>&1; then
    system_package=1
fi

# Nothing installed in this prefix (wrong --prefix, or only the system package): the Hyprland
# wiring and the file-manager actions may belong to the other installation, so leave them.
remove_files=1
if (( ! installed_here )); then
    remove_files=0
    warn "$(t 'Nessuna installazione utente trovata in' 'No user installation found in') $PREFIX"
    if (( system_package )); then
        info "$(t 'Dropwheel è installato come pacchetto di sistema; per rimuoverlo:' 'Dropwheel is installed as a system package; to remove it:')"
        cmd "sudo pacman -Rns dropwheel"
    elif ask "$(t 'Tolgo comunque i resti di Dropwheel (configurazione di Hyprland, voci del menu, azioni dei file manager)?' \
                  'Remove the Dropwheel leftovers anyway (Hyprland configuration, menu entries, file-manager actions)?')" n n; then
        remove_files=1
    fi
    if (( ! remove_files )); then
        KEEP_HYPRLAND=1
        info "$(t 'Configurazione di Hyprland e azioni dei file manager lasciate come sono.' \
                  'Hyprland configuration and file-manager actions left as they are.')"
    fi
fi

# --------------------------------------------------------------------------------------------
# Stop the running instance
# --------------------------------------------------------------------------------------------

step "$(t 'Arresto Dropwheel' 'Stopping Dropwheel')"
run_with_timeout() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 15 "$@"
    else
        "$@"
    fi
}
if [[ -x $DW_BIN ]] && grep -qs 'DROPWHEEL-USER-WRAPPER' "$DW_BIN"; then
    run_with_timeout "$DW_BIN" stop >/dev/null 2>&1 || true
fi
# Fallback: ask Quickshell to close the instance started from this shell directory.
if command -v qs >/dev/null 2>&1 && [[ -d $DATA_DIR/shell ]]; then
    run_with_timeout qs kill -p "$DATA_DIR/shell" >/dev/null 2>&1 || true
fi
ok "$(t 'fatto' 'done')"

# --------------------------------------------------------------------------------------------
# Hyprland configuration
# --------------------------------------------------------------------------------------------

# Removes the lines that load Dropwheel (require("dropwheel"), pcall(require, "dropwheel"),
# dofile(".../dropwheel.lua")) plus a comment line right above them that mentions Dropwheel.
# Commented-out lines are left alone.
read -r -d '' STRIP_AWK <<'AWK' || true
function is_comment(line) { return line ~ /^[ \t]*--/ }
function loads_dropwheel(line,   l) {
    if (is_comment(line)) return 0
    l = tolower(line)
    if (l ~ /require[ \t]*\(?[ \t]*["']dropwheel["']/) return 1
    if (l ~ /dofile[ \t]*\(?.*dropwheel\.lua/) return 1
    return 0
}
{
    if (loads_dropwheel($0)) {
        removed++
        if (have_prev && is_comment(prev) && tolower(prev) ~ /dropwheel/) have_prev = 0
        next
    }
    if (have_prev) print prev
    prev = $0
    have_prev = 1
}
END {
    if (have_prev) print prev
}
AWK

hypr_changed=0
strip_loader() {
    local file=$1 tmp
    [[ -f $file ]] || return 0
    tmp=$(mktemp "${TMPDIR:-/tmp}/dropwheel-uninstall.XXXXXX")
    TMP_PATHS+=("$tmp")
    awk "$STRIP_AWK" "$file" > "$tmp"
    if cmp -s "$file" "$tmp"; then
        return 0
    fi
    cp -p -- "$file" "$file.dropwheel-bak-$TIMESTAMP"
    # Write in place (cat >) so that symlinks and permissions of the file are preserved.
    cat -- "$tmp" > "$file"
    ok "$(t 'tolto Dropwheel da' 'removed Dropwheel from') $file ($(t 'backup' 'backup'): $file.dropwheel-bak-$TIMESTAMP)"
    if awk '!/^[ \t]*--/ && /(^|[^A-Za-z0-9_])dropwheel[.:]/ { found = 1 } END { exit !found }' "$file"; then
        warn "$(t 'il file usa ancora la variabile del modulo dropwheel: controllalo' 'the file still uses the dropwheel module variable: please check it'): $file"
    fi
    hypr_changed=1
}

# Removes the copy of dropwheel.lua that "dropwheel setup hyprland" placed next to the user
# configuration; a modified copy is kept as a backup unless --purge.
remove_module_copy() {
    local copy=$1 original="$DATA_DIR/integrations/hyprland/dropwheel.lua"
    [[ -f $copy ]] || return 0
    if (( PURGE )) || { [[ -f $original ]] && cmp -s "$copy" "$original"; }; then
        rm -f -- "$copy"
        ok "$(t 'rimosso' 'removed') $copy"
    else
        mv -- "$copy" "$copy.dropwheel-bak-$TIMESTAMP"
        ok "$(t 'la tua copia modificata è stata salvata in' 'your modified copy was saved as') $copy.dropwheel-bak-$TIMESTAMP"
    fi
    hypr_changed=1
}

if (( ! KEEP_HYPRLAND )); then
    step "Hyprland"
    strip_loader "$XDG_CONFIG_HOME_DIR/caelestia/hypr-user.lua"
    strip_loader "$XDG_CONFIG_HOME_DIR/hypr/hyprland.lua"
    remove_module_copy "$XDG_CONFIG_HOME_DIR/caelestia/dropwheel.lua"
    remove_module_copy "$XDG_CONFIG_HOME_DIR/hypr/dropwheel.lua"
    if (( hypr_changed )); then
        info "$(t 'Ricarica Hyprland per togliere le scorciatoie di Dropwheel:' 'Reload Hyprland to drop the Dropwheel shortcuts:')"
        cmd "hyprctl reload"
    else
        ok "$(t 'nessuna configurazione di Dropwheel trovata' 'no Dropwheel configuration found')"
    fi
fi

# --------------------------------------------------------------------------------------------
# Installed files
# --------------------------------------------------------------------------------------------

step "$(t 'Rimuovo i file' 'Removing files')"

installed=()
if (( ! remove_files )); then
    ok "$(t 'niente da rimuovere' 'nothing to remove')"
elif [[ -f $MANIFEST ]]; then
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line == /* ]] && installed+=("$line")
    done < "$MANIFEST"
else
    installed=(
        "$DW_BIN"
        "$APP_DIR/dropwheel.desktop"
        "$ICON_ROOT/scalable/apps/dropwheel.svg"
        "$XDG_DATA_HOME_DIR/nautilus/scripts/Dropwheel"
        "$XDG_DATA_HOME_DIR/kio/servicemenus/dropwheel.desktop"
        "$XDG_DATA_HOME_DIR/nemo/actions/dropwheel.nemo_action"
    )
fi

for path in "${installed[@]+"${installed[@]}"}"; do
    [[ -e $path || -L $path ]] || continue
    case $path in
        "$DW_BIN")
            if ! grep -qs 'DROPWHEEL-USER-WRAPPER' "$path"; then
                warn "$(t 'non rimosso (non è il lanciatore di install.sh)' 'not removed (not the install.sh launcher)'): $path"
                continue
            fi
            ;;
        *.svg) ;;
        *)
            if ! grep -qsi 'dropwheel' "$path"; then
                warn "$(t 'non rimosso (non sembra di Dropwheel)' 'not removed (does not look like a Dropwheel file)'): $path"
                continue
            fi
            ;;
    esac
    rm -f -- "$path"
    ok "$path"
done

# Only what install.sh put in the data directory, including what an interrupted update left
# behind: whisper/ and wallpapers/ live there too, but they are the user's data (see --purge).
if (( remove_files )); then
    for path in "$DATA_DIR"/{python,shell,integrations,uninstall.sh,install-manifest.txt} \
                "$DATA_DIR"/.{shell,integrations}.old \
                "$DATA_DIR"/.{shell,integrations}.new.* "$DATA_DIR"/.dropwheel-install.*; do
        [[ -e $path || -L $path ]] || continue
        rm -rf -- "$path"
        ok "$path"
    done
fi

if command -v update-desktop-database >/dev/null 2>&1 && [[ -d $APP_DIR ]]; then
    update-desktop-database -q "$APP_DIR" >/dev/null 2>&1 || true
fi
if [[ -f $ICON_ROOT/icon-theme.cache ]] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$ICON_ROOT" >/dev/null 2>&1 || true
fi

# Thunar: install.sh never edits uca.xml, so only point the user to it.
if grep -qs 'dropwheel open' "$XDG_CONFIG_HOME_DIR/Thunar/uca.xml"; then
    info "$(t 'Thunar: togli l'"'"'azione "Apri in Dropwheel" da Modifica → Configura azioni personalizzate.' \
              'Thunar: remove the "Open in Dropwheel" action from Edit → Configure custom actions.')"
fi

# --------------------------------------------------------------------------------------------
# Settings, history, cache, Whisper models, saved wallpapers (--purge)
# --------------------------------------------------------------------------------------------

user_dirs=("$XDG_CONFIG_HOME_DIR/dropwheel" "$XDG_STATE_HOME_DIR/dropwheel" "$XDG_CACHE_HOME_DIR/dropwheel"
           "$XDG_DATA_HOME_DIR/dropwheel/whisper" "$XDG_DATA_HOME_DIR/dropwheel/wallpapers")
present=()
for d in "${user_dirs[@]}"; do
    [[ -e $d ]] && present+=("$d")
done
if (( PURGE )); then
    step "$(t 'Impostazioni, cronologia, cache, modelli Whisper e sfondi' 'Settings, history, cache, Whisper models and wallpapers')"
    if (( ${#present[@]} == 0 )); then
        ok "$(t 'niente da cancellare' 'nothing to delete')"
    else
        for d in "${present[@]}"; do
            info "$d"
        done
        if ask "$(t 'Cancello queste cartelle?' 'Delete these folders?')" n n; then
            for d in "${present[@]}"; do
                rm -rf -- "$d"
                ok "$d"
            done
        else
            info "$(t 'Lasciate al loro posto.' 'Left in place.')"
        fi
    fi
elif (( ${#present[@]} )); then
    info "$(t 'Restano al loro posto impostazioni, cronologia, modelli Whisper e sfondi salvati (usa --purge per cancellarli):' \
              'Settings, history, Whisper models and saved wallpapers stay in place (use --purge to delete them):')"
    for d in "${present[@]}"; do
        info "  $d"
    done
fi

# The folders go only when nothing else is left in them.
for d in "$DATA_DIR" "$XDG_DATA_HOME_DIR/dropwheel"; do
    if [[ -d $d ]]; then
        rmdir -- "$d" 2>/dev/null || true
    fi
done

if (( installed_here && system_package )); then
    info "$(t 'Il pacchetto di sistema "dropwheel" è ancora installato:' 'The "dropwheel" system package is still installed:')"
    cmd "sudo pacman -Rns dropwheel"
    if (( hypr_changed )); then
        info "$(t 'Se vuoi continuare a usarlo, ricollega le scorciatoie:' 'To keep using it, wire the shortcuts again:')"
        cmd "dropwheel setup hyprland"
    fi
fi

if (( installed_here )); then
    step "$(t 'Dropwheel è stato disinstallato' 'Dropwheel has been uninstalled')"
else
    step "$(t 'Fatto' 'Done')"
fi
