# Dropwheel — build, install and check.
#
#   make                      show this help
#   make wheel                build the Python wheel (python -m build --wheel --no-isolation)
#   make install              system install (PREFIX=/usr, DESTDIR= for packaging)
#   make install-user         user-local install in ~/.local (runs ./install.sh)
#   make uninstall            remove what "make install" installed (same PREFIX/DESTDIR)
#   make uninstall-user       remove the user-local install (runs ./uninstall.sh)
#   make test                 run the test suite (pytest)
#   make lint                 byte-compile Python, check shell/Lua/XML/desktop files, qmllint
#   make dist                 source tarball in dist/
#   make clean                remove build artefacts
#
# On Arch Linux prefer the PKGBUILD ("makepkg -si"): it calls "make install" with
# DESTDIR="$pkgdir", so pacman tracks every file. A bare "sudo make install" writes into
# /usr behind pacman's back.

SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

PYTHON      ?= python3
PREFIX      ?= /usr
DESTDIR     ?=
DATAROOTDIR ?= $(PREFIX)/share
DATADIR     ?= $(DATAROOTDIR)/dropwheel
APPDIR      ?= $(DATAROOTDIR)/applications
ICONDIR     ?= $(DATAROOTDIR)/icons/hicolor/scalable/apps
DOCDIR      ?= $(DATAROOTDIR)/doc/dropwheel
LICENSEDIR  ?= $(DATAROOTDIR)/licenses/dropwheel
SERVICEMENUDIR ?= $(DATAROOTDIR)/kio/servicemenus
NEMOACTIONDIR  ?= $(DATAROOTDIR)/nemo/actions
# Install the Dolphin service menu and the Nemo action system-wide (1) or not (0).
FM_INTEGRATIONS ?= 1
# Where "make wheel" puts the wheel and where "make install" looks for it.
WHEELDIR    ?= build/wheel
# Extra arguments: make test PYTEST_ARGS="-k image"; make install-user INSTALL_ARGS="--yes";
# make uninstall-user UNINSTALL_ARGS="--purge"
PYTEST_ARGS ?=
INSTALL_ARGS ?=
UNINSTALL_ARGS ?=
# 1 = qmllint findings fail "make lint" (default: reported as warnings only).
STRICT_QML  ?= 0

# DESTDIR made absolute, so recipes keep working if it was given as a relative path.
DEST := $(if $(DESTDIR),$(abspath $(DESTDIR)),)

