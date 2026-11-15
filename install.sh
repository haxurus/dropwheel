#!/usr/bin/env bash
# Dropwheel — interactive installer for Arch Linux (Hyprland or GNOME), Debian, Ubuntu, Fedora and
# openSUSE (GNOME), and macOS 13+ (preview, with Homebrew).
#
# It runs under macOS's own bash 3.2 too: no ${var,,}, associative arrays or mapfile on the
# paths macOS takes, and empty arrays are expanded as ${a[@]+"${a[@]}"} (set -u).
#
#   ./install.sh                 asks everything, installs into ~/.local and starts Dropwheel
#   curl -fsSL https://raw.githubusercontent.com/haxurus/dropwheel/main/install.sh | bash
#                                the same without cloning first: the code goes to ~/.local/src/dropwheel
#   ./install.sh --help          all options
#
# Layout (see docs/SPEC.md §8):
#   ~/.local/share/dropwheel/python/dropwheel   Python package
#   ~/.local/share/dropwheel/shell/             Quickshell shell (launched by path)
#   ~/.local/share/dropwheel/integrations/      Hyprland, file-manager and desktop files
#   ~/.local/bin/dropwheel                      launcher (python3 -m dropwheel, PYTHONPATH set)
#   ~/.local/share/applications/dropwheel.desktop, ~/.local/share/icons/hicolor/…/dropwheel.svg
#
# Packages are installed with pacman, apt, dnf or zypper only after you say yes, from a terminal
# (sudo asks for your password once); without a terminal the commands are printed instead.
# Everything else stays in your home directory. On Debian, Ubuntu, Fedora, openSUSE and macOS the
# Python modules pip installs go into a private environment (~/.local/share/dropwheel/venv), never
# into the system Python; on Debian, Ubuntu and macOS PySide6 too (Fedora and openSUSE package it).
# On macOS the packages come from Homebrew (no sudo); Homebrew itself is installed only if you say
# yes to its official command, which is shown first.

set -euo pipefail

# empty when the script comes from a pipe (curl … | bash): the sources are then cloned
SCRIPT_DIR=""
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
    SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
fi
readonly SCRIPT_DIR

# --------------------------------------------------------------------------------------------
# Language, colours, messages
# --------------------------------------------------------------------------------------------

_locale=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
if [[ $_locale == it* ]]; then IT=1; else IT=0; fi

# t "italiano" "english" → the text in the session language
t() { if (( IT )); then printf '%s' "$1"; else printf '%s' "$2"; fi; }

# lower-case (bash 3.2, the macOS one, has no ${var,,})
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

