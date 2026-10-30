"""User-defined actions from the configuration (``[[custom]]`` tables, SPEC §3.6, docs/CONFIG.md).

Every entry becomes an action ``custom.user.<id>`` with the configured label, icon, categories and
multi-file mode.  They are not listed in any curated layout: the menu builder appends applicable
actions a layout does not mention, grouped by id prefix, so they land at the end of their
categories' ring inside the registered group ``custom.user`` ("Personalizzate"); actions for every
category land on the File page.  :func:`reload_custom` (also exported as ``register``/``setup``,
the hook :func:`dropwheel.actions.load_all` calls) unregisters the previous set and registers the
current one, so a configuration reload never leaves stale actions behind.

Command templates
-----------------
* a **string** runs through ``sh -c``; every placeholder becomes a reference to an environment
  variable, quoted for the shell context it appears in (``{file}`` → ``"${DROPWHEEL_FILE}"``), so
  a file name is never parsed as shell text, at any nesting (``"$(cmd {file})"`` too); pipes,
  redirections and ``&&`` work.  ``{{`` / ``}}`` are literal braces and ``${file}`` is the shell's
  own variable.  A program that takes code as an argument parses what it gets once more
  (``sh -c '… {file}'``, ``eval``, ``python -c``): such code should read the variable itself
  (``"$DROPWHEEL_FILE"``, ``os.environ["DROPWHEEL_FILE"]``);
* a **list** is executed directly: every element is one argument, an element that is exactly
  ``{files}`` expands to one argument per file.

Placeholders: ``{file}`` ``{files}`` ``{dir}`` ``{name}`` ``{stem}`` ``{ext}`` ``{out}`` (the folder
where outputs belong: next to the source, ``output.folder`` or ``~/Downloads/Dropwheel``), also in
the environment of every command as ``DROPWHEEL_FILE`` ``DROPWHEEL_DIR`` ``DROPWHEEL_NAME``
``DROPWHEEL_STEM`` ``DROPWHEEL_EXT`` ``DROPWHEEL_OUT``; ``DROPWHEEL_FILES`` has one path per line.
Paths are always absolute (no leading-dash problems).  ``output = "files"`` reports the new files
that appeared in ``{dir}``/``{out}``; ``"text"`` shows the command's standard output; ``"none"``
only the outcome.  A line ``NN%`` on standard output updates the progress bar.

Custom actions offer no Undo: all that is known about their outputs is that those files appeared
while the command ran, which is also true of a download finishing in the same folder, and Undo
would move that to the trash.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any, Sequence

from .. import config as cfgmod
from .. import registry, tools, util
from ..detect import FileInfo
from ..errors import ToolError, UserError
from ..jobs import Context
from ..registry import Action, group

log = logging.getLogger("dropwheel.custom")

__all__ = ["PREFIX", "reload_custom", "register", "setup", "render_shell", "shell_env", "render_argv",
           "placeholder_values", "registered_ids"]

PREFIX = "custom.user."
PLACEHOLDERS = ("files", "file", "dir", "name", "stem", "ext", "out")
_PH_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
_PROGRESS_RE = re.compile(r"^\s*(\d{1,3}(?:[.,]\d+)?)\s*%\s*$")
_HEREDOC_RE = re.compile(r"""<<(-?)[ \t]*((?:[^\s;&|<>()'"\\]|\\.|'[^']*'|"[^"]*")+)""")
_ENV_VALUE_MAX = 128 * 1024 - 64    # Linux refuses to start a program with a longer environment string
_SHELL_WORDS = {"cd", "export", "exec", "eval", "set", "unset", "if", "for", "while", "until", "case", "test", "[",
                "{", "(", "!", "true", "false", "echo", "printf", "read", "source", ".", ":", "time", "env", "nohup",
                "command", "exit", "return", "trap", "ulimit", "umask", "wait", "kill", "shift", "alias", "local",
                "type", "hash", "pwd", "times", "getopts", "break", "continue", "readonly", "builtin", "select",
                "function", "declare", "typeset", "let", "sleep"}
_TEXT_LIMIT = 1024 * 1024

_REGISTERED: set[str] = set()

group("custom.user", "grp.custom", "extension", [])


# ================================================================================ rendering

def _env_name(key: str, index: int = 0) -> str:
    """The environment variable holding a placeholder's value (``{files}``: one per file)."""
    return f"DROPWHEEL_FILE_{index + 1}" if key == "files" else f"DROPWHEEL_{key.upper()}"


def shell_env(values: dict[str, list[str]]) -> dict[str, str]:
    """The environment a script from :func:`render_shell` needs."""
    return {_env_name(key, i): value for key, vals in values.items() for i, value in enumerate(vals)}


def _reference(state: str, key: str, values: dict[str, list[str]]) -> str:
    """Script text that expands placeholder ``key`` in the shell context ``state``."""
    vals = values.get(key, [])
    if not vals:
        return ""
    if state == "<<'":
        # nothing expands in a quoted here-document, the text is all there is (and a line of the
        # value must not end the document early)
        return " ".join(value.replace("\n", " ") for value in vals)
    if state == "((":
        # bash evaluates the expanded text as arithmetic, and "a[$(cmd)]" runs cmd: leading digits only
        return " ".join(f"${{{_env_name(key, i)}%%[!0-9]*}}" for i in range(len(vals)))
    refs = [f"${{{_env_name(key, i)}}}" for i in range(len(vals))]
    if state in ('"', "<<"):            # already one word, and quotes would be literal in a here-document
        return " ".join(refs)
    if state == "'":
        return "'\"" + " ".join(refs) + "\"'"
    if state == "$'":
        return "'\"" + " ".join(refs) + "\"$'"
    return " ".join(f'"{ref}"' for ref in refs)


def render_shell(template: str, values: dict[str, list[str]]) -> str:
    """Turn a ``sh -c`` template into a script that reads the placeholders from the environment.

    Names never become shell text, so no name can run as a command, whatever the template looks
    like: ``{file}`` becomes ``"${DROPWHEEL_FILE}"`` and the shell expands it (see
    :func:`shell_env`).  Following the template's contexts (quotes, ``$(…)``, backticks, comments,
    here-documents) only decides the quoting around that reference, so that a value stays one word:
    a context this scanner gets wrong gives the wrong quotes at worst.  The exception is the body of
    a quoted here-document, where expansions do not exist: the value is copied there.
    """
    out: list[str] = []
    stack: list[str] = []                       # open contexts, innermost last
    pending: list[tuple[str, bool, bool]] = []  # here-documents announced, their bodies start at the next newline
    docs: list[tuple[str, bool]] = []           # (delimiter, strip tabs) of the open here-documents
    i = 0
    n = len(template)

    def open_doc() -> None:
        delim, quoted, strip = pending.pop(0)
        stack.append("<<'" if quoted else "<<")
        docs.append((delim, strip))

    while i < n:
        state = stack[-1] if stack else ""
        ch = template[i]
        two = template[i:i + 2]
        if state == "#":
            if ch != "\n":
                out.append(ch)
                i += 1
                continue
            stack.pop()
            state = stack[-1] if stack else ""
        if state in ("<<", "<<'") and template[i - 1] == "\n":
            end = template.find("\n", i)
            end = n if end < 0 else end
            delim, strip = docs[-1]
            if (template[i:end].lstrip("\t") if strip else template[i:end]) == delim:
                out.append(template[i:end + 1])
                i = end + 1
                stack.pop()
                docs.pop()
                if pending:
                    open_doc()
                continue
        if two in ("{{", "}}"):
            out.append(ch)
            i += 2
            continue
        if two == "${" and template[i + 2:i + 3] != "{":    # a shell variable, also when it is called ${file}
            out.append(two)
            i += 2
            continue
        m = _PH_RE.match(template, i)
        if m:
            out.append(_reference(state, m.group(1), values))
            i = m.end()
            continue
        step = 1
        if state == "'":
            if ch == "'":
                stack.pop()
        elif state == "$'":
            if ch == "\\":
                step = 2
            elif ch == "'":
                stack.pop()
        elif state != "<<'":
            if ch == "\\":
                step = 2
            elif template.startswith("$((", i):
                stack += ["((", "(("]
                step = 3
            elif two == "$(":
                stack.append("$(")
                step = 2
            elif ch == "`":
                if "`" in stack:                            # the closing one, whatever is open inside
                    del stack[len(stack) - 1 - stack[::-1].index("`"):]
                else:
                    stack.append("`")
            elif state == '"':
                if ch == '"':
                    stack.pop()
            elif state == "((":
                if ch == "(":
                    stack.append("((")
                elif ch == ")":
                    stack.pop()
            elif state != "<<":                             # a command line, at any depth
                if two == "$'":
                    stack.append("$'")
                    step = 2
                elif ch in "'\"(":
                    stack.append(ch)
                elif ch == ")":
                    if state in ("$(", "("):
                        stack.pop()
                elif ch == "#" and (i == 0 or template[i - 1] in " \t\n;&|()`"):
                    stack.append("#")
                elif template.startswith("<<<", i):
                    step = 3
                elif two == "<<":
                    doc = _HEREDOC_RE.match(template, i)
                    if doc:
                        delim = re.sub(r"\\(.)|['\"]", lambda q: q.group(1) or "", doc.group(2))
                        pending.append((delim, bool(re.search(r"['\"\\]", doc.group(2))), doc.group(1) == "-"))
                        step = doc.end() - i
                elif ch == "\n" and pending:
                    open_doc()
        out.append(template[i:i + step])
        i += step
    return "".join(out)


def render_argv(template: Sequence[str], values: dict[str, list[str]]) -> list[str]:
    """Fill a list command: ``"{files}"`` alone expands to one argument per file."""
    argv: list[str] = []
    for item in template:
        if item == "{files}":
            argv.extend(values.get("files", []))
            continue
        text = item.replace("{{", "\x00").replace("}}", "\x01")
        text = _PH_RE.sub(lambda m: " ".join(values.get(m.group(1), [])), text)
        argv.append(text.replace("\x00", "{").replace("\x01", "}"))
    return argv


def _out_dir(config: dict, first: FileInfo) -> Path:
    if cfgmod.get(config, "output.location") == "folder":
        folder = cfgmod.output_folder(config)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.access(folder, os.W_OK):
                return folder
        except OSError:
            pass
    elif first.path and not first.volatile:
        parent = Path(first.path).parent
        if parent.is_dir() and os.access(parent, os.W_OK):
            return parent
    fb = util.fallback_output_dir()
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def placeholder_values(ctx: Context) -> dict[str, list[str]]:
    """Values for every placeholder (original paths when they still exist, else the staged copies)."""
    paths: list[str] = []
    origs: list[FileInfo] = []
    for f in ctx.files:
        orig = ctx.original(f)
        origs.append(orig)
        if orig.path and os.path.exists(orig.path):
            paths.append(os.path.abspath(orig.path))
        else:
            paths.append(os.path.abspath(f.path))
    first = origs[0]
    first_path = paths[0]
    name = first.name or os.path.basename(first_path)
    stem, ext = (first.stem, first.ext) if first.name else util.split_name(name)
    return {
        "file": [first_path],
        "files": paths,
        "dir": [os.path.dirname(first_path)],
        "name": [name],
        "stem": [stem or name],
        "ext": [ext],
        "out": [str(_out_dir(ctx.config, first))],
    }


def _snapshot(dirs: Sequence[str]) -> dict[str, set[str]]:
    snap: dict[str, set[str]] = {}
    for d in dirs:
        try:
            with os.scandir(d) as it:
                snap[d] = {e.name for e in it}
        except OSError:
            snap[d] = set()
    return snap


def _new_entries(before: dict[str, set[str]]) -> list[Path]:
    found: list[Path] = []
    for d, names in before.items():
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.name in names or entry.name.startswith("."):
                        continue
                    if entry.name.endswith((".part", ".tmp", ".crdownload")):
                        continue
                    found.append(Path(entry.path))
        except OSError:
            continue
    return util.natural_sorted(found, key=lambda p: p.name)


# ================================================================================ actions

def _program(command: Any) -> str | None:
    """The program a command runs (for the menu's availability check), ``None`` if unknown."""
    if isinstance(command, list):
        first = command[0] if command else ""
    else:
        try:
            words = shlex.split(str(command))
        except ValueError:
            return None
        first = words[0] if words else ""
    if not first or "=" in first or first in _SHELL_WORDS or _PH_RE.search(first) or "$" in first:
        return None
    if not re.fullmatch(r"[\w./+~-]+", first):
        return None
    if first.startswith("~"):
        first = os.path.expanduser(first)
    return first


def _make_fn(entry: dict):
    command = entry["command"]
    output = (entry.get("output") or "none").lower()
    label = entry.get("label") or entry["id"]

    def run_custom(ctx: Context):
        values = placeholder_values(ctx)
        cwd = values["dir"][0]
        # one variable per file only for the templates that use them: the environment has a size limit too
        env = shell_env(values if not isinstance(command, list) and "{files}" in str(command)
                        else {k: v for k, v in values.items() if k != "files"})
        env["DROPWHEEL_LANG"] = ctx.lang
        names = "\n".join(values["files"])
        if len(os.fsencode(names)) < _ENV_VALUE_MAX:
            env["DROPWHEEL_FILES"] = names
        if isinstance(command, list):
            argv = render_argv(command, values)
            if not argv:
                raise UserError("err.custom.empty", label=label)
        else:
            argv = ["sh", "-c", render_shell(str(command), values)]
        watch = sorted({*values["dir"], *values["out"], *{os.path.dirname(p) for p in values["files"]}})
        before = _snapshot(watch) if output == "files" else {}
        ctx.progress(None, label)

        def on_line(line: str) -> None:
            m = _PROGRESS_RE.match(line)
            if m:
                try:
                    ctx.progress(float(m.group(1).replace(",", ".")) / 100.0, label)
                except ValueError:
                    pass

        try:
            res = ctx.run(argv, cwd=cwd if os.path.isdir(cwd) else None, env=env, on_line=on_line)
        except ToolError as exc:
            if exc.message == "err.tool_failed":
                raise UserError("err.custom.failed", details=exc.details, label=label,
                                code=exc.fmt.get("code", "?")) from None
            raise
        if output == "files":
            outs = _new_entries(before)
            if not outs:
                ctx.warn(ctx.t("warn.custom.no_files"))
                return ctx.result(kind="none", message=ctx.t("msg.custom.done", label=label))
            return ctx.result(outs, message=ctx.t("msg.custom.done", label=label))
        if output == "text":
            text = res.stdout or ""
            if len(text) > _TEXT_LIMIT:
                text = text[:_TEXT_LIMIT] + "\n…"
            if not text.strip():
                return ctx.result(kind="none", message=ctx.t("msg.custom.no_output", label=label))
            return ctx.result(kind="text", text=text, message=ctx.t("msg.custom.done", label=label))
        return ctx.result(kind="none", message=ctx.t("msg.custom.done", label=label))

    run_custom.__name__ = f"custom_{re.sub(r'[^A-Za-z0-9_]', '_', entry['id'])}"
    run_custom.__doc__ = f"User action {entry['id']!r} from config.toml."
    return run_custom


def _build(entry: dict) -> Action | None:
    ident = entry.get("id")
    command = entry.get("command")
    if not isinstance(ident, str) or not ident or not command:
        return None
    cats = entry.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    cats = [c for c in cats if isinstance(c, str)]
    multi = entry.get("multi", "each")
    if multi not in registry.MULTI_MODES:
        multi = "each"
    program = _program(command)
    requires = [program] if program and program not in ("sh", "bash") else []
    if isinstance(command, str):
        requires.append("sh")
    preview = " ".join(command) if isinstance(command, list) else str(command)
    act = Action(
        id=PREFIX + ident,
        label=str(entry.get("label") or ident),
        fn=_make_fn(entry),
        icon=str(entry.get("icon") or "terminal"),
        categories=frozenset(cats),
        multi=multi,
        mode="instant",
        requires=requires,
        undoable=False,     # new files in a folder are not proof of who made them (module docstring)
        description=str(entry.get("description") or preview)[:300],
        mixed_ok=True,
        order=int(entry.get("order", 50)) if isinstance(entry.get("order", 50), int) else 50,
        module=__name__,
    )
    return act


def reload_custom(config: dict | None = None) -> list[str]:
    """(Re)register the ``[[custom]]`` actions of ``config`` (loaded from disk when ``None``).

    Previously registered custom actions are removed first.  Returns the registered action ids.
    Invalid entries are skipped (``config.load`` already reports them in ``_warnings``).
    """
    if config is None:
        try:
            config = cfgmod.load()
        except Exception:  # a broken config file must never break the action modules
            log.exception("cannot load the configuration for custom actions")
            config = {}
    entries = config.get("custom") if isinstance(config, dict) else None
    for ident in list(_REGISTERED):
        registry.unregister(ident)
    _REGISTERED.clear()
    ids: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        try:
            act = _build(entry)
        except (TypeError, ValueError) as exc:
            log.warning("custom action %r skipped: %s", entry.get("id"), exc)
            continue
        if act is None or act.id in seen:
            continue
        try:
            registry.register(act)
        except ValueError as exc:
            log.warning("custom action %s rejected: %s", act.id, exc)
            continue
        seen.add(act.id)
        _REGISTERED.add(act.id)
        ids.append(act.id)
    try:
        tools.refresh()
    except Exception:  # pragma: no cover - cache refresh is best effort
        pass
    return ids


def registered_ids() -> list[str]:
    """Ids of the custom actions currently registered."""
    return sorted(_REGISTERED)


#: hooks looked up by :func:`dropwheel.actions.load_all`
register = reload_custom
setup = reload_custom
