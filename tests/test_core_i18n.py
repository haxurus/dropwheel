"""Tests for dropwheel.i18n and the core locale fragments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dropwheel import i18n

LOCALES = Path(__file__).resolve().parent.parent / "src" / "dropwheel" / "locales"


def test_translate_it_en():
    assert i18n.t("grp.convert", "it") == "Converti"
    assert i18n.t("grp.convert", "en") == "Convert"
    assert i18n.t("sfx.compressed", "it") == "compresso"
    assert i18n.t("sfx.compressed", "en") == "Compressed"
    assert i18n.t("page.tools", "it") == "Strumenti"


def test_placeholders_and_missing_keys():
    assert i18n.t("err.file_not_found", "it", name="a.pdf") == "File non trovato: a.pdf"
    assert i18n.t("PNG", "it") == "PNG"
    assert i18n.t("err.file_not_found", "en") == "File not found: {name}"
    assert i18n.t("Literal {weird", "en", x=1) == "Literal {weird"
    assert i18n.t("", "en") == ""
    # no number agreement needed: one failure is the common case
    assert i18n.t("job.batch_summary", "it", done=3, total=4, failed=1) == "3/4 completati · non riusciti: 1"
    assert i18n.t("grp.resize", "it") == "Ridimensiona"
    # placeholder names may collide with parameter names; lang= keyword is accepted too
    assert i18n.t("cfg.invalid", "en", key="a.b", value="1", default="2").startswith("Config: invalid value for a.b")
    assert i18n.t("grp.more", lang="it") == "Altro"
    assert i18n.tn("menu.files", 2, lang="it") == "2 file"


def test_plural():
    assert i18n.tn("menu.files", 1, "it") == "1 file"
    assert i18n.tn("menu.files", 3, "it") == "3 file"
    assert i18n.tn("menu.files", 3, "en") == "3 files"
    assert i18n.tn("menu.files", 1, "en") == "1 file"
    assert i18n.tn("no.such.key", 2, "en") == "no.such.key"


def test_english_fallback_and_user_overrides(dropwheel_env):
    en_dir = dropwheel_env.config / "dropwheel" / "locales" / "en"
    en_dir.mkdir(parents=True)
    (en_dir / "extra.json").write_text(json.dumps({"test.only_en": "Only English", "grp.convert": "Transmute"}),
                                       encoding="utf-8")
    it_dir = dropwheel_env.config / "dropwheel" / "locales" / "it"
    it_dir.mkdir(parents=True)
    (it_dir / "broken.json").write_text("{not json", encoding="utf-8")
    i18n.reload()
    assert i18n.t("test.only_en", "it") == "Only English"
    assert i18n.t("grp.convert", "en") == "Transmute"
    assert i18n.t("grp.convert", "it") == "Converti"
    assert i18n.has("test.only_en", "it") and not i18n.has("nope.nope", "it")


def test_all_strings_merged():
    strings = i18n.all_strings("it")
    assert strings["grp.more"] == "Altro"
    assert set(i18n.all_strings("en")) <= set(strings)


@pytest.mark.parametrize("env,expected", [
    ({"LANG": "it_IT.UTF-8"}, "it"),
    ({"LC_ALL": "it_CH.UTF-8", "LANG": "en_US.UTF-8"}, "it"),
    ({"LC_MESSAGES": "en_GB.UTF-8", "LANG": "it_IT.UTF-8"}, "en"),
    ({"LANG": "C"}, "en"),
    ({"LANG": "de_DE.UTF-8"}, "en"),
])
def test_detect_lang_env(monkeypatch, env, expected):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert i18n.detect_lang({"language": "auto"}) == expected
    assert i18n.detect_lang(None) == expected


def test_detect_lang_config_wins(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.detect_lang({"language": "it"}) == "it"
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    assert i18n.detect_lang({"language": "en"}) == "en"


def test_normalize_and_default_lang():
    assert i18n.normalize_lang("it_IT.UTF-8") == "it"
    assert i18n.normalize_lang("fr") == "en"
    assert i18n.normalize_lang(None) == "en"
    i18n.set_lang("it")
    try:
        assert i18n.t("grp.more") == "Altro" and i18n.get_lang() == "it"
    finally:
        i18n.set_lang("en")


def test_core_fragments_have_identical_keys():
    it = json.loads((LOCALES / "it" / "core.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en" / "core.json").read_text(encoding="utf-8"))
    assert set(it) == set(en)
    for key in it:
        assert it[key].strip() and en[key].strip(), key
        # placeholders must match between languages
        import re

        assert sorted(re.findall(r"\{(\w+)\}", it[key])) == sorted(re.findall(r"\{(\w+)\}", en[key])), key


def test_required_shared_keys_exist():
    required = ["grp." + g for g in ("convert compress resize edit background privacy pages extract combine audio "
                                     "video ocr share secure open file info more transform trim tools metadata").split()]
    required += ["sfx." + s for s in ("compressed cropped resized rotated flipped edited annotated background "
                                      "no_background no_metadata redacted watermarked trimmed muted normalized speed "
                                      "merged split extracted pages frames ocr organized converted copy").split()]
    strings = i18n.all_strings("it")
    for key in required:
        assert key in strings, key