if [[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; then
    C_RESET=$'\e[0m' C_BOLD=$'\e[1m' C_RED=$'\e[1;31m' C_GREEN=$'\e[1;32m'
    C_YELLOW=$'\e[1;33m' C_BLUE=$'\e[1;34m' C_DIM=$'\e[2m' C_CYAN=$'\e[1;36m'
    GRADIENT=($'\e[38;5;51m' $'\e[38;5;45m' $'\e[38;5;39m' $'\e[38;5;33m' $'\e[38;5;63m' $'\e[38;5;99m')
else
    C_RESET='' C_BOLD='' C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_DIM='' C_CYAN=''
    GRADIENT=('' '' '' '' '' '')
fi

STEP_NO=${DROPWHEEL_INSTALL_STEP:-0}
step() {
    STEP_NO=$((STEP_NO + 1))
    printf '\n%s━━ %s%s %s%s%s\n' "$C_BLUE" "$STEP_NO" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"
}
info() { printf '    %s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; exit 1; }
cmd()  { printf '      %s$ %s%s\n' "$C_DIM" "$*" "$C_RESET"; }

banner() {
    local cols i
    local -a art=(
        '██████╗ ██████╗  ██████╗ ██████╗ ██╗    ██╗██╗  ██╗███████╗███████╗██╗'
        '██╔══██╗██╔══██╗██╔═══██╗██╔══██╗██║    ██║██║  ██║██╔════╝██╔════╝██║'
        '██║  ██║██████╔╝██║   ██║██████╔╝██║ █╗ ██║███████║█████╗  █████╗  ██║'
        '██║  ██║██╔══██╗██║   ██║██╔═══╝ ██║███╗██║██╔══██║██╔══╝  ██╔══╝  ██║'
        '██████╔╝██║  ██║╚██████╔╝██║     ╚███╔███╔╝██║  ██║███████╗███████╗███████╗'
        '╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝      ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝'
    )
    cols=${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}
    printf '\n'
    if (( cols >= 78 )) && [[ ${LC_ALL:-${LC_CTYPE:-${LANG:-}}} == *[Uu][Tt][Ff]* ]]; then
        for i in "${!art[@]}"; do
            printf '  %s%s%s\n' "${GRADIENT[i]}" "${art[i]}" "$C_RESET"
        done
    else
        printf '  %sD R O P W H E E L%s\n' "$C_CYAN" "$C_RESET"
    fi
    printf '\n  %s%s%s  %s·  by Haxurus%s\n\n' "$C_BOLD" \
        "$(t 'La ruota delle azioni sui file' 'A radial wheel of file actions')" "$C_RESET" "$C_DIM" "$C_RESET"
    field "$(t 'Codice' 'Code')" "https://github.com/haxurus/dropwheel"
    field "$(t 'Documentazione' 'Documentation')" "https://github.com/haxurus/dropwheel/blob/main/docs/README.md"
    field "$(t 'Segnalazioni' 'Issues')" "https://github.com/haxurus/dropwheel/issues"
    field "$(t 'Novità' 'Changelog')" "https://github.com/haxurus/dropwheel/blob/main/CHANGELOG.md"
}

# field "label" "value" [style]: a label padded by characters (printf pads by bytes)
field() {
    local pad=$((16 - ${#1}))
    (( pad > 0 )) || pad=1
    printf '  %s%s%*s %s%s\n' "${3:-}" "$1" "$pad" '' "$C_RESET" "$2"
}

# A terminal to ask in (not when piped, not with --yes).
interactive() { (( ! ASSUME_YES )) && [[ -t 0 ]]; }

# choose_groups "<title>" label... → CHOSEN=(indices); Enter = all, n = none, or numbers
CHOSEN=()
choose_groups() {
    local title=$1 reply n i ok
    shift
    local -a labels=("$@")
    CHOSEN=()
    if ! interactive; then
        for i in "${!labels[@]}"; do CHOSEN+=("$i"); done
        return
    fi
    printf '  %s%s%s\n' "$C_BOLD" "$title" "$C_RESET"
    for i in "${!labels[@]}"; do
        printf '    %s%2d%s  %s\n' "$C_CYAN" $((i + 1)) "$C_RESET" "${labels[i]}"
    done
    while true; do
        printf '  %s?%s %s ' "$C_YELLOW" "$C_RESET" \
            "$(t 'Quali installo? [Invio = tutti, n = nessuno, oppure i numeri, es. 1 3]' \
                 'Which ones? [Enter = all, n = none, or the numbers, e.g. 1 3]')"
        read -r reply || reply=""
        reply=$(lower "$reply")
        reply=${reply//,/ }
        case $reply in
            '') for i in "${!labels[@]}"; do CHOSEN+=("$i"); done; return ;;
            n|no|0|none|nessuno) return ;;
        esac
        ok=1
        for n in $reply; do
            if [[ $n =~ ^[0-9]+$ ]] && (( n >= 1 && n <= ${#labels[@]} )); then
                CHOSEN+=($((n - 1)))
            else
                ok=0
            fi
        done
        (( ok )) && return
        CHOSEN=()
        info "$(t 'Numeri da 1 a' 'Numbers from 1 to') ${#labels[@]}."
    done
}

# The one place that runs a command as root, always after the user said yes.
as_root() {
    sudo -- "$@"
}

usage() {
    if (( IT )); then
        cat <<'EOF'
Uso: ./install.sh [opzioni]

Installa Dropwheel per l'utente corrente (in ~/.local), senza permessi di root.

Opzioni:
  --prefix DIR         cartella di installazione (predefinita: ~/.local)
  --python PERCORSO    interprete Python da usare (predefinito: /usr/bin/python3, su macOS quello di Homebrew)
  --desktop NOME       hyprland oppure gnome (predefinito: quello della sessione)
  --no-packages        non installare pacchetti né moduli pip: mostra solo i comandi
  --no-hyprland        non proporre "dropwheel setup hyprland" (né "setup gnome")
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
  --python PATH        Python interpreter to use (default: /usr/bin/python3, on macOS Homebrew's)
  --desktop NAME       hyprland or gnome (default: the one of this session)
  --no-packages        do not install packages or pip modules: only show the commands
  --no-hyprland        do not offer "dropwheel setup hyprland" (nor "setup gnome")
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
        reply=$(lower "$reply")
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
    # the replacement comes from variables: bash 3.2 and 5 treat quotes in a literal one differently
    local q="'" r="'\\''"
    printf "'%s'" "${1//$q/$r}"
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

# macOS: what Homebrew has installed, one name per line (casks written cask:NAME), read once by
# brew_list: each "brew list" takes a moment, and pkg_installed also runs in $(…) subshells
BREW_LISTED=""
brew_list() {
    BREW_LISTED=$'\n'
    [[ -n $BREW ]] || return 0
    BREW_LISTED+=$("$BREW" list --formula -1 2>/dev/null || true)$'\n'
    BREW_LISTED+=$("$BREW" list --cask -1 2>/dev/null | sed 's/^/cask:/' || true)$'\n'
}

# brew_split NAME… → BREW_F (formulae) and BREW_C (casks, without "cask:")
brew_split() {
    local x
    BREW_F=() BREW_C=()
    for x in "$@"; do
        case $x in
            cask:*) BREW_C+=("${x#cask:}") ;;
            *) BREW_F+=("$x") ;;
        esac
    done
}

pkg_installed() {
    case ${DISTRO:-arch} in
        macos) [[ $BREW_LISTED == *$'\n'"$1"$'\n'* ]] ;;
        debian) [[ $(dpkg-query -W -f='${Status}' -- "$1" 2>/dev/null) == *"install ok installed"* ]] ;;
        fedora|suse) rpm -q --whatprovides "$1" >/dev/null 2>&1 ;;  # also a capability (python3-Pillow)
        *) pacman -Qq -- "$1" >/dev/null 2>&1 ;;
    esac
}

# the package manager knows the package (releases differ: unknown names are skipped)
# shortcut: dnf and zypper are asked once per package, slower than apt; fine for ~30 names
pkg_available() {
    case $DISTRO in
        fedora) dnf -q info "$1" >/dev/null 2>&1 ;;
        suse) zypper -q -n search --provides --match-exact "$1" >/dev/null 2>&1 ;;
        *) apt-cache show --no-all-versions -- "$1" >/dev/null 2>&1 ;;
    esac
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
DO_PACKAGES=1
DESKTOP=""
ORIG_ARGS=("$@")

while (( $# )); do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --prefix) [[ $# -ge 2 ]] || die "$(t '--prefix richiede una cartella' '--prefix needs a directory')"; PREFIX=$2; shift 2 ;;
        --prefix=*) PREFIX=${1#*=}; shift ;;
        --python) [[ $# -ge 2 ]] || die "$(t '--python richiede un percorso' '--python needs a path')"; PYTHON_BIN=$2; shift 2 ;;
        --python=*) PYTHON_BIN=${1#*=}; shift ;;
        --no-hyprland) DO_HYPRLAND=0; shift ;;
        --no-packages) DO_PACKAGES=0; shift ;;
        --desktop) [[ $# -ge 2 ]] || die "$(t '--desktop richiede hyprland o gnome' '--desktop needs hyprland or gnome')"; DESKTOP=$2; shift 2 ;;
        --desktop=*) DESKTOP=${1#*=}; shift ;;
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

[[ -n ${DROPWHEEL_INSTALL_CLONED:-} ]] || banner

case $(uname -s) in
    Linux) MACOS=0 ;;
    Darwin) MACOS=1 ;;
    *) die "$(t 'install.sh è per Linux e macOS. Su Windows usa install.ps1 in PowerShell.' \
                'install.sh is for Linux and macOS. On Windows use install.ps1 in PowerShell.')" ;;
esac
if (( EUID == 0 )); then
    die "$(t 'Non eseguire install.sh come root: installa per il tuo utente (chiederà la password solo per i pacchetti). Per un pacchetto di sistema su Arch usa "makepkg -si".' \
             'Do not run install.sh as root: it installs for your user (it asks for the password only for packages). For a system package on Arch use "makepkg -si".')"
fi

# --- which system and which desktop
DISTRO=arch
DISTRO_NAME="Linux"
OS_RELEASE=${DROPWHEEL_OS_RELEASE:-/etc/os-release}  # the variable is for the tests
if [[ -r $OS_RELEASE ]]; then
    # shellcheck disable=SC1090
    os_id=$(. "$OS_RELEASE" && printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
    # shellcheck disable=SC1090
    DISTRO_NAME=$(. "$OS_RELEASE" && printf '%s' "${PRETTY_NAME:-${NAME:-Linux}}")
    case " $(lower "$os_id") " in
        *" debian "*|*" ubuntu "*) DISTRO=debian ;;
        *" fedora "*|*" rhel "*|*" centos "*) DISTRO=fedora ;;
        *suse*) DISTRO=suse ;;  # opensuse-tumbleweed, opensuse-leap, sles
    esac