VERSION := $(shell sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' pyproject.toml 2>/dev/null | head -n 1)
VERSION := $(or $(VERSION),0.0.0)

QMLLINT  ?= $(firstword $(shell command -v qmllint6 qmllint 2>/dev/null) $(wildcard /usr/lib/qt6/bin/qmllint))
LUAC     ?= $(firstword $(shell command -v luac5.4 luac luac5.3 2>/dev/null))

.PHONY: help wheel install install-python install-data uninstall install-user uninstall-user \
        test lint dist clean

help:
	@sed -n '3,12p' Makefile | sed 's/^# \{0,1\}//'
	@echo
	@echo "Variables: PREFIX=$(PREFIX) DESTDIR=$(DESTDIR) PYTHON=$(PYTHON) VERSION=$(VERSION)"

# ---------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------

wheel:
	rm -rf "$(WHEELDIR)"
	$(PYTHON) -m build --wheel --no-isolation --outdir "$(WHEELDIR)" .

# ---------------------------------------------------------------------------------------
# System install (PREFIX / DESTDIR)
# ---------------------------------------------------------------------------------------

install: install-python install-data

# Installs the wheel found in $(WHEELDIR); builds it first when there is none, so that
# "make install" works on its own while the PKGBUILD can build in build() and install in
# package() without rebuilding.
install-python:
	@shopt -s nullglob; wheels=("$(WHEELDIR)"/dropwheel-*.whl); \
	if (( $${#wheels[@]} == 0 )); then \
		$(MAKE) --no-print-directory wheel; \
		wheels=("$(WHEELDIR)"/dropwheel-*.whl); \
	fi; \
	if (( $${#wheels[@]} != 1 )); then \
		echo "error: expected exactly one dropwheel wheel in $(WHEELDIR), found $${#wheels[@]}" >&2; \
		exit 1; \
	fi; \
	echo "==> Installing $${wheels[0]}"; \
	$(PYTHON) -m installer $(if $(DEST),--destdir="$(DEST)",) --prefix="$(PREFIX)" "$${wheels[0]}"

install-data:
	@for d in shell integrations assets; do \
		[[ -d $$d ]] || { echo "error: $$d/ is missing; run make from the repository root" >&2; exit 1; }; \
	done
	@echo "==> Installing the Quickshell shell and integrations into $(DEST)$(DATADIR)"
	rm -rf "$(DEST)$(DATADIR)/shell" "$(DEST)$(DATADIR)/integrations"
	find shell integrations \( -name __pycache__ -o -name '.git*' \) -prune -o -type f \
		! -name '*.pyc' ! -name '.qmlls.ini' ! -name '*.qmlc' ! -name '*~' -print0 \
		| while IFS= read -r -d '' f; do install -Dm0644 "$$f" "$(DEST)$(DATADIR)/$$f"; done
	chmod 0755 "$(DEST)$(DATADIR)/integrations/nautilus/Dropwheel"
	@echo "==> Installing the desktop entry and the icon"
	install -Dm0644 integrations/desktop/dropwheel.desktop "$(DEST)$(APPDIR)/dropwheel.desktop"
	install -Dm0644 assets/dropwheel.svg "$(DEST)$(ICONDIR)/dropwheel.svg"
	@if [[ "$(FM_INTEGRATIONS)" == 1 ]]; then \
		echo "==> Installing the Dolphin service menu and the Nemo action"; \
		install -Dm0644 integrations/dolphin/dropwheel.desktop "$(DEST)$(SERVICEMENUDIR)/dropwheel.desktop"; \
		install -Dm0644 integrations/nemo/dropwheel.nemo_action "$(DEST)$(NEMOACTIONDIR)/dropwheel.nemo_action"; \
	fi
	@echo "==> Installing documentation and licence"
	install -Dm0644 README.md "$(DEST)$(DOCDIR)/README.md"
	install -Dm0644 assets/dropwheel.svg "$(DEST)$(DOCDIR)/assets/dropwheel.svg"
	for f in docs/*.md; do install -Dm0644 "$$f" "$(DEST)$(DOCDIR)/$$f"; done
	install -Dm0644 LICENSE "$(DEST)$(LICENSEDIR)/LICENSE"

uninstall:
	@paths=$$($(PYTHON) -c 'import sys, sysconfig; v = {"base": sys.argv[1], "platbase": sys.argv[1]}; print(sysconfig.get_path("purelib", vars=v)); print(sysconfig.get_path("scripts", vars=v))' "$(PREFIX)"); \
	purelib=$$(sed -n 1p <<< "$$paths"); scripts=$$(sed -n 2p <<< "$$paths"); \
	echo "==> Removing the Python package from $(DEST)$$purelib"; \
	rm -rf "$(DEST)$$purelib/dropwheel" "$(DEST)$$purelib"/dropwheel-*.dist-info; \
	rm -f "$(DEST)$$scripts/dropwheel"
	rm -rf "$(DEST)$(DATADIR)/shell" "$(DEST)$(DATADIR)/integrations" "$(DEST)$(DOCDIR)" "$(DEST)$(LICENSEDIR)"
	if [[ -d "$(DEST)$(DATADIR)" ]]; then rmdir -- "$(DEST)$(DATADIR)" 2>/dev/null || true; fi
	rm -f "$(DEST)$(APPDIR)/dropwheel.desktop" "$(DEST)$(ICONDIR)/dropwheel.svg"
	rm -f "$(DEST)$(SERVICEMENUDIR)/dropwheel.desktop" "$(DEST)$(NEMOACTIONDIR)/dropwheel.nemo_action"

# ---------------------------------------------------------------------------------------
# User-local install (~/.local, no root)
# ---------------------------------------------------------------------------------------

install-user:
	bash ./install.sh $(INSTALL_ARGS)

uninstall-user:
	bash ./uninstall.sh $(UNINSTALL_ARGS)

# ---------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------

# Without the session variables the tests cannot reach the desktop they run on: otherwise
# wl-copy would replace the clipboard with test data ("makepkg -si" runs this target too).
test:
	env -u WAYLAND_DISPLAY -u DISPLAY -u HYPRLAND_INSTANCE_SIGNATURE \
		PYTHONPATH="$(CURDIR)/src$${PYTHONPATH:+:$$PYTHONPATH}" $(PYTHON) -m pytest -q $(PYTEST_ARGS)

lint:
	@echo "==> Python: byte-compiling src/ and tests/"
	$(PYTHON) -m compileall -q src $(wildcard tests)
	@echo "==> Shell scripts: bash -n"
	bash -n install.sh
	bash -n uninstall.sh
	bash -n PKGBUILD
	bash -n dropwheel.install
	bash -n integrations/nautilus/Dropwheel
	@echo "==> Lua"
	@if [[ -n "$(LUAC)" ]]; then \
		"$(LUAC)" -p integrations/hyprland/dropwheel.lua && echo "integrations/hyprland/dropwheel.lua: ok"; \
	else \
		echo "luac not found: Lua syntax check skipped (pacman -S lua)"; \
	fi
	@echo "==> XML (Thunar action, icon)"
	$(PYTHON) -c 'import sys, xml.dom.minidom as m; [m.parse(p) for p in sys.argv[1:]]' \
		integrations/thunar/uca-action.xml assets/dropwheel.svg
	@echo "==> Desktop entries"
	@if command -v desktop-file-validate >/dev/null 2>&1; then \
		desktop-file-validate integrations/desktop/dropwheel.desktop; \
	else \
		echo "desktop-file-validate not found: skipped (pacman -S desktop-file-utils)"; \
	fi
	@echo "==> QML"
	@if [[ -z "$(QMLLINT)" ]]; then \
		echo "qmllint not found: QML check skipped (it ships with qt6-declarative)"; \
	elif [[ -d shell ]]; then \
		mapfile -d '' qml < <(find shell -name '*.qml' -print0); \
		if (( $${#qml[@]} == 0 )); then echo "no QML files"; \
		elif "$(QMLLINT)" -I shell "$${qml[@]}"; then echo "qmllint: ok"; \
		elif [[ "$(STRICT_QML)" == 1 ]]; then exit 1; \
		else echo "qmllint reported findings (warnings only; STRICT_QML=1 makes them fatal)"; fi; \
	fi

# ---------------------------------------------------------------------------------------
# Release helpers
# ---------------------------------------------------------------------------------------

dist:
	@mkdir -p dist
	@out="dist/dropwheel-$(VERSION).tar.gz"; \
	if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git archive --format=tar.gz --prefix="dropwheel-$(VERSION)/" -o "$$out" HEAD; \
	else \
		tar --exclude-vcs --exclude='./dist' --exclude='./build' --exclude='./.makepkg' \
			--exclude='./pkg' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' \
			--exclude='.pytest_cache' --exclude='*.pkg.tar*' \
			--transform 's|^\.|dropwheel-$(VERSION)|' -czf "$$out" .; \
	fi; \
	sha256sum "$$out" > "$$out.sha256"; \
	echo "==> $$out"; cat "$$out.sha256"

clean:
	rm -rf build dist .makepkg pkg .pytest_cache
	rm -rf src/*.egg-info
	rm -f -- *.pkg.tar *.pkg.tar.*
	find . -path ./.git -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
