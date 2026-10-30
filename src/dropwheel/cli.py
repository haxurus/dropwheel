"""Command line interface: ``dropwheel <command> …`` (entry point ``dropwheel = dropwheel.cli:main``).

Commands (``dropwheel --help`` lists them in the user's language, Italian or English):

* shell control: ``start``, ``stop``, ``restart``, ``status``, ``open [FILES…]``;
* headless use: ``run ACTION [-p key=value …] FILES…``, ``menu FILES…``, ``actions``,
  ``undo [JOB]``, ``history``, ``shares``;
* set-up and diagnostics: ``doctor``, ``config``, ``setup TARGET``;
* internal: ``daemon`` (started by the QML shell), ``share-serve ID`` (started by
  :func:`dropwheel.share.start_share`).

Exit codes: 0 success, 1 failure, 2 usage error, 130 cancelled (Ctrl-C cancels a running job
cleanly: its processes are killed and partial outputs removed).
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TextIO
from xml.sax.saxutils import escape as xml_escape

from . import APP_NAME, __version__
from . import config as cfgmod
from . import i18n, tools, util
from .errors import UserError

log = logging.getLogger("dropwheel.cli")

__all__ = ["main", "build_parser", "REQUIRED_TOOLS", "EXIT_OK", "EXIT_FAIL", "EXIT_USAGE", "EXIT_CANCELLED",
           "parse_param_value", "resolve_inputs", "install_all_command"]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

#: Hard dependencies: (tool name, Arch package).  ``doctor`` exits with 1 when one is missing.
REQUIRED_TOOLS: tuple[tuple[str, str], ...] = (
    ("py:PIL", "python-pillow"),
    ("qs", "quickshell"),
    ("hyprctl", "hyprland"),
    ("wl-copy", "wl-clipboard"),
    ("wl-paste", "wl-clipboard"),
)

#: Programs that are a personal choice (terminals, file managers, viewers, package helpers…):
#: reported by ``doctor`` but never part of the "install everything" command.
CHOICE_TOOLS = frozenset({
    "foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "gnome-terminal", "xterm",
    "thunar", "nautilus", "dolphin", "nemo", "paru", "yay", "pacman", "wine", "flatpak", "okular", "mpv",
    "swww", "grim", "caelestia", "xournalpp", "pdfarranger", "dex", "xdg-terminal-exec", "localsend",
    "convert", "whisper",
})

#: Interchangeable tools: when one of a set is installed the others are not needed.
ALTERNATIVES: tuple[tuple[str, ...], ...] = (
    ("rsvg-convert", "resvg"),
    ("realesrgan", "waifu2x", "upscayl"),
    ("vtracer", "py:vtracer"),
    ("7z", "bsdtar"),
    ("unrar", "unar"),
    ("unar", "lsar", "7z"),
    ("whisper-cli", "whisper"),
    ("localsend-cli", "localsend"),
    ("gtk-launch", "dex"),
    ("markitdown", "py:markitdown"),
    ("pdf2docx", "py:pdf2docx"),
    ("weasyprint", "py:weasyprint"),
    ("fonttools", "py:fontTools"),
    ("pygmentize", "py:pygments"),
    ("tectonic", "xelatex", "typst"),
    ("curl", "wget"),
    ("pdftk", "qpdf"),
)

_GROUP_ORDER = ("image", "pdf", "docs", "media", "archive", "fonts", "system", "python")
_SETUP_TARGETS = ("hyprland", "nautilus", "dolphin", "nemo", "thunar", "desktop", "all")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+://")
_REQUIRE_RE = re.compile(r"""require\s*\(?\s*["']dropwheel["']""")
_HYPR_USER_RE = re.compile(r"""require\s*\(?\s*["']hypr-user["']|dofile\s*\(?.*hypr-user""")
#: What matters to find a top-level ``return`` in Lua source: comments and strings (skipped) and words.
_LUA_TOKEN_RE = re.compile(r"""
    --\[(?P<comment>=*)\[.*?\](?P=comment)\]    # long comment
  | --[^\n]*                                    # line comment
  | \[(?P<long>=*)\[.*?\](?P=long)\]            # long string
  | "(?:\\.|[^"\\\n])*"
  | '(?:\\.|[^'\\\n])*'
  | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
""", re.S | re.X)
_THUNAR_ID = "dropwheel-open-in-dropwheel"


class UsageError(Exception):
    """A command line mistake (exit status 2)."""


# =========================================================================== terminal output

_SYMBOLS = {
    "ok": ("✓", "+"), "fail": ("✗", "x"), "warn": ("⚠", "!"), "star": ("★", "*"), "arrow": ("→", "->"),
    "branch": ("├─ ", "|- "), "last": ("└─ ", "`- "), "pipe": ("│  ", "|  "), "space": ("   ", "   "),
    "ellipsis": ("…", "..."), "full": ("█", "#"), "empty": ("░", "-"), "dot": ("·", "-"), "dash": ("—", "-"),
}
_SPINNER_U = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_A = "|/-\\"


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _can_encode(text: str, encoding: str | None) -> bool:
    try:
        text.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class Term:
    """Styling for one output stream: ANSI colours on a TTY (``NO_COLOR`` respected) and
    Unicode symbols when the stream encoding can represent them."""

    def __init__(self, stream: TextIO | None) -> None:
        self.stream = stream
        self.tty = _isatty(stream)
        self.color = self.tty and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
        self.unicode = _can_encode("✓✗⚠★→├─└│…█░·—⠋", getattr(stream, "encoding", None))

    def sym(self, name: str) -> str:
        """A symbol (Unicode or its ASCII fallback)."""
        pair = _SYMBOLS[name]
        return pair[0] if self.unicode else pair[1]

    def _style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color and text else text

    def bold(self, text: str) -> str:
        return self._style(text, "1")

    def dim(self, text: str) -> str:
        return self._style(text, "2")

    def red(self, text: str) -> str:
        return self._style(text, "31")

    def green(self, text: str) -> str:
        return self._style(text, "32")

    def yellow(self, text: str) -> str:
        return self._style(text, "33")

    def cyan(self, text: str) -> str:
        return self._style(text, "36")

    def ok(self) -> str:
        return self.green(self.sym("ok"))

    def fail(self) -> str:
        return self.red(self.sym("fail"))

    def warn(self) -> str:
        return self.yellow(self.sym("warn"))

    def width(self) -> int:
        """Terminal width (80 when unknown)."""
        try:
            return max(40, shutil.get_terminal_size((80, 24)).columns)
        except (OSError, ValueError):
            return 80


def out(text: str = "") -> None:
    """Print a line on stdout."""
    stream = sys.stdout
    if stream is None:
        return
    stream.write(f"{text}\n")


def err(text: str = "") -> None:
    """Print a line on stderr."""
    stream = sys.stderr
    if stream is None:
        return
    stream.write(f"{text}\n")
    with contextlib.suppress(OSError, ValueError):
        stream.flush()


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def text_width(text: str) -> int:
    """Display width of ``text`` (ANSI codes ignored, wide characters count twice)."""
    return sum(_char_width(c) for c in _ANSI_RE.sub("", text))


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """Cut plain ``text`` to ``width`` display columns."""
    if text_width(text) <= width:
        return text
    out_chars: list[str] = []
    used = 0
    limit = max(0, width - text_width(ellipsis))
    for ch in text:
        w = _char_width(ch)
        if used + w > limit:
            break
        out_chars.append(ch)
        used += w
    return "".join(out_chars) + ellipsis


def pad(text: str, width: int) -> str:
    """Left-align ``text`` in ``width`` display columns."""
    return text + " " * max(0, width - text_width(text))


def _table(term: Term, headers: Sequence[str], rows: Sequence[Sequence[str]], *, flex: int = -1,
           min_flex: int = 12) -> list[str]:
    """Aligned columns; column ``flex`` is truncated so the table fits the terminal."""
    if not rows:
        return []
    cols = len(headers)
    widths = [max([text_width(headers[i])] + [text_width(r[i]) for r in rows]) for i in range(cols)]
    total = sum(widths) + 2 * (cols - 1)
    avail = term.width() if term.tty else 10_000
    flex_i = flex % cols
    if total > avail:
        widths[flex_i] = max(min_flex, widths[flex_i] - (total - avail))
    ell = term.sym("ellipsis")
    lines = ["  ".join(term.bold(pad(truncate(h, widths[i], ell), widths[i])) for i, h in enumerate(headers)).rstrip()]
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            plain = _ANSI_RE.sub("", cell)
            if text_width(plain) > widths[i]:
                cell = truncate(plain, widths[i], ell)
            cells.append(pad(cell, widths[i]) if i < cols - 1 else cell)
        lines.append("  ".join(cells).rstrip())
    return lines


def _columns(term: Term, items: Sequence[str], indent: int = 4) -> list[str]:
    """Lay ``items`` out in columns (like ``ls``) within the terminal width."""
    if not items:
        return []
    width = (term.width() if term.tty else 100) - indent
    col_w = max(text_width(i) for i in items) + 2
    ncols = max(1, width // max(1, col_w))
    nrows = (len(items) + ncols - 1) // ncols
    lines = []
    for r in range(nrows):
        cells = [items[c * nrows + r] for c in range(ncols) if c * nrows + r < len(items)]
        lines.append(" " * indent + "".join(pad(c, col_w) for c in cells).rstrip())
    return lines


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ============================================================================ context / i18n

@dataclass
class Ctx:
    """Shared state of one CLI invocation."""

    config: dict
    lang: str
    term: Term
    eterm: Term
    debug: bool = False

    def t(self, key: str, /, **fmt: Any) -> str:
        """Translate ``key`` into the CLI language."""
        return i18n.t(key, self.lang, **fmt)

    def tn(self, key: str, n: int, /, **fmt: Any) -> str:
        """Plural-aware translation."""
        return i18n.tn(key, n, self.lang, **fmt)


_ARGPARSE_IT = {
    "usage: ": "uso: ",
    "positional arguments": "argomenti",
    "options": "opzioni",
    "optional arguments": "opzioni",
    "show this help message and exit": "mostra questo aiuto ed esce",
    "show program's version number and exit": "mostra la versione ed esce",
    "the following arguments are required: %s": "mancano questi argomenti: %s",
    "unrecognized arguments: %s": "argomenti non riconosciuti: %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "scelta non valida: %(value)r (scegli tra %(choices)s)",
    "%(prog)s: error: %(message)s\n": "%(prog)s: errore: %(message)s\n",
    "expected one argument": "serve un valore",
    "expected at least one argument": "serve almeno un valore",
    "argument %(argument_name)s: %(message)s": "argomento %(argument_name)s: %(message)s",
    "invalid %(type)s value: %(value)r": "valore %(type)s non valido: %(value)r",
    "not allowed with argument %s": "non ammesso insieme a %s",
    "ambiguous option: %(option)s could match %(matches)s": "opzione ambigua: %(option)s può essere %(matches)s",
}


@contextlib.contextmanager
def _argparse_language(lang: str) -> Iterator[None]:
    """Translate argparse's own messages ("usage:", "options"…) while the CLI runs."""
    original = getattr(argparse, "_", None)
    if lang != "it" or not callable(original):
        yield
        return
    table = _ARGPARSE_IT

    def translate(message: str) -> str:
        return table.get(message) or original(message)

    argparse._ = translate  # type: ignore[attr-defined]
    try:
        yield
    finally:
        argparse._ = original  # type: ignore[attr-defined]


def _prescan(argv: Sequence[str]) -> tuple[str | None, bool]:
    """``--lang`` and ``--debug`` before the parser exists (help texts depend on the language)."""
    lang = None
    debug = False
    for i, arg in enumerate(argv):
        if arg == "--":
            break
        if arg == "--lang" and i + 1 < len(argv):
            lang = argv[i + 1]
        elif arg.startswith("--lang="):
            lang = arg.split("=", 1)[1]
        elif arg == "--debug":
            debug = True
    if lang not in i18n.LANGS:
        lang = None
    return lang, debug


# ==================================================================================== parser

class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30, width=min(100, max(60, shutil.get_terminal_size().columns)))


def build_parser(lang: str = "en") -> argparse.ArgumentParser:
    """The argument parser with help texts in ``lang``."""

    def T(key: str, /, **fmt: Any) -> str:  # noqa: N802 - short local translator
        return i18n.t(key, lang, **fmt).replace("%", "%%")

    parser = argparse.ArgumentParser(prog="dropwheel", description=T("cli.desc.main"), epilog=T("cli.epilog.main"),
                                     formatter_class=_HelpFormatter)
    parser.add_argument("-V", "--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--lang", choices=i18n.LANGS, help=T("cli.arg.lang"))
    parser.add_argument("--debug", action="store_true", help=T("cli.arg.debug"))
    sub = parser.add_subparsers(dest="command", metavar=T("cli.metavar.command"), title=T("cli.title.commands"))

    def add(name: str, func: Callable[[argparse.Namespace, Ctx], int], **kwargs: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=T(f"cli.help.{name}"), description=T(f"cli.help.{name}"),
                           formatter_class=_HelpFormatter, **kwargs)
        p.set_defaults(func=func)
        return p

    p = add("start", cmd_start)
    p.add_argument("--foreground", action="store_true", help=T("cli.arg.foreground"))
    p.add_argument("--no-wait", action="store_true", help=T("cli.arg.no_wait"))

    add("stop", cmd_stop)
    add("restart", cmd_restart)

    p = add("status", cmd_status)
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("open", cmd_open, epilog=T("cli.epilog.open"))
    p.add_argument("files", nargs="*", metavar=T("cli.metavar.file"), help=T("cli.arg.open_files"))
    p.add_argument("--at", metavar="X,Y", help=T("cli.arg.at"))

    p = add("run", cmd_run, epilog=T("cli.epilog.run"))
    p.add_argument("action", metavar=T("cli.metavar.action"), help=T("cli.arg.action"))
    p.add_argument("files", nargs="+", metavar=T("cli.metavar.file"), help=T("cli.arg.files"))
    p.add_argument("-p", "--param", action="append", default=[], metavar=T("cli.metavar.param"),
                   help=T("cli.arg.param"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.run_json"))
    p.add_argument("-q", "--quiet", action="store_true", help=T("cli.arg.quiet"))

    p = add("menu", cmd_menu)
    p.add_argument("files", nargs="+", metavar=T("cli.metavar.file"), help=T("cli.arg.files"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))
    p.add_argument("--drag", action="store_true", help=T("cli.arg.drag"))

    p = add("actions", cmd_actions)
    p.add_argument("-c", "--category", metavar=T("cli.metavar.category"), help=T("cli.arg.category"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("doctor", cmd_doctor)
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))
    p.add_argument("--full", action="store_true", help=T("cli.arg.full"))

    p = add("undo", cmd_undo)
    p.add_argument("job", nargs="?", metavar=T("cli.metavar.job"), help=T("cli.arg.job"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("history", cmd_history)
    p.add_argument("-n", "--limit", type=int, default=20, metavar="N", help=T("cli.arg.limit"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("config", cmd_config)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--path", action="store_true", help=T("cli.arg.config_path"))
    g.add_argument("--init", action="store_true", help=T("cli.arg.config_init"))
    g.add_argument("--edit", action="store_true", help=T("cli.arg.config_edit"))
    p.add_argument("--force", action="store_true", help=T("cli.arg.force"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("setup", cmd_setup, epilog=T("cli.epilog.setup"))
    p.add_argument("target", choices=_SETUP_TARGETS, metavar=T("cli.metavar.target"),
                   help=T("cli.arg.target", targets=", ".join(_SETUP_TARGETS)))
    p.add_argument("--dry-run", action="store_true", help=T("cli.arg.dry_run"))
    p.add_argument("--apply", action="store_true", help=T("cli.arg.apply"))

    p = add("shares", cmd_shares)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--stop", metavar="ID", help=T("cli.arg.share_stop"))
    g.add_argument("--stop-all", action="store_true", help=T("cli.arg.share_stop_all"))
    p.add_argument("--json", action="store_true", help=T("cli.arg.json"))

    p = add("daemon", cmd_daemon)
    p.add_argument("daemon_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    # "share-serve ID" (internal) is dispatched by main() before parsing, so it stays out of the help
    return parser


# ===================================================================================== main

@contextlib.contextmanager
def _logging_to_stderr(debug: bool) -> Iterator[None]:
    """Errors (everything with ``--debug``) of every module on stderr while the command runs."""
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.ERROR)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _setup_streams() -> None:
    """Never crash on file names that the console encoding cannot represent."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        enc = (getattr(stream, "encoding", "") or "").lower().replace("_", "-")
        try:
            reconfigure(errors="surrogateescape" if enc in ("utf-8", "utf8") and os.name == "posix"
                        else "backslashreplace")
        except (ValueError, OSError, TypeError):
            pass


def _detach_stdout() -> None:
    """After a broken pipe (``dropwheel history | head``) send further output to /dev/null."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass


def _print_user_error(ctx: Ctx, exc: UserError) -> None:
    term = ctx.eterm
    err(f"{term.fail()} {term.bold(exc.text(ctx.lang))}")
    if exc.details and (ctx.debug or len(exc.details) <= 600):
        for line in exc.details.strip().splitlines()[-15:]:
            err("  " + term.dim(line))
    if exc.install:
        err(f"  {ctx.t('cli.msg.install')} {term.cyan(exc.install)}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI with ``argv`` (``sys.argv[1:]`` by default) and return the exit status."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    _setup_streams()
    lang_opt, debug = _prescan(args_list)
    try:
        config = cfgmod.load()
    except Exception as exc:  # config.load never raises by contract; stay usable anyway
        log.debug("config load failed: %s", exc)
        config = cfgmod.defaults()
    lang = lang_opt or i18n.detect_lang(config)
    i18n.set_lang(lang)
    ctx = Ctx(config=config, lang=lang, term=Term(sys.stdout), eterm=Term(sys.stderr), debug=debug)
    if args_list and args_list[0] == "daemon":
        # everything after "daemon" belongs to it (e.g. --debug); it sets up its own logging
        return cmd_daemon(argparse.Namespace(daemon_args=args_list[1:]), ctx)
    if args_list and args_list[0] == "share-serve":
        if len(args_list) != 2:
            err("usage: dropwheel share-serve SHARE_ID")
            return EXIT_USAGE
        return cmd_share_serve(argparse.Namespace(share_id=args_list[1]), ctx)
    with _logging_to_stderr(debug), _argparse_language(lang):
        return _dispatch(build_parser(lang), args_list, ctx)


def _dispatch(parser: argparse.ArgumentParser, args_list: list[str], ctx: Ctx) -> int:
    try:
        args = parser.parse_args(args_list)
    except SystemExit as exc:  # --help, --version or a usage error
        code = exc.code
        if code is None:
            return EXIT_OK
        return code if isinstance(code, int) else EXIT_USAGE
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    ctx.debug = bool(args.debug)
    try:
        return int(args.func(args, ctx) or 0)
    except UsageError as exc:
        err(f"dropwheel {args.command}: {ctx.t('cli.msg.usage_error')}: {exc}")
        return EXIT_USAGE
    except UserError as exc:
        _print_user_error(ctx, exc)
        return EXIT_FAIL
    except KeyboardInterrupt:
        err()
        return EXIT_CANCELLED
    except BrokenPipeError:
        _detach_stdout()
        return EXIT_FAIL
    except Exception as exc:  # a bug: show it, never a bare traceback without context
        err(f"{ctx.eterm.fail()} {ctx.t('err.unexpected')}: {exc}")
        if ctx.debug:
            import traceback

            err(traceback.format_exc())
        else:
            err("  " + ctx.t("cli.msg.debug_hint"))
        return EXIT_FAIL


# ============================================================================= input helpers

def resolve_input(arg: str) -> str:
    """Absolute path for a command line file argument; ``file://`` URIs become paths, http(s)
    URLs are kept, other URIs (sftp://, smb://…) map to their gvfs mount when there is one."""
    text = str(arg)
    if text.lower().startswith("file:"):
        return util.uri_to_path(text)
    if _URL_RE.match(text):
        scheme = text.split(":", 1)[0].lower()
        if scheme in ("http", "https"):
            return text
        mapped = util.gvfs_path(text)
        return mapped or text
    return os.path.abspath(os.path.expanduser(text))


def resolve_inputs(items: Iterable[str], *, must_exist: bool = True) -> list[str]:
    """Resolve every argument (see :func:`resolve_input`); duplicates are dropped.

    Raises :class:`UserError` listing the local files that do not exist.
    """
    files: list[str] = []
    missing: list[str] = []
    for item in items:
        if item is None or str(item) == "":
            continue
        path = resolve_input(str(item))
        if must_exist and not _URL_RE.match(path) and not os.path.lexists(path):
            missing.append(str(item))
            continue
        if path not in files:
            files.append(path)
    if missing:
        raise UserError("cli.err.not_found", names=", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
    return files


_NUMBER_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_param_value(raw: str) -> Any:
    """Value of ``-p key=value``: JSON objects/arrays/strings/numbers, ``true``/``false``/``null``
    (also ``yes``/``no``/``on``/``off``/``sì``), otherwise the text unchanged."""
    text = raw.strip()
    low = text.lower()
    if low in ("true", "yes", "on", "si", "sì"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    if text[:1] in ("{", "[", '"') or _NUMBER_RE.fullmatch(text):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return raw


def _parse_params(ctx: Ctx, items: Iterable[str]) -> dict:
    params: dict[str, Any] = {}
    for item in items or []:
        key, sep, raw = str(item).partition("=")
        key = key.strip()
        if not sep or not key:
            raise UsageError(ctx.t("cli.run.bad_param", item=item))
        params[key] = parse_param_value(raw)
    return params


def _load_actions(ctx: Ctx) -> dict[str, str]:
    """Import the action modules (and ``$DROPWHEEL_EXTRA_ACTIONS``), like the daemon does."""
    from . import daemon

    return daemon.load_actions(ctx.config)


# ============================================================================ shell control

def cmd_start(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel start``: launch the Quickshell shell (and with it the daemon)."""
    from . import shellctl

    term = ctx.term
    if args.foreground:
        return shellctl.run_foreground()
    info = shellctl.start(wait=0.0 if args.no_wait else 5.0)
    if info.get("already"):
        out(f"{term.ok()} {ctx.t('cli.start.already')}")
    else:
        out(f"{term.ok()} {ctx.t('cli.start.started', shell=info.get('shell', ''))}")
    if info.get("responding") is False:
        err(f"{ctx.eterm.warn()} {ctx.t('cli.start.not_responding', log=info.get('log', ''))}")
    return EXIT_OK


def cmd_stop(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel stop``: stop every instance of the shell."""
    from . import shellctl

    if shellctl.stop():
        out(f"{ctx.term.ok()} {ctx.t('cli.stop.stopped')}")
    else:
        out(ctx.t("cli.stop.not_running"))
    return EXIT_OK


def cmd_restart(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel restart``."""
    from . import shellctl

    info = shellctl.restart(wait=5.0)
    out(f"{ctx.term.ok()} {ctx.t('cli.start.started', shell=info.get('shell', ''))}")
    if info.get("responding") is False:
        err(f"{ctx.eterm.warn()} {ctx.t('cli.start.not_responding', log=info.get('log', ''))}")
    return EXIT_OK


def _instance_pid(inst: dict) -> str:
    for key in ("pid", "processId", "process_id"):
        if inst.get(key):
            return str(inst[key])
    return "?"


def cmd_status(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel status``: exit status 0 when the shell runs, 1 otherwise."""
    from . import shellctl

    info = shellctl.status(check_ping=True)
    running = bool(info.get("running"))
    if args.json:
        out(_dumps(info))
        return EXIT_OK if running else EXIT_FAIL
    term = ctx.term
    yes, no = term.ok(), term.fail()
    rows = [
        (ctx.t("cli.status.data"), f"{info['data_dir']}"),
        (ctx.t("cli.status.shell"), f"{info['shell_dir']}  {yes if info['shell_ok'] else no}"),
        (ctx.t("cli.status.qs"), f"{info['qs'] or no}  {term.dim(info.get('qs_version') or '')}".rstrip()),
        (ctx.t("cli.status.backend"), shlex.join(info.get("backend") or [])),
        (ctx.t("cli.status.hyprland"), ctx.t("cli.yes") if info.get("hyprland") else ctx.t("cli.no")),
    ]
    if running:
        pids = ", ".join(_instance_pid(i) for i in info.get("instances") or [])
        state = ctx.t("cli.status.running", pids=pids)
        if info.get("responding") is True:
            state += f" {term.dim('·')} {ctx.t('cli.status.responding')}"
        elif info.get("responding") is False:
            state += f" {term.dim('·')} {term.yellow(ctx.t('cli.status.not_responding'))}"
        rows.append((ctx.t("cli.status.state"), f"{yes} {state}"))
    else:
        rows.append((ctx.t("cli.status.state"), f"{no} {ctx.t('cli.status.stopped')}"))
    rows.append((ctx.t("cli.status.log"), info.get("log") or ""))
    out(term.bold(f"{APP_NAME} {__version__}"))
    width = max(text_width(r[0]) for r in rows) + 2
    for label, value in rows:
        out(f"  {pad(label + ':', width)}{value}")
    if info.get("error"):
        err(f"{ctx.eterm.warn()} {info['error']}")
    return EXIT_OK if running else EXIT_FAIL


def _parse_at(ctx: Ctx, value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*(-?\d+)\s*[,x ]\s*(-?\d+)\s*", value)
    if not m:
        raise UsageError(ctx.t("cli.open.bad_at", value=value))
    return int(m.group(1)), int(m.group(2))


def cmd_open(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel open [FILES]``: show the wheel in click mode (starting the shell if needed)."""
    from . import shellctl

    files = resolve_inputs(args.files)
    at = _parse_at(ctx, args.at)
    if not shellctl.running():
        info = shellctl.start(wait=5.0)
        if not info.get("responding"):
            raise UserError("cli.open.not_ready", log=info.get("log", shellctl.log_command()))
    elif not shellctl.wait_ready(5.0):
        raise UserError("cli.open.not_ready", log=shellctl.log_command())
    if files:
        payload: dict[str, Any] = {"files": files}
        if at is not None:
            payload["x"], payload["y"] = at
        shellctl.ipc_call("open", json.dumps(payload, ensure_ascii=False), timeout=10.0)
    else:
        shellctl.ipc_call("openAtCursor", timeout=10.0)
    return EXIT_OK


# ====================================================================================== run

class Progress:
    """A one-line progress display on stderr (TTY only) plus job warnings."""

    def __init__(self, term: Term, label: str, *, enabled: bool, warnings: bool = True) -> None:
        self.term = term
        self.label = label
        self.enabled = enabled and term.tty
        self.show_warnings = warnings
        self.frames = _SPINNER_U if term.unicode else _SPINNER_A
        self.frame = 0
        self.drawn = False
        self.last = 0.0
        self.seen = 0
        self.started = time.monotonic()

    def update(self, job: Any) -> None:
        """Redraw from the job state (at most every 80 ms)."""
        warnings = list(getattr(job, "warnings", []) or [])
        if self.show_warnings and len(warnings) > self.seen:
            for w in warnings[self.seen:]:
                self.clear()
                err(f"{self.term.warn()} {w}")
        self.seen = len(warnings)
        if not self.enabled:
            return
        now = time.monotonic()
        if self.drawn and now - self.last < 0.08:
            return
        self.last = now
        frac = getattr(job, "progress", None)
        parts = []
        if frac is None:
            parts.append(self.term.cyan(self.frames[self.frame % len(self.frames)]))
            self.frame += 1
        else:
            frac = max(0.0, min(1.0, float(frac)))
            n = 24
            filled = int(round(frac * n))
            parts.append(self.term.cyan(self.term.sym("full") * filled) + self.term.dim(self.term.sym("empty") * (n - filled)))
            parts.append(f"{int(frac * 100):3d}%")
        parts.append(self.term.bold(self.label))
        message = " ".join(str(getattr(job, "message", "") or "").split())
        if message:
            parts.append(self.term.dim(message))
        elapsed = now - self.started
        if elapsed >= 2:
            parts.append(self.term.dim(util.human_duration(elapsed)))
        line = " ".join(parts)
        limit = self.term.width() - 1
        if text_width(line) > limit:
            line = truncate(_ANSI_RE.sub("", line), limit, self.term.sym("ellipsis"))
        stream = self.term.stream
        if stream is None:
            return
        stream.write("\r\033[K" + line)
        stream.flush()
        self.drawn = True

    def clear(self) -> None:
        """Remove the progress line."""
        if self.drawn and self.term.stream is not None:
            self.term.stream.write("\r\033[K")
            self.term.stream.flush()
            self.drawn = False


@contextlib.contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """SIGTERM/SIGHUP cancel the running job like Ctrl-C (main thread only)."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(sig, old)


def _resolve_action(ctx: Ctx, ident: str, files: list[str]) -> str:
    from . import registry

    if registry.get(ident) is not None:
        return ident
    target = registry.resolve_target(ident, files, ctx.config, ctx.lang)
    if target is not None:
        return target
    ids = [a.id for a in registry.all_actions()]
    close = difflib.get_close_matches(ident, ids, n=3, cutoff=0.6)
    hint = ctx.t("cli.run.did_you_mean", ids=", ".join(close)) if close else ctx.t("cli.run.see_actions")
    raise UsageError(f"{ctx.t('err.unknown_action', id=ident)}. {hint}")


def _print_result(ctx: Ctx, res: dict) -> None:
    term = ctx.term
    kind = res.get("kind") or "files"
    if res.get("message"):
        out(str(res["message"]))
    for o in res.get("outputs") or []:
        size = o.get("size_h") or ""
        out(f"{term.ok()} {o.get('path')}" + (term.dim(f"  ({size})") if size else ""))
    if res.get("size_before_h") and res.get("size_after_h"):
        delta = res.get("delta")
        change = f" ({delta:+d} %)" if isinstance(delta, int) else ""
        out(term.dim(ctx.t("cli.run.size_change", before=res["size_before_h"], after=res["size_after_h"]) + change))
    if kind == "text" and res.get("text"):
        out(str(res["text"]))
    if kind == "info" and res.get("info"):
        rows = []
        last_group = None
        for row in res["info"]:
            group = str(row.get("group") or "")
            rows.append((group if group != last_group else "", str(row.get("key") or ""), str(row.get("value") or "")))
            last_group = group
        for line in _table(term, (ctx.t("cli.col.group"), ctx.t("cli.col.key"), ctx.t("cli.col.value")), rows):
            out(line)
    if kind == "qr":
        if res.get("qr") and not res.get("outputs"):
            out(f"{term.ok()} {res['qr']}")
        if res.get("text"):
            out(str(res["text"]))
    if kind == "share":
        url = str(res.get("url") or res.get("text") or "")
        if url:
            out(ctx.t("cli.run.share_url", url=term.cyan(url)))
            if term.tty:
                from . import share

                art = share.qr_text(url)
                if art:
                    out(art.rstrip("\n"))
        if res.get("expires"):
            with contextlib.suppress(TypeError, ValueError, OSError, OverflowError):
                until = datetime.fromtimestamp(float(res["expires"])).strftime("%H:%M")
                out(term.dim(ctx.t("cli.run.share_until", time=until)))
        if res.get("share_id"):
            out(term.dim(ctx.t("cli.run.share_stop", cmd=f"dropwheel shares --stop {res['share_id']}")))


def cmd_run(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel run ACTION FILES``: run an action without the shell."""
    from .jobs import FINAL_STATES, JobRunner

    files = resolve_inputs(args.files)
    params = _parse_params(ctx, args.param)
    _load_actions(ctx)
    target = _resolve_action(ctx, args.action, files)
    final: dict = {}

    def emit(event: dict) -> None:
        if event.get("state") in FINAL_STATES:
            final.clear()
            final.update(event)

    # notifications only when nobody watches a terminal (scripts, key bindings)
    notify_fn: Any = False if (ctx.term.tty or ctx.eterm.tty) else None
    runner = JobRunner(ctx.config, ctx.lang, emit, notify_fn=notify_fn)
    interrupted = False
    try:
        job_id = runner.submit(target, files, params)
        job = runner.get(job_id)
        progress = Progress(ctx.eterm, job.label if job else target, enabled=not (args.json or args.quiet),
                            warnings=not args.json)
        with _sigterm_as_interrupt():
            try:
                while job is not None and not job.done:
                    runner.wait(job_id, timeout=0.1)
                    progress.update(job)
            except KeyboardInterrupt:
                interrupted = True
                progress.clear()
                err(f"{ctx.eterm.warn()} {ctx.t('cli.run.cancelling')}")
                runner.cancel(job_id)
                try:
                    deadline = time.monotonic() + 30
                    while job is not None and not job.done and time.monotonic() < deadline:
                        runner.wait(job_id, timeout=0.2)
                except KeyboardInterrupt:
                    # second Ctrl-C: do not wait for the worker thread at interpreter exit
                    progress.clear()
                    for stream in (sys.stdout, sys.stderr):
                        with contextlib.suppress(Exception):
                            stream.flush()
                    os._exit(EXIT_CANCELLED)
            finally:
                if job is not None:
                    progress.update(job)
                progress.clear()
    finally:
        runner.shutdown(wait=False)
    if job is None:
        return EXIT_FAIL
    event = dict(final) if final else dict(job.to_json(), event="job")
    if args.json:
        out(_dumps(event))
    state = job.state
    if state == "done":
        if not args.json:
            _print_result(ctx, job.result or {})
        return EXIT_OK
    if state == "failed":
        if not args.json:
            error = job.error or {}
            term = ctx.eterm
            err(f"{term.fail()} {term.bold(str(error.get('message') or ctx.t('job.failed')))}")
            details = str(error.get("details") or "").strip()
            if details and (ctx.debug or len(details) <= 1500):
                for line in details.splitlines()[-15:]:
                    err("  " + term.dim(line))
            if error.get("install"):
                err(f"  {ctx.t('cli.msg.install')} {term.cyan(str(error['install']))}")
        return EXIT_FAIL
    if not args.json:
        err(ctx.t("cli.run.cancelled"))
        if job.result and job.result.get("outputs"):
            _print_result(ctx, job.result)
    return EXIT_CANCELLED if (interrupted or state == "cancelled") else EXIT_FAIL


# ===================================================================================== menu

def _node_label(node: dict) -> str:
    return str(node.get("text") or node.get("label") or node.get("id") or "")


def _display_label(term: Term, node: dict) -> str:
    label = _node_label(node)
    if node.get("type") != "group" and node.get("mode") == "dialog" and not label.endswith(("…", "...")):
        label += term.sym("ellipsis")
    if node.get("type") != "group" and node.get("default"):
        label = f"{term.sym('star')} {label}"
    return label


def _print_nodes(ctx: Ctx, nodes: list[dict], prefix: str) -> None:
    term = ctx.term
    labels = [_display_label(term, n) for n in nodes]
    width = max((text_width(lbl) for lbl in labels), default=0)
    for i, node in enumerate(nodes):
        last = i == len(nodes) - 1
        branch = term.sym("last") if last else term.sym("branch")
        label = labels[i]
        if node.get("type") == "group":
            line = f"{prefix}{branch}{term.bold(pad(label, width))}  {term.dim(str(node.get('id', '')))}"
            if node.get("default"):
                line += f"  {term.yellow(term.sym('star'))} {term.dim(str(node['default']))}"
            out(line)
            _print_nodes(ctx, list(node.get("items") or []), prefix + (term.sym("space") if last else term.sym("pipe")))
            continue
        available = node.get("available", True)
        shown = pad(label, width)
        if not available:
            shown = term.dim(shown)
        elif node.get("danger"):
            shown = term.red(shown)
        elif node.get("default"):
            shown = term.yellow(shown)
        line = f"{prefix}{branch}{shown}  {term.dim(str(node.get('id', '')))}"
        tags = []
        if node.get("mode") == "quick":
            tags.append(ctx.t("cli.menu.quick"))
        if node.get("mode") == "dialog":
            tags.append(ctx.t("cli.menu.dialog", dialog=node.get("dialog") or "form"))
        if tags:
            line += "  " + term.dim("[" + ", ".join(tags) + "]")
        if not available:
            hint = node.get("install") or node.get("hint") or ""
            line += f"  {term.fail()} " + term.dim(ctx.t("cli.menu.unavailable") + (f": {hint}" if hint else ""))
        out(line)


def cmd_menu(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel menu FILES``: the wheel menu as a tree (or JSON)."""
    from . import registry

    files = resolve_inputs(args.files)
    _load_actions(ctx)
    thumb_fn = None
    if args.json:
        from . import preview

        thumb_fn = preview.thumb_path
    menu = registry.build_menu(files, ctx.config, ctx.lang, "drag" if args.drag else "click", thumb_fn=thumb_fn)
    if args.json:
        out(_dumps(menu))
        return EXIT_OK
    term = ctx.term
    head = term.bold(str(menu.get("title") or ""))
    if menu.get("subtitle"):
        head += f" {term.dim(term.sym('dash'))} {menu['subtitle']}"
    category = str(menu.get("category") or "")
    if category:
        head += "  " + term.dim(f"[{ctx.t('cat.' + category)}]")
    out(head)
    for page in menu.get("pages") or []:
        out()
        out(term.bold(str(page.get("label") or page.get("id") or "")))
        items = list(page.get("items") or [])
        if not items:
            out("  " + term.dim(ctx.t("cli.menu.empty_page")))
            continue
        _print_nodes(ctx, items, "  ")
    return EXIT_OK


# ================================================================================== actions

def cmd_actions(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel actions [--category C]``: every registered action with its availability."""
    from . import registry
    from .detect import CATEGORIES

    _load_actions(ctx)
    category = (args.category or "").strip().lower() or None
    if category is not None and category not in CATEGORIES:
        raise UsageError(ctx.t("cli.actions.bad_category", category=category, categories=", ".join(CATEGORIES)))
    hidden = set(cfgmod.get(ctx.config, "hidden_actions", []) or [])
    acts = sorted(registry.all_actions(), key=lambda a: a.id)
    if category is not None:
        acts = [a for a in acts if a.matches_category(category)]
    rows_json = []
    for act in acts:
        missing = act.missing()
        rows_json.append({
            "id": act.id, "label": registry.display_label(act.id, ctx.lang), "icon": act.icon,
            "categories": sorted(act.categories), "multi": act.multi, "mode": act.mode, "dialog": act.dialog,
            "available": not missing, "missing": missing, "install": tools.install_hint(missing) if missing else "",
            "hidden": act.id in hidden, "danger": act.danger,
            "params": [p.name for p in act.all_params()],
        })
    if args.json:
        out(_dumps(rows_json))
        return EXIT_OK
    term = ctx.term
    if not rows_json:
        out(ctx.t("cli.actions.none"))
        return EXIT_OK
    rows = []
    for r in rows_json:
        cats = ", ".join(r["categories"]) if r["categories"] else "*"
        if r["available"]:
            status = term.ok()
        else:
            status = f"{term.fail()} " + term.dim(", ".join(tools.display_name(m) for m in r["missing"]))
        if r["hidden"]:
            status += " " + term.dim(ctx.t("cli.actions.hidden"))
        mode = r["mode"] if r["mode"] != "dialog" else f"dialog:{r['dialog']}"
        rows.append((r["id"], r["label"], cats, f"{r['multi']}/{mode}", status))
    headers = (ctx.t("cli.col.id"), ctx.t("cli.col.label"), ctx.t("cli.col.categories"), ctx.t("cli.col.mode"),
               ctx.t("cli.col.available"))
    for line in _table(term, headers, rows, flex=2):
        out(line)
    unavailable = sum(1 for r in rows_json if not r["available"])
    out()
    out(term.dim(ctx.t("cli.actions.summary", total=len(rows_json), unavailable=unavailable)))
    return EXIT_OK


# =================================================================================== doctor

def _lua_code_lines(text: str) -> Iterator[str]:
    """Lines of Lua source without comments (``--`` and ``--[[ … ]]``)."""
    in_block = False
    for line in text.splitlines():
        if in_block:
            if "]]" not in line:
                continue
            in_block = False
            line = line.split("]]", 1)[1]
        stripped = line.lstrip()
        if stripped.startswith("--[["):
            if "]]" not in stripped[4:]:
                in_block = True
            continue
        yield line.split("--", 1)[0]


def _lua_mentions(path: Path, pattern: re.Pattern) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(pattern.search(code) for code in _lua_code_lines(text))


def lua_requires_dropwheel(path: Path) -> bool:
    """``True`` when the Lua file loads our module (``require("dropwheel")`` outside comments)."""
    return _lua_mentions(path, _REQUIRE_RE)


def caelestia_config_active(cfg_home: Path) -> bool:
    """``True`` when the Hyprland configuration really loads ``~/.config/caelestia/hypr-user.lua``.

    Only the Hyprland configuration of the Caelestia dots does, and ``~/.config/caelestia`` also
    exists for whoever runs caelestia-shell with a Hyprland configuration of their own."""
    return _lua_mentions(cfg_home / "hypr" / "hyprland.lua", _HYPR_USER_RE)


def hyprland_setup_state() -> dict:
    """Where ``dropwheel setup hyprland`` put the module and whether the loader requires it."""
    cfg_home = util.xdg_config_home()
    main_cfg = cfg_home / "hypr" / "hyprland.lua"
    plain = (cfg_home / "hypr" / "dropwheel.lua", main_cfg)
    if caelestia_config_active(cfg_home):
        candidates = ((cfg_home / "caelestia" / "dropwheel.lua", cfg_home / "caelestia" / "hypr-user.lua"), plain)
    else:  # nothing reads hypr-user.lua, whatever it contains
        candidates = (plain, (cfg_home / "caelestia" / "dropwheel.lua", main_cfg))
    for module, loader in candidates:
        if module.is_file():
            return {"configured": lua_requires_dropwheel(loader), "module": str(module), "loader": str(loader)}
    return {"configured": False, "module": None, "loader": None}


def _alternative_satisfied(name: str) -> bool:
    for group in ALTERNATIVES:
        if name in group and any(other != name and tools.have(other) for other in group):
            return True
    return False


def install_all_command(report: dict) -> str:
    """One command installing every missing tool, except personal-choice programs and tools whose
    alternative is already installed."""
    names = [row["name"] for row in report.get("tools") or []
             if not row.get("available") and row["name"] not in CHOICE_TOOLS and not _alternative_satisfied(row["name"])]
    return tools.install_hint(names)


def _required_status() -> list[dict]:
    rows = []
    for name, pkg in REQUIRED_TOOLS:
        rows.append({"name": name, "package": pkg, "path": tools.path(name)})
    return rows


def cmd_doctor(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel doctor``: session, required dependencies, optional tools and the commands
    that install what is missing.  Exit status 1 when a required dependency is missing."""
    from . import shellctl

    load_errors = _load_actions(ctx)
    report = tools.report()
    required = _required_status()
    required_missing = sorted({r["package"] for r in required if not r["path"]})
    install_all = install_all_command(report)
    shell = shellctl.status(check_ping=True)
    hypr = hyprland_setup_state()
    cfg_path = cfgmod.config_path()
    warnings = [str(w) for w in ctx.config.get("_warnings") or []]
    try:
        from . import actions as actions_pkg

        loaded = actions_pkg.loaded_modules()
    except Exception:  # pragma: no cover - broken installation
        loaded = []
    code = EXIT_FAIL if required_missing else EXIT_OK
    if args.json:
        data = dict(report)
        data.update({
            "version": __version__, "required": required, "required_missing": required_missing,
            "install_required": tools.install_hint([r["name"] for r in required if not r["path"]]),
            "install_all": install_all, "shell": shell, "hyprland_setup": hypr, "load_errors": load_errors,
            "loaded_modules": loaded, "config_path": str(cfg_path), "config_exists": cfg_path.is_file(),
            "config_warnings": warnings, "lang": ctx.lang,
            "hyprland_session": bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")),
        })
        out(_dumps(data))
        return code
    term = ctx.term
    yes, no = term.ok(), term.fail()
    out(term.bold(f"{APP_NAME} {__version__}") + term.dim(f"  {term.sym('dot')}  Python {report.get('python')} "
                                                           f"({report.get('executable')})"))
    cfg_state = ctx.t("cli.doctor.config_exists") if cfg_path.is_file() else ctx.t("cli.doctor.config_default")
    out(f"{ctx.t('cli.doctor.config')}: {cfg_path} {term.dim('(' + cfg_state + ')')}")
    for w in warnings:
        out(f"  {term.warn()} {w}")

    # --- session
    out()
    out(term.bold(ctx.t("cli.doctor.session")))
    rows: list[tuple[str, str, str]] = []
    in_hypr = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
    rows.append((yes if in_hypr else no, "Hyprland",
                 ctx.t("cli.doctor.hypr_session") if in_hypr else ctx.t("cli.doctor.hypr_no_session")))
    qs_text = shell.get("qs") or ctx.t("cli.doctor.missing")
    if shell.get("qs_version"):
        qs_text += term.dim(f"  {shell['qs_version']}")
    rows.append((yes if shell.get("qs") else no, "Quickshell", qs_text))
    if shell.get("running"):
        pids = ", ".join(_instance_pid(i) for i in shell.get("instances") or [])
        state = ctx.t("cli.status.running", pids=pids)
        if shell.get("responding") is False:
            state += " " + term.yellow(ctx.t("cli.status.not_responding"))
    else:
        state = ctx.t("cli.status.stopped") + term.dim(f"  {term.sym('arrow')} dropwheel start")
    rows.append((yes if shell.get("shell_ok") else no, ctx.t("cli.status.shell"),
                 f"{shell.get('shell_dir')}  {term.dim(term.sym('dot'))} {state}"))
    if hypr.get("configured"):
        rows.append((yes, ctx.t("cli.doctor.hypr_setup"), str(hypr.get("module"))))
    elif hypr.get("module"):
        rows.append((term.warn(), ctx.t("cli.doctor.hypr_setup"),
                     ctx.t("cli.doctor.hypr_not_loaded", loader=hypr.get("loader")) +
                     term.dim(f"  {term.sym('arrow')} dropwheel setup hyprland")))
    else:
        rows.append((no, ctx.t("cli.doctor.hypr_setup"),
                     ctx.t("cli.doctor.hypr_not_setup") + term.dim(f"  {term.sym('arrow')} dropwheel setup hyprland")))
    width = max(text_width(r[1]) for r in rows) + 2
    for mark, label, value in rows:
        out(f"  {mark} {pad(label, width)}{value}")

    # --- required
    out()
    out(term.bold(ctx.t("cli.doctor.required")))
    seen: set[str] = set()
    items = []
    for r in required:
        if r["package"] in seen:
            continue
        seen.add(r["package"])
        ok = all(x["path"] for x in required if x["package"] == r["package"])
        items.append(f"{yes if ok else no} {r['package']}")
    for line in _columns(term, items, indent=2):
        out(line)

    # --- action modules
    out()
    out(term.bold(ctx.t("cli.doctor.modules")) + term.dim(f"  ({ctx.t('cli.doctor.loaded', n=len(loaded))})"))
    if load_errors:
        for module, error in sorted(load_errors.items()):
            last = [ln for ln in str(error).strip().splitlines() if ln.strip()]
            out(f"  {no} {module}: {last[-1].strip() if last else ''}")
            if ctx.debug:
                for line in str(error).strip().splitlines():
                    out("      " + term.dim(line))
    else:
        out(f"  {yes} {ctx.t('cli.doctor.modules_ok')}")

    # --- optional tools
    out()
    out(term.bold(ctx.t("cli.doctor.tools")) + term.dim(
        f"  ({ctx.t('cli.doctor.tools_count', available=report.get('available', 0), total=len(report.get('tools') or []))})"))
    groups: dict[str, list[dict]] = {}
    for row in report.get("tools") or []:
        groups.setdefault(str(row.get("group") or "system"), []).append(row)
    order = [g for g in _GROUP_ORDER if g in groups] + sorted(g for g in groups if g not in _GROUP_ORDER)
    for group in order:
        rows_g = groups[group]
        found = sum(1 for r in rows_g if r.get("available"))
        out(f"  {term.bold(ctx.t('tools.group.' + group))} {term.dim(f'{found}/{len(rows_g)}')}")
        if args.full:
            table_rows = []
            for r in rows_g:
                if r.get("available"):
                    table_rows.append((yes, r["name"], str(r.get("path") or ""), term.dim(r.get("description") or "")))
                else:
                    pkg = ", ".join(r.get("packages") or [])
                    repo = r.get("repo") or ""
                    table_rows.append((no, r["name"], f"{pkg} ({repo})" if repo else pkg,
                                       term.dim(r.get("description") or "")))
            for line in _table(term, ("", ctx.t("cli.col.tool"), ctx.t("cli.col.where"), ""), table_rows, flex=2)[1:]:
                out("    " + line)
        else:
            cells = [f"{yes if r.get('available') else no} {r['name']}" for r in rows_g]
            for line in _columns(term, cells, indent=4):
                out(line)

    # --- what to do
    out()
    if required_missing:
        out(f"{no} {term.bold(ctx.t('cli.doctor.required_missing', packages=', '.join(required_missing)))}")
        out("  " + term.cyan(tools.install_hint([r["name"] for r in required if not r["path"]])))
    if install_all:
        out(ctx.t("cli.doctor.install_all"))
        out("  " + term.cyan(install_all))
    elif not required_missing:
        out(f"{yes} {ctx.t('cli.doctor.all_installed')}")
    return code


# ============================================================================= undo / history

def _runner(ctx: Ctx) -> Any:
    from .jobs import JobRunner

    return JobRunner(ctx.config, ctx.lang, None, notify_fn=False)


def cmd_undo(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel undo [JOB]``: trash the outputs of a job (the last undoable one by default)
    and restore replaced originals."""
    runner = _runner(ctx)
    try:
        res = runner.undo(args.job or None)
    finally:
        runner.shutdown(wait=False)
    ok = bool(res.get("ok"))  # without a job id the runner answers "undo.nothing" when nothing is undoable
    if args.json:
        out(_dumps(res))
        return EXIT_OK if ok else EXIT_FAIL
    if ok:
        out(f"{ctx.term.ok()} {res.get('message') or ''}")
    else:
        err(f"{ctx.eterm.fail()} {res.get('message') or ctx.t('undo.nothing')}")
    for problem in res.get("problems") or []:
        err(f"  {ctx.eterm.warn()} {problem}")
    return EXIT_OK if ok else EXIT_FAIL


def _short_list(items: Sequence[Any], term: Term) -> str:
    names = [os.path.basename(str(x.get("path") if isinstance(x, dict) else x).rstrip("/\\")) or str(x)
             for x in items]
    if not names:
        return ""
    text = names[0]
    if len(names) > 1:
        text += f" (+{len(names) - 1})"
    return text


def _history_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


def cmd_history(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel history [-n N]``: recent jobs, newest first."""
    if args.limit is not None and args.limit < 1:
        raise UsageError(ctx.t("cli.history.bad_limit"))
    runner = _runner(ctx)
    try:
        entries = runner.history(limit=args.limit or 20)
    finally:
        runner.shutdown(wait=False)
    if args.json:
        out(_dumps(entries))
        return EXIT_OK
    term = ctx.term
    if not entries:
        out(ctx.t("cli.history.empty"))
        return EXIT_OK
    rows = []
    for e in entries:
        state = str(e.get("state") or "")
        mark = term.ok() if state == "done" else term.warn() if state == "cancelled" else term.fail()
        flow = _short_list(e.get("files") or [], term)
        outs = _short_list(e.get("outputs") or [], term)
        if outs:
            flow += f" {term.sym('arrow')} {outs}"
        note = ""
        if e.get("undone"):
            note = term.dim(ctx.t("cli.history.undone"))
        elif e.get("undoable"):
            note = term.dim(ctx.t("cli.history.undoable"))
        rows.append((_history_time(e.get("time")), str(e.get("job") or ""), f"{mark} {e.get('label') or e.get('action')}",
                     flow, note))
    headers = (ctx.t("cli.col.time"), ctx.t("cli.col.job"), ctx.t("cli.col.action"), ctx.t("cli.col.files"), "")
    for line in _table(term, headers, rows, flex=3):
        out(line)
    return EXIT_OK


# =================================================================================== config

def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def _toml_lines(cfg: dict) -> list[str]:
    """The effective configuration in TOML syntax (private ``_`` keys omitted)."""
    lines = []
    tables = []
    arrays = []
    for key, value in cfg.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            arrays.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for key, table in tables:
        lines.append("")
        lines.append(f"[{key}]")
        for k, v in table.items():
            if not str(k).startswith("_"):
                lines.append(f"{k} = {_toml_value(v)}")
    for key, items in arrays:
        for item in items:
            lines.append("")
            lines.append(f"[[{key}]]")
            for k, v in item.items():
                lines.append(f"{k} = {_toml_value(v)}")
    return lines


def _edit_file(ctx: Ctx, path: Path) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    if editor and _isatty(sys.stdin) and ctx.term.tty:
        try:
            argv = shlex.split(editor)
        except ValueError:
            argv = [editor]
        try:
            subprocess.call([*argv, str(path)])
            return
        except OSError as exc:
            raise UserError("err.open_failed", details=str(exc), name=editor) from None
    if not util.open_path(path):
        raise UserError("err.open_failed", name=str(path),
                        install="" if tools.have("xdg-open") else tools.install_hint(["xdg-open"]))


def cmd_config(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel config [--path|--init|--edit]``."""
    path = cfgmod.config_path()
    term = ctx.term
    if args.path:
        out(str(path))
        return EXIT_OK
    if args.init:
        try:
            written = cfgmod.write_default(path, force=args.force)
        except FileExistsError:
            raise UserError("cli.config.exists", path=str(path)) from None
        out(f"{term.ok()} {ctx.t('cli.config.written', path=str(written))}")
        return EXIT_OK
    if args.edit:
        if not path.exists():
            cfgmod.write_default(path)
            out(f"{term.ok()} {ctx.t('cli.config.written', path=str(path))}")
        _edit_file(ctx, path)
        new = cfgmod.load(path)
        for w in new.get("_warnings") or []:
            err(f"{ctx.eterm.warn()} {w}")
        return EXIT_OK
    cfg = cfgmod.load(path)
    warnings = [str(w) for w in cfg.get("_warnings") or []]
    if args.json:
        out(_dumps({"path": str(path), "exists": path.is_file(), "config": cfgmod.public_config(cfg),
                    "warnings": warnings}))
        return EXIT_OK
    state = ctx.t("cli.doctor.config_exists") if path.is_file() else ctx.t("cli.config.not_found_defaults")
    out(term.bold(str(path)) + "  " + term.dim(f"({state})"))
    out(term.dim(f"# {ctx.t('cli.config.language', lang=i18n.detect_lang(cfg))}"))
    for line in _toml_lines(cfg):
        out(line)
    for w in warnings:
        err(f"{ctx.eterm.warn()} {w}")
    if not path.is_file():
        out()
        out(term.dim(ctx.t("cli.config.init_hint")))
    return EXIT_OK


# ==================================================================================== setup

def launcher_path() -> str | None:
    """Absolute path that launchers must use for the ``dropwheel`` command, or ``None`` when the
    bare name is right (the package's ``/usr/bin/dropwheel``, or nothing found to point at).

    A user-local install lives in ``~/.local/bin``, which the graphical session's ``PATH`` often
    lacks: desktop entries and file manager actions naming plain ``dropwheel`` would do nothing."""
    found = shutil.which("dropwheel")
    if found:
        found = os.path.abspath(found)
        return None if os.path.realpath(found) == "/usr/bin/dropwheel" else found
    wrapper = Path.home() / ".local" / "bin" / "dropwheel"
    try:
        return str(wrapper) if "DROPWHEEL-USER-WRAPPER" in wrapper.read_text(encoding="utf-8", errors="replace") else None
    except OSError:
        return None


def _shell_quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def _desktop_exec_arg(path: str) -> str:
    """One argument of the Exec key of a desktop entry (Desktop Entry Spec, "Exec key")."""
    if re.fullmatch(r"[A-Za-z0-9._/+@-]+", path):
        return path
    quoted = path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return '"' + quoted.replace("\\", "\\\\") + '"'  # string-level escaping applies on top of the Exec quoting


def rewrite_launcher(text: str, launcher: str, style: str) -> str:
    """Point a desktop entry, service menu or Nemo action at ``launcher`` (``style``: how its
    ``Exec`` is parsed, ``"desktop"`` or ``"shell"``); same output as ``install_launcher`` in install.sh."""
    command = _desktop_exec_arg(launcher) if style == "desktop" else _shell_quote(launcher)
    plain = launcher.replace("\\", "\\\\")
    lines = []
    for line in text.splitlines():
        if line == "Exec=dropwheel" or line.startswith("Exec=dropwheel "):
            line = f"Exec={command}{line[len('Exec=dropwheel'):]}"
        elif line == "TryExec=dropwheel":
            line = f"TryExec={plain}"
        elif line == "Dependencies=dropwheel;":
            line = f"Dependencies={plain};"
        lines.append(line)
    return "\n".join(lines) + "\n"


class _Setup:
    """State of one ``dropwheel setup`` run."""

    def __init__(self, ctx: Ctx, *, dry: bool, apply: bool) -> None:
        from . import shellctl

        self.ctx = ctx
        self.dry = dry
        self.apply = apply
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.src = shellctl.integrations_dir()
        self.term = ctx.term
        self.launcher = launcher_path()

    def t(self, key: str, /, **fmt: Any) -> str:
        return self.ctx.t(key, **fmt)

    def say(self, text: str) -> None:
        prefix = self.term.dim(self.t("cli.setup.dry_prefix") + " ") if self.dry else ""
        out(f"  {prefix}{text}")

    def source(self, *parts: str) -> Path:
        path = self.src.joinpath(*parts)
        if not path.is_file():
            raise UserError("cli.setup.missing_source", path=str(path))
        return path

    def backup_path(self, path: Path) -> Path:
        candidate = path.with_name(f"{path.name}.dropwheel-bak-{self.stamp}")
        n = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = path.with_name(f"{path.name}.dropwheel-bak-{self.stamp}-{n}")
            n += 1
        return candidate

    def _same_content(self, dest: Path, data: bytes) -> bool:
        try:
            return dest.is_file() and not dest.is_symlink() and dest.read_bytes() == data
        except OSError:
            return False

    def install_file(self, src: Path, dest: Path, mode: int, *, launcher_style: str | None = None) -> str:
        """Copy ``src`` to ``dest`` (backup of a different existing file); ``same|created|updated``.

        With ``launcher_style`` the copy is pointed at the launcher (:func:`rewrite_launcher`)."""
        term = self.term
        data = src.read_bytes()
        if launcher_style and self.launcher:
            data = rewrite_launcher(data.decode("utf-8"), self.launcher, launcher_style).encode("utf-8")
        if self._same_content(dest, data):
            if not self.dry and os.name == "posix" and (dest.stat().st_mode & 0o777) != mode:
                with contextlib.suppress(OSError):
                    os.chmod(dest, mode)
            self.say(f"{term.ok()} {self.t('cli.setup.up_to_date', path=str(dest))}")
            return "same"
        exists = dest.exists() or dest.is_symlink()
        backup = self.backup_path(dest) if exists else None
        if self.dry:
            self.say(self.t("cli.setup.would_copy", src=str(src), dest=str(dest)))
            if backup is not None:
                self.say(self.t("cli.setup.would_backup", path=str(backup)))
            return "updated" if exists else "created"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if backup is not None:
            if dest.is_symlink():
                os.replace(dest, backup)  # keep the link itself, never write through it
            else:
                shutil.copy2(dest, backup)
        tmp = dest.with_name(f".{dest.name}.dropwheel-tmp")
        try:
            tmp.write_bytes(data)
            with contextlib.suppress(OSError):
                os.chmod(tmp, mode)
            os.replace(tmp, dest)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise UserError("cli.setup.write_failed", details=str(exc), path=str(dest)) from None
        if backup is not None:
            self.say(f"{term.ok()} {self.t('cli.setup.updated', path=str(dest), backup=str(backup))}")
            return "updated"
        self.say(f"{term.ok()} {self.t('cli.setup.created', path=str(dest))}")
        return "created"

    def ensure_require(self, loader: Path) -> None:
        """Add ``require("dropwheel")`` to ``loader`` once (backup first): at the end, or before a
        final ``return``, which Lua only accepts as the last statement of the file."""
        term = self.term
        line = 'require("dropwheel")'
        block = f"\n-- Dropwheel (dropwheel setup hyprland)\n{line}\n"
        if loader.is_file():
            if lua_requires_dropwheel(loader):
                self.say(f"{term.ok()} {self.t('cli.setup.require_present', path=str(loader))}")
                return
            backup = self.backup_path(loader)
            if self.dry:
                self.say(self.t("cli.setup.would_append", line=line, path=str(loader)))
                self.say(self.t("cli.setup.would_backup", path=str(backup)))
                return
            try:
                with open(loader, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                    text = fh.read()
                shutil.copy2(loader, backup)
                # written in place: symlinks and permissions of the user's file are preserved
                final_return = _lua_final_return(text)
                if final_return is None:
                    with open(loader, "a", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                        if text and not text.endswith("\n"):
                            fh.write("\n")
                        fh.write(block)
                else:
                    head = text[:final_return]
                    if head and not head.endswith("\n"):
                        head += "\n"
                    with open(loader, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                        fh.write(head + block.lstrip("\n") + "\n" + text[final_return:])
            except OSError as exc:
                raise UserError("cli.setup.write_failed", details=str(exc), path=str(loader)) from None
            self.say(f"{term.ok()} {self.t('cli.setup.require_added', path=str(loader), backup=str(backup))}")
            return
        if self.dry:
            self.say(self.t("cli.setup.would_create", path=str(loader), line=line))
            return
        try:
            loader.parent.mkdir(parents=True, exist_ok=True)
            loader.write_text(block.lstrip("\n"), encoding="utf-8")
        except OSError as exc:
            raise UserError("cli.setup.write_failed", details=str(exc), path=str(loader)) from None
        self.say(f"{term.ok()} {self.t('cli.setup.created', path=str(loader))}")


def _lua_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lua_final_return(text: str) -> int | None:
    """Offset of the ``return`` statement at the top level of a Lua chunk, if it has one."""
    depth = 0
    for match in _LUA_TOKEN_RE.finditer(text):
        word = match.group("word")
        if word in ("function", "do", "if", "repeat"):  # "while"/"for" open their block with "do"
            depth += 1
        elif word in ("end", "until"):
            depth -= 1
        elif word == "return" and depth <= 0:
            return match.start()
    return None


def _setup_hyprland(st: _Setup) -> int:
    term = st.term
    out(term.bold("Hyprland"))
    src = st.source("hyprland", "dropwheel.lua")
    cfg_home = util.xdg_config_home()
    caelestia = cfg_home / "caelestia"
    if caelestia_config_active(cfg_home):
        st.install_file(src, caelestia / "dropwheel.lua", 0o644)
        st.ensure_require(caelestia / "hypr-user.lua")
        out()
        out(st.t("cli.setup.hypr_next"))
        out("  " + term.cyan("hyprctl reload && dropwheel start"))
        return EXIT_OK
    hypr = cfg_home / "hypr"
    st.install_file(src, hypr / "dropwheel.lua", 0o644)
    main_cfg = hypr / "hyprland.lua"
    out()
    if main_cfg.is_file() and lua_requires_dropwheel(main_cfg):
        out(f"{term.ok()} {st.t('cli.setup.require_present', path=str(main_cfg))}")
        out("  " + term.cyan("hyprctl reload && dropwheel start"))
        return EXIT_OK
    out(st.t("cli.setup.hypr_no_caelestia", path=str(main_cfg)))
    out("  " + term.cyan(f"package.path = package.path .. {_lua_string(';' + str(hypr) + '/?.lua')}"))
    out("  " + term.cyan('require("dropwheel")'))
    out(st.t("cli.setup.hypr_then"))
    out("  " + term.cyan("hyprctl reload && dropwheel start"))
    return EXIT_OK


def _setup_simple(st: _Setup, title: str, src_parts: tuple[str, ...], dest: Path, mode: int, note_key: str,
                  launcher_style: str | None = None) -> int:
    out(st.term.bold(title))
    st.install_file(st.source(*src_parts), dest, mode, launcher_style=launcher_style)
    if not st.dry:
        out("  " + st.term.dim(st.t(note_key)))
    return EXIT_OK


def _desktop_mimes(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and line.startswith("MimeType="):
            return [m for m in line.split("=", 1)[1].split(";") if m and "*" not in m]
    return []


def _mime_defaults(mimes: list[str]) -> dict[str, str]:
    from concurrent.futures import ThreadPoolExecutor

    def query(mime: str) -> tuple[str, str]:
        res = util.run_quiet(["xdg-mime", "query", "default", mime], timeout=10)
        return mime, (res.stdout.strip() if res is not None and res.returncode == 0 else "")

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(query, mimes))


def _setup_desktop(st: _Setup) -> int:
    term = st.term
    out(term.bold(st.t("cli.setup.desktop_title")))
    src = st.source("desktop", "dropwheel.desktop")
    dest = util.xdg_data_home() / "applications" / "dropwheel.desktop"
    mimes = _desktop_mimes(src)
    guard = bool(mimes) and tools.have("xdg-mime") and not st.dry
    before = _mime_defaults(mimes) if guard else {}
    result = st.install_file(src, dest, 0o644, launcher_style="desktop")
    if st.dry or result == "same":
        return EXIT_OK
    if tools.have("update-desktop-database"):
        util.run_quiet(["update-desktop-database", str(dest.parent)], timeout=60)
    if guard:
        # a new entry listing many types may become the implicit default handler: keep the old ones
        after = _mime_defaults(mimes)
        repinned = 0
        for mime in mimes:
            previous = before.get(mime, "")
            if after.get(mime) == "dropwheel.desktop" and previous and previous != "dropwheel.desktop":
                res = util.run_quiet(["xdg-mime", "default", previous, mime], timeout=10)
                if res is not None and res.returncode == 0:
                    repinned += 1
        if repinned:
            st.say(term.dim(st.ctx.tn("cli.setup.mime_repinned", repinned)))
    return EXIT_OK


def _thunar_action(st: _Setup) -> str:
    src = st.source("thunar", "uca-action.xml")
    text = src.read_text(encoding="utf-8")
    match = re.search(r"<action>.*?</action>", text, re.S)
    if not match:
        raise UserError("cli.setup.missing_source", path=str(src))
    action = match.group(0)
    if st.launcher:
        # Thunar expands %-codes, then splits the command like a shell would
        command = st.launcher if re.fullmatch(r"[A-Za-z0-9._/+@-]+", st.launcher) else _shell_quote(st.launcher)
        command = xml_escape(command.replace("%", "%%"))
        action = action.replace("<command>dropwheel open %F</command>", f"<command>{command} open %F</command>")
    return action


def _setup_thunar(st: _Setup) -> int:
    import xml.etree.ElementTree as ET

    term = st.term
    out(term.bold("Thunar"))
    action = _thunar_action(st)
    uca = util.xdg_config_home() / "Thunar" / "uca.xml"
    text = ""
    if uca.is_file():
        try:
            text = uca.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UserError("cli.setup.thunar_invalid", details=str(exc), path=str(uca)) from None
        if _THUNAR_ID in text or "dropwheel open" in text:
            st.say(f"{term.ok()} {st.t('cli.setup.thunar_present', path=str(uca))}")
            return EXIT_OK
    if not st.apply:
        out("  " + st.t("cli.setup.thunar_why", path=str(uca)))
        out()
        for line in action.splitlines():
            out("    " + line.replace("\t", "  "))
        out()
        out("  " + st.t("cli.setup.thunar_apply_hint"))
        out("  " + term.cyan("dropwheel setup thunar --apply"))
        out("  " + term.dim(st.t("cli.setup.thunar_readme", path=str(st.src / "thunar" / "README.md"))))
        return EXIT_OK
    if text:
        try:
            ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as exc:
            raise UserError("cli.setup.thunar_invalid", details=str(exc), path=str(uca)) from None
        idx = text.rfind("</actions>")
        if idx < 0:
            raise UserError("cli.setup.thunar_invalid", details="</actions> not found", path=str(uca))
        head = text[:idx]
        if not head.endswith("\n"):
            head += "\n"
        new = head + action + "\n" + text[idx:]
    else:
        new = '<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n' + action + "\n</actions>\n"
    try:
        ET.fromstring(new.encode("utf-8"))
    except ET.ParseError as exc:  # pragma: no cover - our snippet is valid XML
        raise UserError("cli.setup.thunar_invalid", details=str(exc), path=str(uca)) from None
    backup = st.backup_path(uca) if text else None
    if st.dry:
        st.say(st.t("cli.setup.thunar_would_add", path=str(uca)))
        if backup is not None:
            st.say(st.t("cli.setup.would_backup", path=str(backup)))
        return EXIT_OK
    try:
        uca.parent.mkdir(parents=True, exist_ok=True)
        if backup is not None:
            shutil.copy2(uca, backup)
        with open(uca, "w", encoding="utf-8") as fh:  # in place: keeps a symlinked uca.xml working
            fh.write(new)
    except OSError as exc:
        raise UserError("cli.setup.write_failed", details=str(exc), path=str(uca)) from None
    if backup is not None:
        st.say(f"{term.ok()} {st.t('cli.setup.updated', path=str(uca), backup=str(backup))}")
    else:
        st.say(f"{term.ok()} {st.t('cli.setup.created', path=str(uca))}")
    out("  " + st.t("cli.setup.thunar_restart"))
    out("  " + term.dim(st.t("cli.setup.thunar_caelestia")))
    return EXIT_OK


def cmd_setup(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel setup TARGET``: install the desktop integrations for the current user."""
    st = _Setup(ctx, dry=args.dry_run, apply=args.apply)
    if not st.src.is_dir():
        raise UserError("cli.setup.no_integrations", path=str(st.src))
    if args.apply and args.target not in ("thunar", "all"):
        raise UsageError(ctx.t("cli.setup.apply_only_thunar"))
    data = util.xdg_data_home()
    handlers: dict[str, Callable[[], int]] = {
        "hyprland": lambda: _setup_hyprland(st),
        "nautilus": lambda: _setup_simple(st, "Nautilus", ("nautilus", "Dropwheel"),
                                          data / "nautilus" / "scripts" / "Dropwheel", 0o755, "cli.setup.nautilus_note"),
        "dolphin": lambda: _setup_simple(st, "Dolphin", ("dolphin", "dropwheel.desktop"),
                                         data / "kio" / "servicemenus" / "dropwheel.desktop", 0o755,
                                         "cli.setup.dolphin_note", launcher_style="desktop"),
        "nemo": lambda: _setup_simple(st, "Nemo", ("nemo", "dropwheel.nemo_action"),
                                      data / "nemo" / "actions" / "dropwheel.nemo_action", 0o644, "cli.setup.nemo_note",
                                      launcher_style="shell"),
        "thunar": lambda: _setup_thunar(st),
        "desktop": lambda: _setup_desktop(st),
    }
    if args.target != "all":
        return handlers[args.target]()
    code = EXIT_OK
    installed_fm = {"nautilus": "nautilus", "dolphin": "dolphin", "nemo": "nemo", "thunar": "thunar"}
    for name in ("hyprland", "desktop", "nautilus", "dolphin", "nemo", "thunar"):
        if name in installed_fm and not tools.have(installed_fm[name]):
            out(ctx.term.bold(name.capitalize()))
            out("  " + ctx.term.dim(ctx.t("cli.setup.skipped_not_installed", name=name)))
            out()
            continue
        try:
            rc = handlers[name]()
        except UserError as exc:
            _print_user_error(ctx, exc)
            rc = EXIT_FAIL
        code = max(code, rc)
        out()
    return code


# =================================================================================== shares

def cmd_shares(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel shares [--stop ID|--stop-all]``: LAN shares started by "Share → Phone"."""
    from . import share

    term = ctx.term
    if args.stop:
        if share.stop_share(args.stop):
            out(f"{term.ok()} {ctx.t('cli.shares.stopped', id=args.stop)}")
            return EXIT_OK
        err(f"{ctx.eterm.fail()} {ctx.t('cli.shares.not_running', id=args.stop)}")
        return EXIT_FAIL
    items = share.list_shares()
    if args.stop_all:
        n = sum(1 for s in items if share.stop_share(str(s.get("id"))))
        out(f"{term.ok()} {ctx.tn('cli.shares.stopped_all', n)}")
        return EXIT_OK
    if args.json:
        out(_dumps(items))
        return EXIT_OK
    if not items:
        out(ctx.t("cli.shares.none"))
        return EXIT_OK
    rows = []
    for s in items:
        until = ""
        with contextlib.suppress(TypeError, ValueError, OSError, OverflowError):
            until = datetime.fromtimestamp(float(s.get("expires"))).strftime("%H:%M")
        rows.append((str(s.get("id")), str(s.get("url") or ""), until, _short_list(s.get("paths") or [], term)))
    headers = ("ID", "URL", ctx.t("cli.col.until"), ctx.t("cli.col.files"))
    for line in _table(term, headers, rows, flex=3):
        out(line)
    return EXIT_OK


# ================================================================================= internal

def cmd_daemon(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel daemon``: the JSON-lines server used by the QML shell (never returns)."""
    from . import daemon

    extra = list(getattr(args, "daemon_args", None) or [])
    if ctx.debug and "--debug" not in extra:
        extra.append("--debug")
    return daemon.main(extra)


def cmd_share_serve(args: argparse.Namespace, ctx: Ctx) -> int:
    """``dropwheel share-serve ID``: body of a detached share server (internal)."""
    from . import share

    return share.serve_main([args.share_id])