fi
if [[ $DISTRO == arch ]] && ! command -v pacman >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then DISTRO=debian
    elif command -v dnf >/dev/null 2>&1; then DISTRO=fedora
    elif command -v zypper >/dev/null 2>&1; then DISTRO=suse
    fi
fi
if (( MACOS )); then
    DISTRO=macos
    DISTRO_NAME="macOS $(sw_vers -productVersion 2>/dev/null || true)"
    DESKTOP=macos
fi

if [[ -z $DESKTOP ]]; then
    if in_hyprland_session; then
        DESKTOP=hyprland
    elif [[ ${XDG_CURRENT_DESKTOP:-} == *GNOME* ]]; then
        DESKTOP=gnome
    elif command -v hyprctl >/dev/null 2>&1 || [[ -d ${XDG_CONFIG_HOME:-$HOME/.config}/hypr ]]; then
        DESKTOP=hyprland
    elif command -v gnome-shell >/dev/null 2>&1; then
        DESKTOP=gnome
    elif [[ $DISTRO != arch ]]; then
        DESKTOP=gnome
    else
        DESKTOP=hyprland
    fi
fi
DESKTOP=$(lower "$DESKTOP")
[[ $DESKTOP == hyprland || $DESKTOP == gnome ]] || (( MACOS )) \
    || die "$(t '--desktop: hyprland oppure gnome' '--desktop: hyprland or gnome')"
if [[ $DISTRO != arch && $DISTRO != macos && $DESKTOP == hyprland ]]; then
    warn "$(t "Su $DISTRO_NAME Dropwheel supporta GNOME: Hyprland 0.57 e Quickshell non sono nei repository. Continuo, ma i pacchetti di Hyprland dovrai installarli tu." \
              "On $DISTRO_NAME Dropwheel supports GNOME: Hyprland 0.57 and Quickshell are not in the repositories. Continuing, but you will have to install the Hyprland packages yourself.")"
fi

if [[ $DESKTOP == gnome ]]; then desktop_label="GNOME"; else desktop_label="Hyprland"; fi
if (( MACOS )); then desktop_label="Finder ($(t 'anteprima' 'preview'))"; fi

if [[ -z ${DROPWHEEL_INSTALL_CLONED:-} ]]; then
    printf '\n'
    field "$(t 'Sistema' 'System')" "$DISTRO_NAME" "$C_DIM"
    field "Desktop" "$desktop_label" "$C_DIM"
    field "$(t 'Cartella' 'Folder')" "$PREFIX" "$C_DIM"
fi
if interactive && [[ -z ${DROPWHEEL_INSTALL_ASKED:-} ]]; then
    printf '\n'
    ask "$(t 'Iniziamo?' 'Shall we start?')" y y || exit 0
    export DROPWHEEL_INSTALL_ASKED=1
fi

# --- macOS: Homebrew (packages without sudo) and its Python
BREW=""
if (( MACOS )); then
    BREW=$(command -v brew || true)
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -z $BREW && -x $b ]]; then BREW=$b; fi
    done
    if [[ -z $BREW ]] && (( DO_PACKAGES )) && interactive; then
        warn "$(t 'Homebrew non è installato: serve per Python e per gli strumenti delle azioni.' \
                  'Homebrew is not installed: it provides Python and the tools of the actions.')"
        info "$(t 'Il comando ufficiale (https://brew.sh) è:' 'Its official command (https://brew.sh) is:')"
        # shellcheck disable=SC2016 # printed as it is, for the user to read
        cmd '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        if ask "$(t 'Lo eseguo ora? (chiederà la tua password)' 'Run it now? (it will ask for your password)')" n n; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
                || warn "$(t 'Installazione di Homebrew non riuscita' 'Homebrew installation failed')"
            for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                if [[ -z $BREW && -x $b ]]; then BREW=$b; fi
            done
        fi
    fi
    # this script, and what it starts, finds the Homebrew programs even before the shell profile does
    if [[ -n $BREW ]]; then PATH="$(dirname -- "$BREW"):$PATH"; fi
fi

# --- the source tree: this folder, or a fresh clone when the script came from curl
if [[ ! -e $SCRIPT_DIR/src/dropwheel/__init__.py ]]; then
    # "curl … | bash": bash reads this script from the pipe, so the questions need the terminal.
    # This whole if-block is parsed before it runs and it ends with exec, so switching stdin here
    # does not cut the script short.
    if [[ ! -t 0 ]] && (( ! ASSUME_YES )) && { true < /dev/tty; } 2>/dev/null; then
        exec < /dev/tty
        if [[ -z ${DROPWHEEL_INSTALL_ASKED:-} ]]; then
            printf '\n'
            ask "$(t 'Iniziamo?' 'Shall we start?')" y y || exit 0
            export DROPWHEEL_INSTALL_ASKED=1
        fi
    fi
    SRC_CLONE="$HOME/.local/src/dropwheel"
    step "$(t 'Scarico Dropwheel' 'Downloading Dropwheel')"
    if ! command -v git >/dev/null 2>&1; then
        if (( DO_PACKAGES )) && interactive && ask "$(t 'Serve git. Lo installo ora?' 'git is needed. Install it now?')" y n; then
            case $DISTRO in
                macos)
                    [[ -n $BREW ]] || die "$(t 'Senza Homebrew: xcode-select --install installa git.' 'Without Homebrew: xcode-select --install installs git.')"
                    "$BREW" install git ;;
                debian) as_root apt-get install -y git ;;
                fedora) as_root dnf install -y git ;;
                suse) as_root zypper --non-interactive install git ;;
                *) as_root pacman -S --needed --noconfirm git ;;
            esac
        else
            die "$(t 'Installa git e riprova.' 'Install git and try again.')"
        fi
    fi
    if [[ -d $SRC_CLONE/.git ]]; then
        git -C "$SRC_CLONE" pull --ff-only || die "$(t 'Aggiornamento di' 'Cannot update') $SRC_CLONE"
    else
        mkdir -p -- "$(dirname -- "$SRC_CLONE")"
        git clone --depth 1 "${DROPWHEEL_REPO:-https://github.com/haxurus/dropwheel.git}" "$SRC_CLONE" || die "git clone"
    fi
    ok "$SRC_CLONE"
    DROPWHEEL_INSTALL_CLONED=1 DROPWHEEL_INSTALL_STEP=$STEP_NO exec bash "$SRC_CLONE/install.sh" "${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}"
