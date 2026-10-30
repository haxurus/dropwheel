"""Locale consistency across the whole project (Python backend + QML shell).

* every fragment ``locales/<lang>/*.json`` is valid JSON, a flat object of strings, without
  duplicate keys inside the file; Italian and English have the same fragments, the same keys and
  the same ``{placeholders}``; plural pairs (``.one``/``.other``) are complete;
* no key is defined by two fragments with different meanings (the daemon merges all fragments and a
  later one would silently win);
* every key referenced by Python code — ``t()``/``tn()`` calls, ``label=``/``description=``/
  ``help=``/``unit=``/``suffix=`` arguments, ``P.*``/``group()`` positional labels,
  ``UserError``/``ToolError`` messages — exists in both languages;
* every key referenced by QML/JS (``I18n.t("…")``, ``I18n.tn("…")``, ``WT.tr(I18n, "…")``…) exists in
  both languages, and dynamic prefixes (``I18n.t("dialog.adjust." + name)``) match at least one key;
* ``ctx.warn()`` / ``ctx.progress()`` never receive a raw key (they do not translate).
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "dropwheel"
LOCALES = PKG / "locales"
SHELL = ROOT / "shell"
LANGS = ("it", "en")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Translation helpers whose first argument is a key.
T_FUNCS = {"t", "_t", "tr", "_tr"}
TN_FUNCS = {"tn", "_tn", "trn"}
ERROR_CLASSES = {"UserError", "ToolError"}
KEY_KWARGS = {"label", "description", "help", "unit", "suffix"}
P_HELPERS = {"int", "float", "slider", "bool", "select", "text", "password", "textarea", "color", "time",
             "timerange", "pages", "path", "dir", "rect", "boxes", "pageops", "metadata", "adjust", "background",
             "integer", "number", "boolean", "choice"}


def _no_dupes(pairs):
    seen: dict = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _fragments(lang: str) -> dict[str, dict[str, str]]:
    out = {}
    for path in sorted((LOCALES / lang).glob("*.json")):
        out[path.name] = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_dupes)
    return out


@pytest.fixture(scope="module")
def tables() -> dict[str, dict[str, str]]:
    """Merged strings per language (like :func:`dropwheel.i18n.load`, without user overrides)."""
    out = {}
    for lang in LANGS:
        merged: dict[str, str] = {}
        for _name, data in sorted(_fragments(lang).items(), key=lambda kv: (kv[0] != "core.json", kv[0])):
            merged.update(data)
        out[lang] = merged
    return out


def _has(table: dict[str, str], key: str, plural: bool = False) -> bool:
    if key in table:
        return True
    return plural and f"{key}.one" in table and f"{key}.other" in table


# --------------------------------------------------------------------------------- fragments

def test_fragments_are_valid_and_parallel():
    names = {lang: sorted(p.name for p in (LOCALES / lang).glob("*.json")) for lang in LANGS}
    assert names["it"] == names["en"], names
    assert "core.json" in names["en"]
    for lang in LANGS:
        for name, data in _fragments(lang).items():
            assert isinstance(data, dict), f"{lang}/{name} is not an object"
            for key, value in data.items():
                assert isinstance(key, str) and key.strip() == key and key, f"{lang}/{name}: bad key {key!r}"
                assert isinstance(value, str), f"{lang}/{name}: {key} is not a string"
                assert value.strip() != "", f"{lang}/{name}: {key} is empty"


def test_same_keys_and_placeholders_in_both_languages():
    it, en = _fragments("it"), _fragments("en")
    problems = []
    for name in en:
        missing_it = sorted(set(en[name]) - set(it[name]))
        missing_en = sorted(set(it[name]) - set(en[name]))
        if missing_it:
            problems.append(f"{name}: missing in it: {missing_it[:10]}")
        if missing_en:
            problems.append(f"{name}: missing in en: {missing_en[:10]}")
        for key in set(en[name]) & set(it[name]):
            ph_en = set(PLACEHOLDER_RE.findall(en[name][key]))
            ph_it = set(PLACEHOLDER_RE.findall(it[name][key]))
            if key.endswith(".one"):
                # a singular form may spell the number out ("e un altro" / "and 1 other")
                ph_en -= {"n", "count"}
                ph_it -= {"n", "count"}
            if ph_en != ph_it:
                problems.append(f"{name}: {key}: placeholders en={sorted(ph_en)} it={sorted(ph_it)}")
    assert not problems, "\n".join(problems)


def test_plural_pairs_complete(tables):
    problems = []
    for lang, table in tables.items():
        for key in table:
            for this, other in ((".one", ".other"), (".other", ".one")):
                if key.endswith(this) and key[: -len(this)] + other not in table:
                    problems.append(f"{lang}: {key} without {other}")
    assert not problems, "\n".join(problems)


def test_no_conflicting_keys_across_fragments():
    problems = []
    for lang in LANGS:
        owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for name, data in _fragments(lang).items():
            for key, value in data.items():
                owners[key].append((name, value))
        for key, defs in owners.items():
            if len(defs) > 1 and len({v for _n, v in defs}) > 1:
                problems.append(f"{lang}: {key} defined differently in {[n for n, _v in defs]}")
    assert not problems, "\n".join(problems)


def test_hello_strings_include_every_fragment(tables):
    """The daemon sends ``i18n.all_strings`` to QML: it must contain the UI fragments too."""
    from dropwheel import i18n

    i18n.reload()
    for lang in LANGS:
        strings = i18n.all_strings(lang)
        for key in tables[lang]:
            assert key in strings, f"{lang}: {key} not served by i18n.all_strings"
        assert strings["ui.ok"] and strings["grp.convert"]
    assert i18n.all_strings("it")["grp.convert"] != i18n.all_strings("en")["grp.convert"]


# ----------------------------------------------------------------------------- Python keys

def _call_name(node: ast.Call) -> tuple[str, str]:
    fn = node.func
    if isinstance(fn, ast.Name):
        return "", fn.id
    if isinstance(fn, ast.Attribute):
        owner = fn.value.id if isinstance(fn.value, ast.Name) else ""
        return owner, fn.attr
    return "", ""


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _python_references() -> tuple[list[tuple[str, int, str, bool]], list[tuple[str, int, str]]]:
    """(key references, suspicious untranslated uses) found in the backend sources."""
    refs: list[tuple[str, int, str, bool]] = []
    suspicious: list[tuple[str, int, str]] = []
    for py in sorted(PKG.rglob("*.py")):
        rel = str(py.relative_to(ROOT))
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            owner, name = _call_name(node)
            first = _const_str(node.args[0]) if node.args else None
            if first is not None and (name in T_FUNCS or name in ERROR_CLASSES):
                refs.append((rel, node.lineno, first, False))
            if first is not None and name in TN_FUNCS:
                refs.append((rel, node.lineno, first, True))
            if owner == "P" and name in P_HELPERS and len(node.args) >= 2:
                label = _const_str(node.args[1])
                if label is not None:
                    refs.append((rel, node.lineno, label, False))
            if name in ("group", "action") and len(node.args) >= 2:
                label = _const_str(node.args[1])
                if label is not None:
                    refs.append((rel, node.lineno, label, False))
            for kw in node.keywords:
                value = _const_str(kw.value)
                if kw.arg in KEY_KWARGS and value is not None:
                    if kw.arg == "suffix" and "." not in value:
                        value = f"sfx.{value}"
                    refs.append((rel, node.lineno, value, False))
            if owner == "ctx" and name in ("warn", "progress", "log"):
                for arg in node.args:
                    value = _const_str(arg)
                    if value is not None and KEY_RE.match(value):
                        suspicious.append((rel, node.lineno, value))
    return refs, suspicious


def test_python_keys_exist_in_both_languages(tables):
    refs, suspicious = _python_references()
    assert len(refs) > 1000, "the scanner found suspiciously few references"
    problems = []
    for rel, line, key, plural in refs:
        if not KEY_RE.match(key):
            continue  # literal text ("PNG", "SHA-256") used as label
        for lang in LANGS:
            if not _has(tables[lang], key, plural):
                problems.append(f"{rel}:{line}: {key!r} missing in {lang}")
    assert not problems, "\n".join(sorted(set(problems)))
    assert not suspicious, "untranslated keys passed to ctx.warn/progress/log: " + repr(suspicious)


def test_suffix_keys_follow_spec(tables):
    """``sfx.*`` keys: Italian lower-case participles, English capitalised (SPEC §3.5)."""
    problems = []
    for key, en_text in tables["en"].items():
        if not key.startswith("sfx."):
            continue
        it_text = tables["it"].get(key, "")
        if en_text[:1].isalpha() and not en_text[:1].isupper():
            problems.append(f"en {key}: {en_text!r} should be capitalised")
        if it_text[:1].isalpha() and it_text[:1].isupper() and not it_text[:3].isupper():
            problems.append(f"it {key}: {it_text!r} should start lower-case")
        for text in (en_text, it_text):
            if any(ch in text for ch in "/\\()\n"):
                problems.append(f"{key}: {text!r} contains characters unsafe in file names")
    assert not problems, "\n".join(problems)


# -------------------------------------------------------------------------------- QML keys

_QML_CALL = re.compile(
    r"\b(I18n\.tn|I18n\.t|WT\.trn|WT\.tr|DialogUtils\.tr|DU\.tr)\(\s*(?:I18n\s*,\s*)?\"([A-Za-z0-9_.\-]+)\"\s*([,)+])")


def _qml_references() -> list[tuple[str, str, str, str]]:
    out = []
    for path in sorted(list(SHELL.rglob("*.qml")) + list(SHELL.rglob("*.js"))):
        text = path.read_text(encoding="utf-8")
        for m in _QML_CALL.finditer(text):
            fn, key, nxt = m.groups()
            out.append((str(path.relative_to(ROOT)), fn, key, nxt))
    return out


def test_qml_keys_exist_in_both_languages(tables):
    refs = _qml_references()
    assert len(refs) > 200, "the scanner found suspiciously few QML references"
    problems = []
    for rel, fn, key, nxt in refs:
        plural = fn.endswith("n")
        for lang in LANGS:
            if nxt == "+":
                if not any(k.startswith(key) for k in tables[lang]):
                    problems.append(f"{rel}: no key starts with {key!r} ({lang})")
            elif not _has(tables[lang], key, plural):
                problems.append(f"{rel}: {key!r} missing in {lang}")
    assert not problems, "\n".join(sorted(set(problems)))


def test_qml_dynamic_key_families_complete(tables):
    """Keys built at run time from a known set of names exist for every name used."""
    families = {
        "dialog.adjust.": ["exposure", "brightness", "contrast", "saturation", "temperature", "tint", "sharpen",
                           "vignette"],
        "dialog.redact.style.": ["solid", "blur", "pixelate"],
    }
    problems = []
    for prefix, names in families.items():
        for name in names:
            for lang in LANGS:
                if prefix + name not in tables[lang]:
                    problems.append(f"{prefix}{name} missing in {lang}")
    assert not problems, "\n".join(problems)
