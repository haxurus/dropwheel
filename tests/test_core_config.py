"""Tests for dropwheel.config."""

from __future__ import annotations

import tomllib

import pytest

from dropwheel import config as cfgmod


def strip_private(cfg):
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def test_defaults_without_file(dropwheel_env):
    cfg = cfgmod.load()
    assert strip_private(cfg) == cfgmod.DEFAULTS
    assert cfg["_warnings"] == []
    assert cfgmod.config_path() == dropwheel_env.config / "dropwheel" / "config.toml"


def test_valid_values_applied(dropwheel_env):
    path = dropwheel_env.base / "c.toml"
    path.write_text('language = "it"\nhidden_actions = ["image.*"]\n[wheel]\nscale = 1.25\nspring_ms = 300\n'
                    '[video]\npreset = "SLOW"\n[audio]\nopus_bitrate = "96k"\n[jobs]\nmax_parallel = 4\n'
                    '[myplugin]\nanswer = 42\n[image]\nfuture_option = "x"\n', encoding="utf-8")
    cfg = cfgmod.load(path)
    assert cfg["language"] == "it" and cfg["hidden_actions"] == ["image.*"]
    assert cfg["wheel"]["scale"] == 1.25 and cfg["wheel"]["spring_ms"] == 300
    assert cfg["video"]["preset"] == "slow" and cfg["audio"]["opus_bitrate"] == "96k"
    assert cfg["jobs"]["max_parallel"] == 4
    assert cfg["myplugin"] == {"answer": 42} and cfg["image"]["future_option"] == "x"
    assert cfg["image"]["jpeg_quality"] == 85
    assert cfg["_warnings"] == []


def test_invalid_values_replaced_with_warnings(dropwheel_env):
    path = dropwheel_env.base / "bad.toml"
    path.write_text('language = "fr"\n[wheel]\nscale = 9\nshow_unavailable = "yes"\n[image]\njpeg_quality = 0\n'
                    '[notifications]\nmode = "sometimes"\n[hud]\ntimeout_ms = -1\n[audio]\nopus_bitrate = "loud"\n'
                    'hotzone = 5\n', encoding="utf-8")
    cfg = cfgmod.load(path)
    assert cfg["language"] == "auto"
    assert cfg["wheel"]["scale"] == 1.0 and cfg["wheel"]["show_unavailable"] is True
    assert cfg["image"]["jpeg_quality"] == 85
    assert cfg["notifications"]["mode"] == "long" and cfg["hud"]["timeout_ms"] == 7000
    assert cfg["audio"]["opus_bitrate"] == "128k"
    assert len(cfg["_warnings"]) >= 7
    assert any("wheel.scale" in w for w in cfg["_warnings"])


def test_parse_error(dropwheel_env):
    path = dropwheel_env.base / "broken.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    cfg = cfgmod.load(path)
    assert strip_private(cfg) == cfgmod.DEFAULTS
    assert len(cfg["_warnings"]) == 1 and "error" in cfg["_warnings"][0].lower()


def test_custom_actions_validation(dropwheel_env):
    path = dropwheel_env.base / "custom.toml"
    path.write_text(
        '[[custom]]\nid = "upload"\nlabel = "Carica"\ncommand = "scp {file} host:"\ncategories = ["image", "nope"]\n'
        '[[custom]]\nid = "upload"\ncommand = "dup"\n'
        '[[custom]]\nlabel = "no id"\ncommand = "x"\n'
        '[[custom]]\nid = "argv"\ncommand = ["echo", "{files}"]\nmulti = "weird"\n',
        encoding="utf-8")
    cfg = cfgmod.load(path)
    custom = cfg["custom"]
    assert [c["id"] for c in custom] == ["upload", "argv"]
    assert custom[0]["categories"] == ["image"] and custom[0]["icon"] == "terminal" and custom[0]["multi"] == "each"
    assert custom[1]["command"] == ["echo", "{files}"] and custom[1]["multi"] == "each"
    assert len(cfg["_warnings"]) == 4


def test_write_default_roundtrip(dropwheel_env):
    target = cfgmod.write_default()
    assert target == cfgmod.config_path() and target.exists()
    text = target.read_text(encoding="utf-8")
    assert "Lingua" in text and "language" in text
    tomllib.loads(text)
    cfg = cfgmod.load()
    assert strip_private(cfg) == cfgmod.DEFAULTS and cfg["_warnings"] == []
    with pytest.raises(FileExistsError):
        cfgmod.write_default()
    cfgmod.write_default(force=True)


def test_env_override_and_helpers(dropwheel_env, monkeypatch):
    path = dropwheel_env.base / "alt.toml"
    path.write_text('[output]\nlocation = "folder"\nfolder = "~/Out"\n', encoding="utf-8")
    monkeypatch.setenv("DROPWHEEL_CONFIG", str(path))
    cfg = cfgmod.load()
    assert cfg["_path"] == str(path) and cfg["output"]["location"] == "folder"
    assert cfgmod.get(cfg, "output.location") == "folder"
    assert cfgmod.get(cfg, "image.webp_quality") == 80
    assert cfgmod.get({}, "pdf.dpi") == 150
    assert cfgmod.get(cfg, "no.such", "x") == "x"
    assert str(cfgmod.output_folder(cfg)).endswith("Out")
    assert cfgmod.output_folder({}) == dropwheel_env.downloads / "Dropwheel"
    pub = cfgmod.public_config(cfg)
    assert "_warnings" not in pub and "_path" not in pub
    assert pub["language_resolved"] == "en" and pub["output"]["folder_resolved"].endswith("Out")
    pub["wheel"]["scale"] = 3
    assert cfg["wheel"]["scale"] == 1.0