fi

for required in src/dropwheel/__init__.py src/dropwheel/__main__.py shell/shell.qml \
                integrations/hyprland/dropwheel.lua integrations/desktop/dropwheel.desktop \
                assets/dropwheel.svg uninstall.sh; do
    [[ -e $SCRIPT_DIR/$required ]] || die "$(t 'File mancante nel sorgente' 'Missing file in the source tree'): $required"
done

python_ok() { [[ -x $1 ]] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }
if [[ -z $PYTHON_BIN ]] && (( MACOS )); then
    # /usr/bin/python3 is the Command Line Tools' 3.9: Homebrew's Python instead
    BREW_PREFIX=""
    if [[ -n $BREW ]]; then BREW_PREFIX=$("$BREW" --prefix 2>/dev/null || true); fi
    if [[ -n $BREW_PREFIX ]] && ! python_ok "$BREW_PREFIX/bin/python3" && (( DO_PACKAGES )) \
            && ask "$(t 'Serve Python 3.11 o più recente. Installo python@3.13 con Homebrew?' \
                        'Python 3.11 or newer is needed. Install python@3.13 with Homebrew?')" y n; then
        "$BREW" install python@3.13 || warn "brew install python@3.13"
    fi
    for cand in "$BREW_PREFIX/bin/python3" "$(command -v python3 || true)"; do
        if [[ -z $PYTHON_BIN ]] && python_ok "$cand"; then PYTHON_BIN=$cand; fi
    done
    [[ -n $PYTHON_BIN ]] || die "$(t 'Serve Python 3.11 o più recente: brew install python@3.13' \
                                     'Python 3.11 or newer is required: brew install python@3.13')"
fi
if [[ -z $PYTHON_BIN ]]; then
    if [[ -x /usr/bin/python3 ]]; then
        PYTHON_BIN=/usr/bin/python3
    else
        PYTHON_BIN=$(command -v python3 || true)
    fi
fi
[[ -n $PYTHON_BIN && -x $PYTHON_BIN ]] || die "$(t 'Python 3 non trovato: sudo pacman -S python (Arch), sudo apt install python3 (Debian, Ubuntu), sudo dnf install python3 (Fedora) oppure sudo zypper install python3 (openSUSE)' 'Python 3 not found: sudo pacman -S python (Arch), sudo apt install python3 (Debian, Ubuntu), sudo dnf install python3 (Fedora) or sudo zypper install python3 (openSUSE)')"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    die "$(t 'Serve Python 3.11 o più recente' 'Python 3.11 or newer is required'): $("$PYTHON_BIN" -V 2>&1)"
fi

# --------------------------------------------------------------------------------------------
# Dependencies: checked, then installed with pacman, apt, dnf or zypper once you say yes
# --------------------------------------------------------------------------------------------

# Arch with Hyprland (the same list as the PKGBUILD's depends)
REQUIRED_PKGS=(python python-pillow quickshell hyprland wl-clipboard libnotify
               ttf-material-symbols-variable glib2 xdg-utils)
# Arch with GNOME: PySide6 hosts the wheel instead of Quickshell
ARCH_GNOME_PKGS=(python python-pillow pyside6 wl-clipboard libnotify ttf-material-symbols-variable glib2 xdg-utils)
# Debian and Ubuntu with GNOME: PySide6 comes from pip into Dropwheel's own environment; the
# libraries below are what its X11 plugin needs (most are already there on a desktop)
DEB_REQUIRED=(python3 python3-venv python3-pil wl-clipboard libnotify-bin libglib2.0-bin xdg-utils fontconfig
              libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0
              libxcb-xkb1 libxkbcommon-x11-0 libegl1 libxext6)
# Fedora and openSUSE with GNOME: PySide6 is packaged (its dependencies bring Qt and the X11 plugin);
# openSUSE ships the QML modules (QtQuick, Shapes, Effects) in a package of their own
FEDORA_REQUIRED=(python3 python3-pillow python3-pyside6 wl-clipboard libnotify glib2 xdg-utils fontconfig)
SUSE_REQUIRED=(python3 python3-Pillow python3-pyside6 qt6-declarative-imports wl-clipboard libnotify-tools glib2-tools
               xdg-utils fontconfig)

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

# Debian and Ubuntu: "labels|apt packages|pip modules" (same groups; "a/b" = the first apt knows)
DEB_GROUPS=(
    "Immagini|Images|imagemagick librsvg2-bin oxipng pngquant jpegoptim libjpeg-turbo-progs gifsicle|pillow-heif pillow-jxl-plugin"
    "Metadati|Metadata|libimage-exiftool-perl mat2|"
    "PDF|PDF|qpdf ghostscript poppler-utils|pymupdf pypdf pikepdf"
    "OCR e codici QR|OCR and QR codes|tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng zbar-tools ocrmypdf|qrcode"
    "Documenti|Documents|libreoffice pandoc typst|openpyxl charset-normalizer pyyaml tomli-w pygments python-docx defusedxml"
    "Audio e video|Audio and video|ffmpeg|mutagen"
    "Archivi|Archives|7zip/p7zip-full unrar|py7zr rarfile"
    "Font|Fonts|fonttools woff2|fonttools brotli"
    "Condivisione e sistema|Sharing and system|zenity gnupg trash-cli udisks2|send2trash"
)

# Fedora: "labels|dnf packages|pip modules" (RPM Fusion's ffmpeg when that repository is enabled)
FEDORA_GROUPS=(
    "Immagini|Images|ImageMagick librsvg2-tools oxipng pngquant jpegoptim libjpeg-turbo-utils gifsicle|pillow-heif pillow-jxl-plugin"
    "Metadati|Metadata|perl-Image-ExifTool mat2|"
    "PDF|PDF|qpdf ghostscript poppler-utils|pymupdf pypdf pikepdf"
    "OCR e codici QR|OCR and QR codes|tesseract tesseract-langpack-ita tesseract-langpack-eng zbar ocrmypdf|qrcode"
    "Documenti|Documents|libreoffice pandoc-cli|openpyxl charset-normalizer pyyaml tomli-w pygments python-docx defusedxml"
    "Audio e video|Audio and video|ffmpeg/ffmpeg-free|mutagen"
    "Archivi|Archives|7zip unrar|py7zr rarfile"
    "Font|Fonts|fonttools woff2-tools|fonttools brotli"
    "Condivisione e sistema|Sharing and system|zenity gnupg2 trash-cli udisks2|send2trash"
)

# openSUSE Tumbleweed and Leap: "labels|zypper packages|pip modules" (some are Tumbleweed only)
SUSE_GROUPS=(
    "Immagini|Images|ImageMagick rsvg-convert oxipng pngquant jpegoptim libjpeg-turbo gifsicle|pillow-heif pillow-jxl-plugin"
    "Metadati|Metadata|exiftool|"
    "PDF|PDF|qpdf ghostscript poppler-tools|pymupdf pypdf pikepdf"
    "OCR e codici QR|OCR and QR codes|tesseract-ocr tesseract-ocr-traineddata-ita tesseract-ocr-traineddata-eng zbar python3-ocrmypdf|qrcode"
    "Documenti|Documents|libreoffice pandoc-cli typst|openpyxl charset-normalizer pyyaml tomli-w pygments python-docx defusedxml"
    "Audio e video|Audio and video|ffmpeg|mutagen"
    "Archivi|Archives|7zip unrar|py7zr rarfile"
    "Font|Fonts|python3-FontTools woff2|fonttools brotli"
    "Condivisione e sistema|Sharing and system|zenity gpg2 trash-cli udisks2|send2trash"
)

# macOS: "labels|Homebrew formulae (cask:NAME = an app)|pip modules" (names checked on formulae.brew.sh)
MAC_GROUPS=(
    "Immagini|Images|imagemagick librsvg oxipng pngquant jpegoptim jpeg-turbo gifsicle gifski|pillow-heif pillow-jxl-plugin"
    "Metadati|Metadata|exiftool mat2|"
    "PDF|PDF|qpdf ghostscript poppler|pymupdf pypdf pikepdf"
    "OCR e codici QR|OCR and QR codes|tesseract tesseract-lang zbar qrencode ocrmypdf|qrcode"
    "Documenti|Documents|cask:libreoffice pandoc typst|openpyxl charset-normalizer pyyaml tomli-w pygments python-docx defusedxml"
    "Audio e video|Audio and video|ffmpeg yt-dlp|mutagen"
    "Archivi|Archives|sevenzip unar|py7zr rarfile"
    "Font|Fonts|woff2|fonttools brotli"
    "Condivisione e sistema|Sharing and system|gnupg|"
)

case $DISTRO in
    macos) PKG_TOOL=brew ;;
    debian) PKG_TOOL=apt-get ;;
    fedora) PKG_TOOL=dnf ;;
    suse) PKG_TOOL=zypper ;;
    *) PKG_TOOL=pacman ;;
esac
can_install() {
    (( DO_PACKAGES )) && interactive && command -v "$PKG_TOOL" >/dev/null 2>&1 \
        && { [[ $DISTRO == macos ]] || command -v sudo >/dev/null 2>&1; }
}

install_cmd_text() {
    case $DISTRO in
        macos)
            brew_split "$@"
            (( ${#BREW_F[@]} )) && printf 'brew install %s' "${BREW_F[*]}"
            (( ${#BREW_F[@]} && ${#BREW_C[@]} )) && printf ' && '
            (( ${#BREW_C[@]} )) && printf 'brew install --cask %s' "${BREW_C[*]}"
            return 0 ;;
        debian) printf 'sudo apt install %s' "$*" ;;
        fedora) printf 'sudo dnf install %s' "$*" ;;
        suse) printf 'sudo zypper install %s' "$*" ;;
        *) printf 'sudo pacman -S --needed %s' "$*" ;;
    esac
}

APT_UPDATED=0
install_pkgs() {
    (( $# )) || return 0
    case $DISTRO in
        macos)
            brew_split "$@"
            local rc=0
            if (( ${#BREW_F[@]} )); then "$BREW" install -- "${BREW_F[@]}" || rc=1; fi
            if (( ${#BREW_C[@]} )); then "$BREW" install --cask -- "${BREW_C[@]}" || rc=1; fi
            brew_list
            return $rc ;;
        debian)
            if (( ! APT_UPDATED )); then
                as_root apt-get update || warn "apt-get update"
                APT_UPDATED=1
            fi
            as_root apt-get install -y -- "$@" ;;
        fedora) as_root dnf install -y "$@" ;;
        suse) as_root zypper --non-interactive install "$@" ;;
        *) as_root pacman -S --needed --noconfirm -- "$@" ;;
    esac
}

# the first of "a/b" that is installed, else the first one the package manager knows
pick_alternative() {
    local alt
    local -a alts
    IFS='/' read -r -a alts <<< "$1"
    for alt in "${alts[@]}"; do
        pkg_installed "$alt" && return 1
    done
    if [[ $DISTRO != arch && $DISTRO != macos ]]; then
        for alt in "${alts[@]}"; do
            if pkg_available "$alt"; then printf '%s' "$alt"; return 0; fi
        done
        return 1
    fi
    printf '%s' "${alts[0]}"
}

missing_required=()
missing_optional=()
missing_aur=()
PIP_MODULES=()

step "$(t 'Dipendenze' 'Dependencies')"
if [[ $DISTRO == macos ]]; then
    required_list=()  # Python was checked above; PySide6 and Pillow go into the private environment
    brew_list
elif [[ $DISTRO == debian ]]; then
    required_list=("${DEB_REQUIRED[@]}")
elif [[ $DISTRO == fedora ]]; then
    required_list=("${FEDORA_REQUIRED[@]}")
elif [[ $DISTRO == suse ]]; then
    required_list=("${SUSE_REQUIRED[@]}")
elif [[ $DESKTOP == gnome ]]; then
    required_list=("${ARCH_GNOME_PKGS[@]}")
else
    required_list=("${REQUIRED_PKGS[@]}")
fi

if command -v "$PKG_TOOL" >/dev/null 2>&1; then
    for p in ${required_list[@]+"${required_list[@]}"}; do
        pkg_installed "$p" || missing_required+=("$p")
    done
    if (( ${#missing_required[@]} == 0 )); then
        ok "$(t 'Dipendenze obbligatorie presenti' 'All required dependencies are installed')"
    else
        warn "$(t 'Dipendenze obbligatorie mancanti' 'Missing required dependencies'): ${missing_required[*]}"
        if can_install && ask "$(t 'Le installo ora? (sudo chiederà la tua password)' 'Install them now? (sudo will ask for your password)')" y n; then
            if install_pkgs "${missing_required[@]}"; then
                ok "$(t 'Dipendenze obbligatorie installate' 'Required dependencies installed')"
                missing_required=()
            else
                warn "$(t 'Installazione non riuscita' 'Installation failed')"
            fi
        else
            cmd "$(install_cmd_text "${missing_required[@]}")"
        fi
    fi

    # --- optional groups: a menu of what is still missing
    group_labels=()
    group_pkgs=()
    group_pip=()
    case $DISTRO in
        macos) groups=("${MAC_GROUPS[@]}") ;;
        debian) groups=("${DEB_GROUPS[@]}") ;;
        fedora) groups=("${FEDORA_GROUPS[@]}") ;;
        suse) groups=("${SUSE_GROUPS[@]}") ;;
        *) groups=("${OPTIONAL_GROUPS[@]}") ;;
    esac
    for group in "${groups[@]}"; do
        IFS='|' read -r label_it label_en pkgs pip_mods <<< "$group"
        group_missing=()
        for p in $pkgs; do
            pick=$(pick_alternative "$p") && group_missing+=("$pick")
        done
        (( ${#group_missing[@]} )) || [[ -n ${pip_mods:-} ]] || continue
        group_labels+=("$(printf '%-24s %s' "$(t "$label_it" "$label_en")" "$C_DIM${group_missing[*]:-}${pip_mods:+ (pip: $pip_mods)}$C_RESET")")
        group_pkgs+=("${group_missing[*]:-}")
        group_pip+=("${pip_mods:-}")
    done
    if (( ${#group_labels[@]} )); then
        info "$(t 'Funzioni facoltative: ogni gruppo abilita altre azioni della ruota.' \
                  'Optional features: each group enables more actions of the wheel.')"
        if can_install; then
            choose_groups "$(t 'Gruppi disponibili' 'Available groups')" "${group_labels[@]}"
        else
            for i in "${!group_labels[@]}"; do
                printf '      %s\n' "${group_labels[i]}"
            done
            CHOSEN=()
        fi
        for i in "${CHOSEN[@]+"${CHOSEN[@]}"}"; do
            read -r -a words <<< "${group_pkgs[i]}"
            missing_optional+=("${words[@]+"${words[@]}"}")
            read -r -a words <<< "${group_pip[i]}"
            PIP_MODULES+=("${words[@]+"${words[@]}"}")
        done
        if can_install && (( ${#missing_optional[@]} )); then
            if install_pkgs "${missing_optional[@]}"; then
                ok "$(t 'Pacchetti facoltativi installati' 'Optional packages installed')"
            else
                warn "$(t 'Alcuni pacchetti non si sono installati: dropwheel doctor dice cosa manca.' \
                          'Some packages did not install: dropwheel doctor tells what is missing.')"
            fi
        elif ! can_install; then
            all_missing=()
            for i in "${!group_pkgs[@]}"; do
                read -r -a words <<< "${group_pkgs[i]}"
                all_missing+=("${words[@]+"${words[@]}"}")
            done
            (( ${#all_missing[@]} )) && cmd "$(install_cmd_text "${all_missing[@]}")"
        fi
    else
        ok "$(t 'Tutte le dipendenze opzionali consigliate sono presenti' 'All suggested optional dependencies are installed')"
    fi

    if [[ $DISTRO == arch ]]; then
        for p in "${AUR_SUGGESTED[@]}"; do
            pkg_installed "$p" || missing_aur+=("$p")
        done
        if (( ${#missing_aur[@]} )); then
            aur_helper=""
            for h in paru yay; do
                command -v "$h" >/dev/null 2>&1 && { aur_helper=$h; break; }
            done
            info "$(t 'Dall'"'"'AUR (OCR dei PDF, PDF → Word):' 'From the AUR (PDF OCR, PDF → Word):')"
            if [[ -n $aur_helper ]] && can_install && ask "$(t "Li installo con $aur_helper?" "Install them with $aur_helper?")" n n; then
                "$aur_helper" -S --needed -- "${missing_aur[@]}" || warn "$aur_helper"
            else
                cmd "${aur_helper:-paru} -S ${missing_aur[*]}"
            fi
        fi
        if pkg_installed dropwheel; then
            warn "$(t 'Il pacchetto di sistema "dropwheel" è già installato: questa installazione utente avrà la precedenza (~/.local/share/dropwheel). Di solito ne basta una.' \
                      'The "dropwheel" system package is installed too: this user install takes precedence (~/.local/share/dropwheel). Usually one is enough.')"
            ask "$(t 'Continuare comunque?' 'Continue anyway?')" n y || exit 1
        fi
    fi
    if ! command -v rembg >/dev/null 2>&1; then
        info "$(t 'Rimozione dello sfondo (facoltativa, scarica un modello AI):' 'Background removal (optional, downloads an AI model):')"
        cmd "pipx install \"rembg[cpu,cli]\""
    fi
else
    warn "$(t "$PKG_TOOL non trovato: controllo delle dipendenze saltato." "$PKG_TOOL not found: dependency check skipped.")"
fi

# --- Debian, Ubuntu and macOS: Dropwheel's own Python environment (PySide6 and the chosen modules);
#     Fedora and openSUSE: only when pip has modules to install (PySide6 comes from the system)
VENV_DIR="$PREFIX/share/dropwheel/venv"
PIP_BASE=PySide6-Essentials
if [[ $DISTRO == macos ]]; then PIP_BASE="PySide6-Essentials pillow"; fi  # Debian has python3-pil from apt
if [[ $DISTRO == fedora || $DISTRO == suse ]] && { (( ${#PIP_MODULES[@]} )) || [[ -x $VENV_DIR/bin/python ]]; }; then
    step "$(t 'Ambiente Python' 'Python environment')"
    if [[ -x $VENV_DIR/bin/python ]] || "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"; then
        if (( ${#PIP_MODULES[@]} )); then
            info "$(t 'Installo con pip:' 'Installing with pip:') ${PIP_MODULES[*]}"
            if "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q --upgrade -- "${PIP_MODULES[@]}"; then
                ok "$(t 'ambiente Python' 'Python environment') → $VENV_DIR"
            else
                warn "$(t 'pip non è riuscito a installare tutto: riprova più tardi con' 'pip could not install everything: try again later with')"
                cmd "$VENV_DIR/bin/python -m pip install ${PIP_MODULES[*]}"
            fi
        fi
        PYTHON_BIN="$VENV_DIR/bin/python"
    else
        warn "$(t "Impossibile creare l'ambiente Python ($PYTHON_BIN -m venv): moduli pip saltati" \
                  "Cannot create the Python environment ($PYTHON_BIN -m venv): pip modules skipped")"
    fi
elif [[ $DISTRO == debian || $DISTRO == macos ]] && (( ! DO_PACKAGES )); then
    info "$(t 'Ambiente Python saltato (--no-packages). Per crearlo:' 'Python environment skipped (--no-packages). To create it:')"
    cmd "$PYTHON_BIN -m venv --system-site-packages $VENV_DIR && $VENV_DIR/bin/python -m pip install $PIP_BASE"
elif [[ $DISTRO == debian || $DISTRO == macos ]]; then
    step "$(t 'Ambiente Python' 'Python environment')"
    if [[ ! -x $VENV_DIR/bin/python ]]; then
        "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR" \
            || die "$(t 'Impossibile creare l'"'"'ambiente Python: serve python3-venv' 'Cannot create the Python environment: python3-venv is needed')"
    fi
    read -r -a pip_base <<< "$PIP_BASE"
    pip_list=("${pip_base[@]}" ${PIP_MODULES[@]+"${PIP_MODULES[@]}"})
    info "$(t 'Installo con pip (PySide6 sono circa 80 MB):' 'Installing with pip (PySide6 is about 80 MB):') ${pip_list[*]}"
    venv_pip() { "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q --upgrade "$@"; }
    # --ignore-installed: a system PySide6 (python3-pyside6) would otherwise lend its shiboken6,
    # whose library is not where the pip build looks for it
    if venv_pip --ignore-installed -- "${pip_base[@]}" \
            && { (( ${#PIP_MODULES[@]} == 0 )) || venv_pip -- "${PIP_MODULES[@]}"; }; then
        ok "$(t 'ambiente Python' 'Python environment') → $VENV_DIR"
    else
        warn "$(t 'pip non è riuscito a installare tutto: riprova più tardi con' 'pip could not install everything: try again later with')"
        cmd "$VENV_DIR/bin/python -m pip install ${pip_list[*]}"
    fi
    PYTHON_BIN="$VENV_DIR/bin/python"
fi

if ! "$PYTHON_BIN" -c 'import PIL' >/dev/null 2>&1; then
    warn "$(t 'Pillow non è importabile da' 'Pillow cannot be imported by') $PYTHON_BIN"
fi
if [[ $DESKTOP == hyprland ]] && ! command -v qs >/dev/null 2>&1; then
    warn "$(t 'Quickshell (comando qs) non trovato: la ruota non potrà partire finché non lo installi.' \
              'Quickshell (qs command) not found: the wheel cannot start until you install it.')"
fi
if [[ $DESKTOP == gnome || $DESKTOP == macos ]] && ! "$PYTHON_BIN" -c 'import PySide6.QtQuick' >/dev/null 2>&1; then
    warn "$(t 'PySide6 non è importabile: la ruota non potrà partire finché non lo installi.' \
              'PySide6 cannot be imported: the wheel cannot start until you install it.')"
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
if (( UPGRADE && DO_START )) && { in_hyprland_session || [[ $DESKTOP == gnome || $DESKTOP == macos ]]; }; then
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

GUARD_TYPES=()
if (( DO_MIME_GUARD && ! MACOS )) && command -v xdg-mime >/dev/null 2>&1; then
    declare -A PREVIOUS_DEFAULT=()  # bash 4 (associative array, mapfile): never reached on macOS
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
mkdir -p -- "$BIN_DIR" "$DATA_DIR" "$PY_DIR"

sync_dir "$SCRIPT_DIR/src" dropwheel "$PY_DIR"
"$PYTHON_BIN" -m compileall -q "$PY_DIR/dropwheel" >/dev/null 2>&1 || warn "$(t 'compilazione dei .pyc non riuscita (non grave)' 'byte-compiling failed (harmless)')"
ok "$(t 'pacchetto Python' 'Python package') → $PY_DIR/dropwheel"

sync_dir "$SCRIPT_DIR" shell "$DATA_DIR"
ok "$(t 'interfaccia' 'interface') → $DATA_DIR/shell"

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

# the application menu entry and icon (macOS: the Quick Action of "dropwheel setup macos" instead)
if (( ! MACOS )); then
    mkdir -p -- "$APP_DIR" "$ICON_DIR"
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
fi

# --------------------------------------------------------------------------------------------
# File managers
# --------------------------------------------------------------------------------------------

if (( DO_FILE_MANAGERS && ! MACOS )); then
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
        shell_name=$(basename -- "${SHELL:-bash}")
        case $shell_name in
            fish) rc="$XDG_CONFIG_HOME_DIR/fish/conf.d/dropwheel-path.fish"; line="fish_add_path $(sq "$BIN_DIR")" ;;
            zsh) rc="$HOME/.zshrc"; line="export PATH=$(sq "$BIN_DIR"):\"\$PATH\"" ;;
            *) rc="$HOME/.bashrc"; line="export PATH=$(sq "$BIN_DIR"):\"\$PATH\"" ;;
        esac
        if interactive && ask "$(t "Lo aggiungo al PATH in $rc?" "Add it to PATH in $rc?")" y n; then
            mkdir -p -- "$(dirname -- "$rc")"
            printf '\n# Dropwheel (install.sh)\n%s\n' "$line" >> "$rc"
            ok "$(t 'PATH aggiornato: vale nei terminali che apri da ora' 'PATH updated: it applies to the terminals you open from now on')"
        else
            info "fish: fish_add_path $BIN_DIR"
            info "bash: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc"
            info "zsh:  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc"
        fi
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
gnome_configured=0
if (( DO_HYPRLAND )) && [[ $DESKTOP == gnome ]]; then
    step "GNOME"
    info "$(t '"dropwheel setup gnome" installa una piccola estensione di GNOME Shell (dice a Dropwheel dove sta il puntatore e se Shift è premuto), la scorciatoia Ctrl+Alt+D, l'"'"'avvio all'"'"'accesso e il font delle icone.' \
              '"dropwheel setup gnome" installs a small GNOME Shell extension (it tells Dropwheel where the pointer is and whether Shift is held), the Ctrl+Alt+D shortcut, starting at login and the icon font.')"
    if ask "$(t 'Configuro GNOME adesso?' 'Set up GNOME now?')" y n; then
        if "$DW_BIN" setup gnome; then
            gnome_configured=1
        else
            warn "$(t 'Configurazione non completa: dropwheel doctor dice cosa manca.' 'Setup not complete: dropwheel doctor tells what is missing.')"
        fi
    else
        info "$(t 'Quando vuoi:' 'Whenever you like:')"
        cmd "dropwheel setup gnome"
    fi
fi
mac_configured=0
if (( DO_HYPRLAND )) && [[ $DESKTOP == macos ]]; then
    step "macOS"
    info "$(t '"dropwheel setup macos" aggiunge l'"'"'azione rapida "Apri in Dropwheel" al Finder, l'"'"'avvio all'"'"'accesso (un LaunchAgent) e il font delle icone.' \
              '"dropwheel setup macos" adds the "Open in Dropwheel" Quick Action to the Finder, starting at login (a LaunchAgent) and the icon font.')"
    if ask "$(t 'Configuro macOS adesso?' 'Set up macOS now?')" y n; then
        if "$DW_BIN" setup macos; then
            mac_configured=1
        else
            warn "$(t 'Configurazione non completa: dropwheel doctor dice cosa manca.' 'Setup not complete: dropwheel doctor tells what is missing.')"
        fi
    else
        info "$(t 'Quando vuoi:' 'Whenever you like:')"
        cmd "dropwheel setup macos"
    fi
fi
if (( DO_HYPRLAND )) && [[ $DESKTOP == hyprland ]]; then
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
    if [[ $DESKTOP == macos ]]; then
        if "$DW_BIN" start; then
            ok "$(t 'Dropwheel è in esecuzione' 'Dropwheel is running')"
            started=1
        else
            warn "$(t 'Avvio non riuscito: controlla con "dropwheel doctor".' 'Start failed: check with "dropwheel doctor".')"
        fi
    elif [[ $DESKTOP == gnome ]]; then
        if [[ ${XDG_CURRENT_DESKTOP:-} != *GNOME* ]]; then
            info "$(t 'Non sei in una sessione GNOME: Dropwheel partirà da solo al prossimo accesso.' \
                      'Not in a GNOME session: Dropwheel will start by itself at the next login.')"
        elif "$DW_BIN" start; then
            ok "$(t 'Dropwheel è in esecuzione' 'Dropwheel is running')"
            started=1
        else
            warn "$(t 'Avvio non riuscito: controlla con "dropwheel doctor".' 'Start failed: check with "dropwheel doctor".')"
        fi
    elif ! in_hyprland_session; then
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
if [[ $DESKTOP == macos ]]; then
    if (( IT )); then
        info "${C_BOLD}Anteprima${C_RESET}: Dropwheel per macOS non è ancora stato provato su un Mac vero."
        info "Mentre trascini dei file dal Finder premi ${C_BOLD}Shift${C_RESET}: compare la ruota sotto il puntatore."
        info "Senza trascinare: tasto destro sui file → Azioni rapide → Apri in Dropwheel."
        (( mac_configured )) || info "Per l'azione rapida e l'avvio all'accesso serve prima: dropwheel setup macos"
        [[ -z $start_hint ]] || info "$start_hint"
        info "Controllo degli strumenti opzionali: dropwheel doctor"
        info "Disinstallazione: $DATA_DIR/uninstall.sh"
    else
        info "${C_BOLD}Preview${C_RESET}: Dropwheel for macOS has not been tried on a real Mac yet."
        info "While dragging files from the Finder press ${C_BOLD}Shift${C_RESET}: the wheel appears under the pointer."
        info "Without dragging: right click the files → Quick Actions → Open in Dropwheel."
        (( mac_configured )) || info "The Quick Action and starting at login need: dropwheel setup macos"
        [[ -z $start_hint ]] || info "$start_hint"
        info "Check optional tools: dropwheel doctor"
        info "Uninstall: $DATA_DIR/uninstall.sh"
    fi
elif [[ $DESKTOP == gnome ]]; then
    if (( IT )); then
        (( gnome_configured )) && info "${C_BOLD}Esci e rientra una volta${C_RESET}: GNOME carica l'estensione solo all'accesso."
        info "Mentre trascini dei file da Nautilus premi ${C_BOLD}Shift${C_RESET}: compare la ruota sotto il puntatore."
        info "Senza trascinare: copia dei file e premi ${C_BOLD}Ctrl+Alt+D${C_RESET}, oppure tasto destro → Script → Dropwheel."
        (( gnome_configured )) || info "Per l'estensione e la scorciatoia serve prima: dropwheel setup gnome"
        [[ -z $start_hint ]] || info "$start_hint"
        info "Controllo degli strumenti opzionali: dropwheel doctor"
        info "Disinstallazione: $DATA_DIR/uninstall.sh"
    else
        (( gnome_configured )) && info "${C_BOLD}Log out and back in once${C_RESET}: GNOME loads the extension only at login."
        info "While dragging files from Nautilus press ${C_BOLD}Shift${C_RESET}: the wheel appears under the pointer."
        info "Without dragging: copy some files and press ${C_BOLD}Ctrl+Alt+D${C_RESET}, or right click → Scripts → Dropwheel."
        (( gnome_configured )) || info "The extension and the shortcut need: dropwheel setup gnome"
        [[ -z $start_hint ]] || info "$start_hint"
        info "Check optional tools: dropwheel doctor"
        info "Uninstall: $DATA_DIR/uninstall.sh"
    fi
elif (( IT )); then
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
