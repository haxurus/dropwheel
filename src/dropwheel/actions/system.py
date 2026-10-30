"""System files: AppImages, disk images, packages and unknown binaries.

* **appimage** - Integrate★ (desktop entry + icon in ``~/.local/share``, ``Exec`` pointing to the
  AppImage, optionally copied to ``~/Applications``) / Remove integration, Make executable & run,
  Extract contents, Info.  The AppImage is never executed to inspect it: its SquashFS payload is
  read with ``unsquashfs -o <ELF size>`` (or 7-Zip on a copy of the payload).
* **disk** (iso img raw qcow2 vhd…) - Mount read-only★ (``udisksctl loop-setup -r``) / Unmount,
  Verify checksum (SHA256SUMS next to it, or the clipboard), Extract (7-Zip / bsdtar), Info (pure
  Python header parsing, plus ``blkid`` when available).
* **package** (.pkg.tar.* .deb .rpm .flatpakref .apk .exe…) - Info★, Files, Install (opens a
  terminal with the right command, e.g. ``sudo pacman -U``: Dropwheel never runs sudo itself),
  Extract.
* **unknown** - Identify★ (``file``, MIME, entropy, magic bytes), Hex preview (first 4 KB),
  Strings → TXT; the File page adds checksums, Open with…, ZIP.
"""

from __future__ import annotations

import collections
import configparser
import json
import math
import os
import re
import secrets
import shlex
import shutil
import stat
import struct
import tarfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .. import i18n, tools, util
from ..detect import FileInfo, classify
from ..errors import MissingTool, ToolError, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout
from . import archive
from .common import (
    TERMINALS, Progress, TreeNode, copy_entry, info_row, persistent_paths, render_tree, size_text, verify_file,
)

__all__ = ["elf_payload_offset", "appimage_type", "rewrite_desktop", "desktop_quote", "disk_format",
           "iso_info", "ar_members", "parse_kv_output", "hexdump", "shannon_entropy", "iter_strings",
           "install_command"]


# ================================================================================ helpers

def _orig_path(ctx: Context, f: FileInfo | None = None) -> Path:
    return Path(ctx.original(f or ctx.file).path)


def _files_ok(files: list[FileInfo]) -> bool:
    return bool(files) and all(f.exists and not f.is_dir and f.path for f in files)


def _read_at(path: str | os.PathLike, offset: int, size: int) -> bytes:
    try:
        with open(path, "rb") as fh:
            if offset < 0:
                fh.seek(0, os.SEEK_END)
                end = fh.tell()
                fh.seek(max(0, end + offset))
            else:
                fh.seek(offset)
            return fh.read(size)
    except OSError:
        return b""


# ================================================================================ AppImage

_MACHINES = {0x03: "x86 (32 bit)", 0x3E: "x86-64", 0x28: "ARM (32 bit)", 0xB7: "AArch64", 0xF3: "RISC-V",
             0x08: "MIPS", 0x14: "PowerPC", 0x15: "PowerPC 64"}


def elf_payload_offset(path: str | os.PathLike) -> int | None:
    """Offset of the data appended after the ELF runtime (= end of the section header table).

    Returns ``None`` when ``path`` is not an ELF file.  When the computed offset does not point at
    a SquashFS superblock the first ``hsqs`` in the first 8 MiB is used instead.
    """
    head = _read_at(path, 0, 64)
    if len(head) < 52 or head[:4] != b"\x7fELF":
        return None
    order = "<" if head[5] == 1 else ">"
    if head[4] == 2:
        shoff = struct.unpack_from(order + "Q", head, 0x28)[0]
        shentsize, shnum = struct.unpack_from(order + "HH", head, 0x3A)
    else:
        shoff = struct.unpack_from(order + "I", head, 0x20)[0]
        shentsize, shnum = struct.unpack_from(order + "HH", head, 0x2E)
    offset = shoff + shentsize * shnum
    if _read_at(path, offset, 4) == b"hsqs":
        return offset
    window = _read_at(path, 0, 8 * 1024 * 1024)
    found = window.find(b"hsqs", 4096)
    return found if found > 0 else offset


def appimage_type(path: str | os.PathLike) -> int | None:
    """AppImage type (1 = ISO 9660 payload, 2 = SquashFS) from the ``AI\\x01``/``AI\\x02`` marker."""
    head = _read_at(path, 0, 16)
    if head[:4] != b"\x7fELF":
        return None
    if head[8:10] == b"AI" and head[10:11] in (b"\x01", b"\x02"):
        return head[10]
    return None


def _elf_machine(path: str | os.PathLike) -> str:
    head = _read_at(path, 0, 20)
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return ""
    order = "<" if head[5] == 1 else ">"
    machine = struct.unpack_from(order + "H", head, 0x12)[0]
    return _MACHINES.get(machine, f"0x{machine:x}")


def desktop_quote(path: str) -> str:
    """Quote a path for a desktop entry ``Exec`` key (Desktop Entry Spec, both escaping levels)."""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", path):
        return path
    inner = re.sub(r'([\\"`$])', r"\\\1", path)
    return '"' + inner.replace("\\", "\\\\") + '"'


_EXEC_HEAD = re.compile(r'^\s*("(?:[^"\\]|\\.)*"|\S+)(.*)$')


def rewrite_desktop(text: str, exec_path: str, icon: str = "", appimage: str = "") -> str:
    """Point every ``Exec`` at ``exec_path`` (arguments kept), drop ``TryExec``, set ``Icon``."""
    out: list[str] = []
    in_main = False
    added = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_main and not added:
                at = len(out)
                while at > 0 and not out[at - 1].strip():
                    at -= 1
                out.insert(at, f"X-AppImage-Path={appimage or exec_path}")
                added = True
            in_main = s == "[Desktop Entry]"
            out.append(line)
            continue
        key, sep, value = line.partition("=")
        k = key.strip()
        if sep and k == "Exec":
            m = _EXEC_HEAD.match(value)
            rest = m.group(2) if m else ""
            out.append(f"Exec={desktop_quote(exec_path)}{rest}")
            continue
        if sep and (k == "TryExec" or k.startswith("X-AppImage-Path")):
            continue
        if sep and k == "Icon" and icon:
            out.append(f"Icon={icon}")
            continue
        out.append(line)
    if in_main and not added:
        out.append(f"X-AppImage-Path={appimage or exec_path}")
    return "\n".join(out).rstrip("\n") + "\n"


def _desktop_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    in_main = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_main = s == "[Desktop Entry]"
            continue
        if not in_main or s.startswith("#"):
            continue
        key, sep, value = s.partition("=")
        if sep and "[" not in key:
            values.setdefault(key.strip(), value.strip())
    return values


def _apps_dir() -> Path:
    return util.xdg_data_home() / "applications"


def _integrated_entry(appimage: str) -> Path | None:
    """The desktop entry Dropwheel created for ``appimage`` (``X-AppImage-Path``), if any."""
    real = os.path.realpath(appimage)
    try:
        entries = sorted(_apps_dir().glob("dropwheel-*.desktop"))
    except OSError:
        return None
    for entry in entries:
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("X-AppImage-Path="):
                target = line.split("=", 1)[1].strip()
                if os.path.realpath(target) == real or target == appimage:
                    return entry
    return None


def _squash_list(ctx: Context, path: str, offset: int) -> list[str]:
    res = ctx.run(["unsquashfs", "-o", str(offset), "-l", "-d", "R", path], timeout=120)
    out = []
    for line in (res.stdout or "").splitlines():
        line = line.rstrip("\n")
        if line == "R" or not line.startswith("R/"):
            continue
        out.append(line[2:])
    return out


def _squash_extract(ctx: Context, path: str, offset: int, dest: Path, members: list[str] | None = None,
                    prog: Progress | None = None) -> None:
    argv = ["unsquashfs", "-o", str(offset), "-f", "-no-xattrs", "-d", str(dest)]
    if members is None:
        argv.insert(1, "-percentage")
    else:
        argv.insert(1, "-no-progress")
    argv.append(path)
    if members:
        argv += members

    def on_line(line: str) -> None:
        if prog is not None:
            m = re.findall(r"(\d{1,3})%?\s*$", line.strip())
            if m:
                prog.frac(int(m[-1]) / 100.0)

    try:
        ctx.run(argv, on_line=on_line if members is None else None, timeout=None if members is None else 120,
                ok_codes=(0, 2))
    except ToolError as exc:
        raise UserError("err.appimage.extract_failed", details=exc.details) from None


def _payload_copy(ctx: Context, path: str, offset: int, prog: Progress | None = None) -> Path:
    target = ctx.tmpdir() / f"payload-{secrets.token_hex(3)}.squashfs"
    total = max(0, os.path.getsize(path) - offset)
    with open(path, "rb") as src, open(target, "wb") as dst:
        src.seek(offset)
        done = 0
        while True:
            ctx.check_cancel()
            chunk = src.read(4 * 1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            if prog is not None and total:
                prog.frac(done / total)
    return target


def _extract_7z(ctx: Context, path: str, offset: int, dest: Path, prog: Progress | None = None) -> None:
    payload = _payload_copy(ctx, path, offset, prog.sub(0.0, 0.3) if prog else None)
    try:
        archive.run_7z(ctx, ["7z", "x", "-y", "-bsp1", "-bso0", "-bse2", "-spd", "-o.", "--", os.path.abspath(payload)],
                       prog=prog.sub(0.3, 1.0) if prog else None, ok_codes=(0, 1), cwd=dest)
    except ToolError as exc:
        raise UserError("err.appimage.extract_failed", details=exc.details) from None
    finally:
        try:
            payload.unlink()
        except OSError:
            pass


def _need_payload(path: str) -> int:
    if appimage_type(path) == 1:
        raise UserError("err.appimage.type1")
    offset = elf_payload_offset(path)
    if offset is None or _read_at(path, offset, 4) != b"hsqs":
        raise UserError("err.appimage.no_squashfs")
    return offset


def _resolve_inside(rel_dir: str, link: str) -> str | None:
    if link.startswith("/"):
        joined = os.path.normpath(link.lstrip("/"))
    else:
        joined = os.path.normpath(os.path.join(rel_dir, link))
    if joined.startswith("..") or os.path.isabs(joined):
        return None
    return joined.replace(os.sep, "/")


def _extract_meta(ctx: Context, path: str) -> Path:
    """Extract the desktop entry, ``.DirIcon`` and icons of an AppImage into a temp folder."""
    offset = _need_payload(path)
    dest = ctx.tmpdir() / f"appimage-{secrets.token_hex(3)}"
    if tools.have("unsquashfs"):
        listing = _squash_list(ctx, path, offset)
        top_desktop = [p for p in listing if "/" not in p and p.endswith(".desktop")]
        wanted = set(top_desktop) | {p for p in listing if p == ".DirIcon"}
        wanted |= {p for p in listing if "/" not in p and p.lower().endswith((".png", ".svg", ".svgz", ".xpm"))}
        _squash_extract(ctx, path, offset, dest, sorted(wanted) or None)
        icon_name = ""
        for d in top_desktop:
            target = dest / d
            for _ in range(5):
                if target.is_symlink():
                    resolved = _resolve_inside(os.path.dirname(d), os.readlink(target))
                    if resolved is None:
                        break
                    _squash_extract(ctx, path, offset, dest, [resolved])
                    target = dest / resolved
                else:
                    break
            try:
                icon_name = _desktop_values(target.read_text(encoding="utf-8", errors="replace")).get("Icon", "")
            except OSError:
                pass
            if icon_name:
                break
        if icon_name and not icon_name.startswith("/"):
            more = [p for p in listing
                    if re.search(r"(^|/)(icons/hicolor/[^/]+/apps|pixmaps)/" + re.escape(icon_name) + r"\.(png|svgz?|xpm)$", p)]
            more += [p for p in listing if p.rsplit("/", 1)[-1] in (f"{icon_name}.png", f"{icon_name}.svg")
                     and "/" not in p]
            if more:
                _squash_extract(ctx, path, offset, dest, sorted(set(more)))
        # resolve symlinked top-level entries (.DirIcon, icons)
        for entry in list(dest.iterdir()) if dest.exists() else []:
            for _ in range(5):
                if not entry.is_symlink():
                    break
                resolved = _resolve_inside("", os.readlink(entry))
                if resolved is None or (dest / resolved).exists():
                    break
                _squash_extract(ctx, path, offset, dest, [resolved])
        return dest
    if tools.have("7z"):
        dest.mkdir(parents=True, exist_ok=True)
        _extract_7z(ctx, path, offset, dest)
        return dest
    raise MissingTool("unsquashfs")


def _find_desktop(root: Path) -> Path | None:
    cands = sorted(p for p in root.glob("*.desktop"))
    for cand in cands:
        real = Path(os.path.realpath(cand))
        if real.is_file() and str(real).startswith(str(root.resolve())):
            return real
    hits = sorted(root.glob("usr/share/applications/*.desktop"))
    return hits[0] if hits else None


def _find_icon(root: Path, icon_name: str) -> Path | None:
    root_real = str(root.resolve())

    def ok(p: Path) -> Path | None:
        try:
            real = Path(os.path.realpath(p))
        except OSError:
            return None
        if real.is_file() and str(real).startswith(root_real) and real.stat().st_size > 0:
            return real
        return None

    if icon_name and not icon_name.startswith("/"):
        best: tuple[int, Path] | None = None
        for p in root.rglob(f"{icon_name}.*"):
            if p.suffix.lower() not in (".png", ".svg", ".svgz", ".xpm"):
                continue
            real = ok(p)
            if real is None:
                continue
            score = 100000 if p.suffix.lower() in (".svg", ".svgz") else 0
            m = re.search(r"/(\d+)x\d+/", str(p))
            score += int(m.group(1)) if m else 64
            if best is None or score > best[0]:
                best = (score, real)
        if best is not None:
            return best[1]
    for cand in (root / ".DirIcon",):
        real = ok(cand)
        if real is not None:
            return real
    return None


def _icon_ext(path: Path) -> str:
    head = _read_at(path, 0, 512)
    if head.startswith(b"\x89PNG"):
        return "png"
    if b"<svg" in head or head.lstrip().startswith(b"<?xml"):
        return "svg"
    if head.startswith(b"\x1f\x8b"):
        return "svgz"
    if head.startswith(b"/* XPM"):
        return "xpm"
    return path.suffix.lstrip(".").lower() or "png"


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.").lower()
    return slug[:60] or "appimage"


def _is_appimage(files: list[FileInfo]) -> bool:
    return _files_ok(files) and all(f.category == "appimage" for f in files)


def _not_integrated(files: list[FileInfo]) -> bool:
    return _is_appimage(files) and all(_integrated_entry(f.path) is None for f in files)


def _integrated(files: list[FileInfo]) -> bool:
    return _is_appimage(files) and all(_integrated_entry(f.path) is not None for f in files)


def _make_executable(path: Path) -> bool:
    """``chmod +x`` (user, plus group/other where readable); ``True`` when the mode changed."""
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError as exc:
        raise UserError("err.not_readable", details=str(exc), name=path.name) from None
    new = mode | stat.S_IXUSR | ((mode & 0o044) >> 2)
    if new == mode:
        return False
    try:
        os.chmod(path, new)
    except OSError as exc:
        raise UserError("err.permission", details=str(exc)) from None
    return True


@action(id="appimage.integrate", label="act.appimage.integrate", icon="install_desktop", categories={"appimage"},
        multi="each", mode="quick", requires=[("unsquashfs", "7z")], when=_not_integrated,
        description="desc.appimage.integrate", order=1,
        params=[P.bool("copy", "prm.appimage.copy", False, help="prm.appimage.copy.help")])
def appimage_integrate(ctx: Context):
    """Add the AppImage to the application menu (desktop entry + icon), without running it."""
    f = ctx.file
    orig = ctx.original(f)
    prog = Progress(ctx, label=ctx.t("act.appimage.integrate"))
    prog.frac(None, force=True)
    meta = _extract_meta(ctx, f.path)
    desktop = _find_desktop(meta)
    if desktop is None:
        raise UserError("err.appimage.no_desktop")
    text = desktop.read_text(encoding="utf-8", errors="replace")
    values = _desktop_values(text)
    name = values.get("Name") or orig.stem
    slug = _slug(desktop.stem if desktop.stem else name)
    outputs: list[Path] = []
    exec_path = Path(orig.path)
    if ctx.pbool("copy") or orig.volatile or f.path != orig.path:
        apps_home = Path.home() / "Applications"
        target = ctx.output_path(f, name=orig.name, directory=apps_home)
        with atomic_target(target) as part:
            copy_entry(ctx, f.path, part, Progress(ctx, 0.2, 0.9, total=f.size or None))
        os.chmod(target, 0o755)
        outputs.append(target)
        exec_path = target
    elif _make_executable(exec_path):
        ctx.warn(ctx.t("warn.appimage.made_executable"))
    icon_value = ""
    icon = _find_icon(meta, values.get("Icon", ""))
    if icon is not None:
        icon_dir = util.xdg_data_home() / "icons" / "dropwheel-appimages"
        icon_out = ctx.output_path(f, name=f"{slug}.{_icon_ext(icon)}", directory=icon_dir)
        with atomic_target(icon_out) as part:
            shutil.copyfile(icon, part)
        outputs.append(icon_out)
        icon_value = str(icon_out)
    entry_text = rewrite_desktop(text, str(exec_path), icon_value, str(exec_path))
    entry_out = ctx.output_path(f, name=f"dropwheel-{slug}.desktop", directory=_apps_dir())
    with atomic_target(entry_out) as part:
        part.write_text(entry_text, encoding="utf-8")
    os.chmod(entry_out, 0o644)
    outputs.insert(0, entry_out)
    if tools.have("update-desktop-database"):
        util.run_quiet(["update-desktop-database", str(_apps_dir())], timeout=30)
    return ctx.result(outputs, message=ctx.t("msg.appimage.integrated", name=name))


@action(id="appimage.unintegrate", label="act.appimage.unintegrate", icon="delete", categories={"appimage"},
        multi="each", when=_integrated, danger=True, undoable=False, description="desc.appimage.unintegrate", order=2)
def appimage_unintegrate(ctx: Context):
    """Remove the menu entry and icon created by Integrate (they go to the trash; the AppImage stays)."""
    entry = _integrated_entry(ctx.file.path)
    if entry is None:
        raise UserError("err.appimage.not_integrated")
    try:
        icon = _desktop_values(entry.read_text(encoding="utf-8", errors="replace")).get("Icon", "")
    except OSError:
        icon = ""
    util.trash(entry)
    icon_dir = util.xdg_data_home() / "icons" / "dropwheel-appimages"
    if icon and icon.startswith(str(icon_dir)) and os.path.isfile(icon):
        try:
            util.trash(icon)
        except UserError:
            pass
    if tools.have("update-desktop-database"):
        util.run_quiet(["update-desktop-database", str(_apps_dir())], timeout=30)
    return ctx.result(kind="none", message=ctx.t("msg.appimage.unintegrated"))


def _libfuse2() -> bool:
    for d in ("/usr/lib", "/usr/lib64", "/lib", "/lib64", "/usr/lib/x86_64-linux-gnu"):
        if os.path.exists(os.path.join(d, "libfuse.so.2")):
            return True
    return False


@action(id="appimage.run", label="act.appimage.run", icon="play_arrow", categories={"appimage"}, multi="single",
        when=_is_appimage, undoable=False, description="desc.appimage.run", order=3)
def appimage_run(ctx: Context):
    """Make the AppImage executable and start it (without FUSE it runs in extract-and-run mode)."""
    path = _orig_path(ctx)
    if not path.exists():
        path = Path(persistent_paths(ctx)[0])
    _make_executable(path)
    env: dict[str, str] = {}
    if not (tools.have("fusermount3") or tools.have("fusermount")) or not _libfuse2():
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    if not util.detach([str(path.absolute())], cwd=str(path.parent), env=env or None):
        raise UserError("err.open_failed", name=path.name)
    return ctx.result(kind="none", message=ctx.t("msg.appimage.started", name=path.name))


@action(id="appimage.extract", label="act.appimage.extract", icon="unarchive", categories={"appimage"},
        multi="each", requires=[("unsquashfs", "7z")], when=_is_appimage, description="desc.appimage.extract",
        order=4)
def appimage_extract(ctx: Context):
    """Extract the AppImage contents into ``"<name> (estratto)"`` (the AppImage is not executed)."""
    f = ctx.file
    offset = _need_payload(f.path)
    outdir = ctx.output_dir(f, suffix="sfx.extracted")
    prog = Progress(ctx, label=ctx.t("act.appimage.extract"))
    work = outdir / ".extracting"
    if tools.have("unsquashfs"):
        _squash_extract(ctx, f.path, offset, work, None, prog)
    else:
        work.mkdir()
        _extract_7z(ctx, f.path, offset, work, prog)
    stats = archive.ExtractStats()
    archive._sanitize_tree(work, stats)
    for child in list(work.iterdir()):
        os.rename(child, outdir / child.name)
    work.rmdir()
    if stats.links:
        ctx.warn(i18n.tn("warn.arc.links_skipped", stats.links, ctx.lang))
    return ctx.result([outdir], message=i18n.tn("msg.arc.extracted", stats.files, ctx.lang))


@action(id="appimage.info", label="act.appimage.info", icon="info", categories={"appimage"}, multi="each",
        undoable=False, when=_is_appimage, description="desc.appimage.info", order=5)
def appimage_info(ctx: Context):
    """Name, version, architecture, AppImage type and the embedded desktop entry."""
    f = ctx.file
    path = f.path
    g = "info.grp.appimage"
    rows = [info_row(ctx, g, "info.file.name", ctx.original(f).name),
            info_row(ctx, g, "info.file.size", size_text(f.size, ctx.lang))]
    kind = appimage_type(path)
    rows.append(info_row(ctx, g, "info.appimage.type", f"AppImage {kind}" if kind else "?"))
    machine = _elf_machine(path)
    if machine:
        rows.append(info_row(ctx, g, "info.appimage.arch", machine))
    executable = os.access(_orig_path(ctx), os.X_OK)
    rows.append(info_row(ctx, g, "info.appimage.executable", ctx.t("info.yes" if executable else "info.no")))
    entry = _integrated_entry(path)
    rows.append(info_row(ctx, g, "info.appimage.integrated", ctx.t("info.yes" if entry else "info.no")))
    if kind != 1 and (tools.have("unsquashfs") or tools.have("7z")):
        try:
            meta = _extract_meta(ctx, path)
            desktop = _find_desktop(meta)
        except UserError as exc:
            ctx.log(f"metadata: {exc}")
            desktop = None
        if desktop is not None:
            values = _desktop_values(desktop.read_text(encoding="utf-8", errors="replace"))
            for key, row_key in (("Name", "info.appimage.app_name"), ("Comment", "info.appimage.comment"),
                                 ("X-AppImage-Version", "info.font.version"),
                                 ("Categories", "info.appimage.categories"), ("Exec", "info.appimage.exec")):
                if values.get(key):
                    rows.append(info_row(ctx, "info.grp.desktop", row_key, values[key]))
    else:
        ctx.warn(ctx.t("warn.appimage.no_meta"))
    return ctx.result(kind="info", info=rows, message=ctx.original(f).name)


# ================================================================================ disk images

MOUNTABLE_EXTS = frozenset({"iso", "img", "ima", "raw", "udf", "squashfs", "sfs"})


def iso_info(path: str | os.PathLike) -> dict[str, Any]:
    """Primary volume descriptor of an ISO 9660 image: label, system, publisher, size, date, bootable."""
    out: dict[str, Any] = {}
    bootable = False
    for index in range(16, 64):
        desc = _read_at(path, index * 2048, 2048)
        if len(desc) < 2048 or desc[1:6] != b"CD001":
            break
        dtype = desc[0]
        if dtype == 0:
            bootable = True
        elif dtype == 1 and "label" not in out:
            def text(a: int, b: int) -> str:
                return desc[a:b].decode("ascii", "replace").strip()

            out["system"] = text(8, 40)
            out["label"] = text(40, 72)
            blocks = struct.unpack_from("<I", desc, 80)[0]
            block_size = struct.unpack_from("<H", desc, 128)[0] or 2048
            out["size"] = blocks * block_size
            out["publisher"] = text(318, 446)
            out["application"] = text(574, 702)
            date = text(813, 829)
            if date[:8].isdigit() and date[:4] != "0000":
                out["created"] = f"{date[0:4]}-{date[4:6]}-{date[6:8]} {date[8:10]}:{date[10:12]}"
        elif dtype == 255:
            break
    if out:
        out["bootable"] = bootable
    return out


def disk_format(path: str | os.PathLike) -> dict[str, Any]:
    """Container format and virtual size of a disk image (pure Python header parsing)."""
    head = _read_at(path, 0, 1024)
    tail = _read_at(path, -512, 512)
    info: dict[str, Any] = {"format": "raw"}
    if head[:4] == b"QFI\xfb":
        info["format"] = "qcow2" if struct.unpack_from(">I", head, 4)[0] >= 2 else "qcow"
        info["virtual_size"] = struct.unpack_from(">Q", head, 24)[0]
    elif head[:8] == b"vhdxfile":
        info["format"] = "vhdx"
    elif head[:4] == b"KDMV":
        info["format"] = "vmdk"
        info["virtual_size"] = struct.unpack_from("<Q", head, 12)[0] * 512
    elif b"Oracle VM VirtualBox Disk Image" in head[:64]:
        info["format"] = "vdi"
        if len(head) >= 0x178:
            info["virtual_size"] = struct.unpack_from("<Q", head, 0x170)[0]
    elif tail[:8] == b"conectix":
        info["format"] = "vhd"
        info["virtual_size"] = struct.unpack_from(">Q", tail, 48)[0]
    elif tail[:4] == b"koly":
        info["format"] = "dmg"
    elif head[:4] == b"hsqs":
        info["format"] = "squashfs"
    else:
        iso = iso_info(path)
        if iso:
            info["format"] = "iso9660"
            info.update({f"iso_{k}": v for k, v in iso.items()})
        else:
            marker = _read_at(path, 0x8001, 5)
            if marker in (b"BEA01", b"NSR02", b"NSR03"):
                info["format"] = "udf"
            elif _read_at(path, 512, 8) == b"EFI PART":
                info["partition_table"] = "GPT"
            elif head[510:512] == b"\x55\xaa":
                info["partition_table"] = "MBR"
    return info


def _blkid(path: str) -> dict[str, str]:
    if not tools.have("blkid"):
        return {}
    res = util.run_quiet(["blkid", "-p", "-o", "export", "--", path], timeout=10)
    if res is None or res.returncode not in (0, 2):
        return {}
    out = {}
    for line in (res.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _loop_devices_for(path: str) -> list[str]:
    """Loop devices (``/dev/loopN``) currently backed by ``path`` (from ``/sys/block``)."""
    real = os.path.realpath(path)
    found = []
    try:
        names = os.listdir("/sys/block")
    except OSError:
        return []
    for name in names:
        if not name.startswith("loop"):
            continue
        try:
            with open(f"/sys/block/{name}/loop/backing_file", encoding="utf-8", errors="replace") as fh:
                backing = fh.read().strip()
        except OSError:
            continue
        if backing.endswith(" (deleted)"):
            continue
        if os.path.realpath(backing) == real:
            found.append(f"/dev/{name}")
    return sorted(found)


def _mountable(files: list[FileInfo]) -> bool:
    if not _files_ok(files):
        return False
    for f in files:
        if f.category != "disk" or f.ext not in MOUNTABLE_EXTS or f.format in ("qcow2", "vhd", "vmdk"):
            return False
        if _loop_devices_for(f.path):
            return False
    return True


def _attached(files: list[FileInfo]) -> bool:
    return _files_ok(files) and all(f.category == "disk" and _loop_devices_for(f.path) for f in files)


def _lsblk(ctx: Context, device: str) -> list[dict]:
    if not tools.have("lsblk"):
        return [{"path": device, "fstype": "?", "mountpoint": None}]
    res = ctx.run(["lsblk", "-J", "-o", "PATH,FSTYPE,MOUNTPOINT,LABEL,TYPE", device], timeout=20)
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        return []
    out: list[dict] = []

    def walk(nodes: list) -> None:
        for node in nodes or []:
            mp = node.get("mountpoint")
            if mp is None and isinstance(node.get("mountpoints"), list):
                mps = [m for m in node["mountpoints"] if m]
                mp = mps[0] if mps else None
            out.append({"path": node.get("path"), "fstype": node.get("fstype"), "mountpoint": mp,
                        "label": node.get("label")})
            walk(node.get("children") or [])

    walk(data.get("blockdevices") or [])
    return out


def _udisks_error(exc: ToolError) -> UserError:
    text = (exc.details or "").lower()
    if "not authorized" in text or "notauthorized" in text:
        return UserError("err.disk.not_authorized", details=exc.details)
    return exc


@action(id="disk.mount", label="act.disk.mount", icon="album", categories={"disk"}, multi="each",
        requires=["udisksctl"], when=_mountable, undoable=False, description="desc.disk.mount", order=1)
def disk_mount(ctx: Context):
    """Attach the image read-only (``udisksctl loop-setup -r``) and mount its file systems."""
    path = str(_orig_path(ctx))
    if not os.path.exists(path):
        path = str(persistent_paths(ctx)[0])
    ctx.progress(None, ctx.t("act.disk.mount"))
    try:
        res = ctx.run(["udisksctl", "loop-setup", "-r", "-f", path, "--no-user-interaction"], timeout=60)
    except ToolError as exc:
        raise _udisks_error(exc) from None
    m = re.search(r"as (/dev/loop\d+)", res.stdout or "")
    if not m:
        raise UserError("err.disk.loop_failed", details=res.stdout)
    loop = m.group(1)
    mounts: list[Path] = []
    deadline = time.monotonic() + 4
    nodes: list[dict] = []
    while True:
        ctx.check_cancel()
        nodes = [n for n in _lsblk(ctx, loop) if n.get("fstype")]
        if nodes and (all(n.get("mountpoint") for n in nodes) or time.monotonic() > deadline):
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.4)
    if not nodes:
        util.run_quiet(["udisksctl", "loop-delete", "-b", loop, "--no-user-interaction"], timeout=20)
        raise UserError("err.disk.no_fs")
    for node in nodes:
        mp = node.get("mountpoint")
        if not mp:
            try:
                out = ctx.run(["udisksctl", "mount", "-b", node["path"], "--no-user-interaction"], timeout=60)
                hit = re.search(r" at (.+)$", (out.stdout or "").strip())
                mp = hit.group(1).strip() if hit else None
                if mp and not os.path.isdir(mp) and mp.endswith(".") and os.path.isdir(mp[:-1]):
                    mp = mp[:-1]
            except ToolError as exc:
                if "already mounted" in (exc.details or "").lower():
                    again = [n for n in _lsblk(ctx, node["path"]) if n.get("mountpoint")]
                    mp = again[0]["mountpoint"] if again else None
                else:
                    ctx.warn(ctx.t("warn.disk.mount_failed", device=node["path"]))
                    ctx.log(exc.details)
        if mp and os.path.isdir(mp):
            mounts.append(Path(mp))
    if not mounts:
        util.run_quiet(["udisksctl", "loop-delete", "-b", loop, "--no-user-interaction"], timeout=20)
        raise UserError("err.disk.mount_failed")
    return ctx.result(mounts, message=ctx.t("msg.disk.mounted", where=", ".join(str(m) for m in mounts)),
                      loop=loop)


@action(id="disk.unmount", label="act.disk.unmount", icon="eject", categories={"disk"}, multi="each",
        requires=["udisksctl"], when=_attached, undoable=False, description="desc.disk.unmount", order=2)
def disk_unmount(ctx: Context):
    """Unmount the image's file systems and detach its loop device."""
    loops = _loop_devices_for(ctx.file.path) or _loop_devices_for(str(_orig_path(ctx)))
    if not loops:
        raise UserError("err.disk.not_mounted")
    for loop in loops:
        for node in reversed(_lsblk(ctx, loop)):
            if node.get("mountpoint"):
                try:
                    ctx.run(["udisksctl", "unmount", "-b", node["path"], "--no-user-interaction"], timeout=60)
                except ToolError as exc:
                    if "busy" in (exc.details or "").lower():
                        raise UserError("err.disk.busy", details=exc.details) from None
                    raise _udisks_error(exc) from None
        try:
            ctx.run(["udisksctl", "loop-delete", "-b", loop, "--no-user-interaction"], timeout=60)
        except ToolError as exc:
            raise _udisks_error(exc) from None
    return ctx.result(kind="none", message=ctx.t("msg.disk.unmounted"))


@action(id="disk.verify", label="act.disk.verify", icon="verified", categories={"disk", "appimage", "package"},
        multi="each", mode="quick", undoable=False, when=_files_ok, description="desc.disk.verify", order=3,
        params=[P.textarea("expected", "prm.file.expected", default="", help="prm.file.expected.help")])
def disk_verify(ctx: Context):
    """Compare with ``SHA256SUMS`` / ``<name>.sha256`` next to the image (or a pasted hash)."""
    res = verify_file(ctx, ctx.file, ctx.pstr("expected"))
    g = "info.grp.checksum"
    rows = [info_row(ctx, g, "info.file.result", ctx.t("msg.file.checksum_ok")),
            info_row(ctx, g, "info.file.algorithm", res["algo"]),
            info_row(ctx, g, "info.file.expected", res["expected"]),
            info_row(ctx, g, "info.file.source", res["source"])]
    return ctx.result(kind="info", info=rows, message=ctx.t("msg.file.checksum_match", algo=res["algo"]))


def _extractable_disk(files: list[FileInfo]) -> bool:
    return _files_ok(files) and all(f.format not in ("qcow2", "vhdx", "vmdk", "vdi") for f in files)


@action(id="disk.extract", label="act.disk.extract", icon="unarchive", categories={"disk"}, multi="each",
        requires=[("7z", "bsdtar")], when=_extractable_disk, description="desc.disk.extract", order=4)
def disk_extract(ctx: Context):
    """Copy the files of the image into a folder (7-Zip reads ISO/UDF/IMG/WIM/DMG, bsdtar ISO 9660)."""
    outs, stats = archive.extract_archive(ctx, ctx.file, smart=False)
    if stats.links:
        ctx.warn(i18n.tn("warn.arc.links_skipped", stats.links, ctx.lang))
    return ctx.result(outs, message=i18n.tn("msg.arc.extracted", stats.files, ctx.lang))


@action(id="disk.info", label="act.disk.info", icon="info", categories={"disk"}, multi="each", undoable=False,
        when=_files_ok, description="desc.disk.info", order=5)
def disk_info(ctx: Context):
    """Format, virtual size, label, partition table and whether it is bootable."""
    f = ctx.file
    lang = ctx.lang
    g = "info.grp.disk"
    data = disk_format(f.path)
    rows = [info_row(ctx, g, "info.file.name", ctx.original(f).name),
            info_row(ctx, g, "info.file.size", size_text(f.size, lang)),
            info_row(ctx, g, "info.disk.format", data["format"].upper())]
    if data.get("virtual_size"):
        rows.append(info_row(ctx, g, "info.disk.virtual_size", util.human_size(data["virtual_size"], lang)))
    if data.get("partition_table"):
        rows.append(info_row(ctx, g, "info.disk.partitions", data["partition_table"]))
    for key, row_key in (("iso_label", "info.disk.label"), ("iso_system", "info.disk.system"),
                         ("iso_publisher", "info.disk.publisher"), ("iso_application", "info.disk.application"),
                         ("iso_created", "info.disk.created")):
        if data.get(key):
            rows.append(info_row(ctx, g, row_key, data[key]))
    if "iso_bootable" in data:
        rows.append(info_row(ctx, g, "info.disk.bootable", ctx.t("info.yes" if data["iso_bootable"] else "info.no")))
    blk = _blkid(f.path)
    for key, row_key in (("LABEL", "info.disk.label"), ("TYPE", "info.disk.fs"), ("UUID", "info.disk.uuid"),
                         ("PTTYPE", "info.disk.partitions")):
        if blk.get(key) and not any(r["key"] == ctx.t(row_key) for r in rows):
            rows.append(info_row(ctx, g, row_key, blk[key]))
    loops = _loop_devices_for(f.path)
    if loops:
        rows.append(info_row(ctx, g, "info.disk.attached", ", ".join(loops)))
    return ctx.result(kind="info", info=rows, message=ctx.original(f).name)


# ================================================================================ packages

def ar_members(path: str | os.PathLike) -> list[tuple[str, int, int]]:
    """Members of a Unix ``ar`` archive (``.deb``): ``[(name, offset, size)]``."""
    out: list[tuple[str, int, int]] = []
    with open(path, "rb") as fh:
        if fh.read(8) != b"!<arch>\n":
            raise UserError("err.pkg.damaged")
        while True:
            hdr = fh.read(60)
            if len(hdr) < 60:
                break
            name = hdr[:16].decode("ascii", "replace").strip()
            try:
                size = int(hdr[48:58].decode("ascii", "replace").strip())
            except ValueError:
                raise UserError("err.pkg.damaged") from None
            if name.endswith("/") and name != "/":
                name = name[:-1]
            offset = fh.tell()
            out.append((name, offset, size))
            fh.seek(size + (size & 1), os.SEEK_CUR)
    return out


def _ar_extract(ctx: Context, path: str, prefix: str) -> Path | None:
    """Copy the first ``ar`` member starting with ``prefix`` to a temp file (keeping its name)."""
    for name, offset, size in ar_members(path):
        if name.startswith(prefix):
            target = ctx.tmpdir() / f"{secrets.token_hex(3)}-{util.safe_filename(name)}"
            with open(path, "rb") as src, open(target, "wb") as dst:
                src.seek(offset)
                left = size
                while left > 0:
                    ctx.check_cancel()
                    chunk = src.read(min(left, 4 * 1024 * 1024))
                    if not chunk:
                        break
                    dst.write(chunk)
                    left -= len(chunk)
            return target
    return None


def _tar_member_text(ctx: Context, path: str | os.PathLike, wanted: set[str], limit: int = 1024 * 1024) -> str | None:
    """Text of the first member of a (compressed) tar whose normalised name is in ``wanted``."""
    kind = archive.detect_kind(path)
    if kind.kind != "tar":
        return None
    src = archive._Source(ctx, path, kind.codec)
    try:
        with tarfile.open(fileobj=src.stream, mode="r|") as tf:
            for member in tf:
                ctx.check_cancel()
                name = member.name[2:] if member.name.startswith("./") else member.name
                if name in wanted and member.isreg():
                    fh = tf.extractfile(member)
                    if fh is None:
                        return None
                    return fh.read(limit).decode("utf-8", "replace")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise UserError("err.pkg.damaged", details=str(exc)) from None
    finally:
        src.close()
    return None


def parse_kv_output(text: str) -> list[tuple[str, str]]:
    """``Key : value`` / ``Key: value`` lines (pacman -Qip, rpm -qi, Debian control) with continuations."""
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = re.match(r"^([A-Za-z][\w .()/-]*?)\s*:\s?(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            rows.append((m.group(1).strip(), m.group(2).strip()))
        elif rows:
            key, value = rows[-1]
            extra = raw.strip()
            if extra == ".":
                extra = ""
            rows[-1] = (key, f"{value} {extra}".strip())
    return rows


def _pkginfo_rows(text: str) -> list[tuple[str, str]]:
    labels = {"pkgname": "Name", "pkgver": "Version", "pkgdesc": "Description", "url": "URL", "arch": "Architecture",
              "license": "Licenses", "size": "Installed size", "packager": "Packager", "builddate": "Build date",
              "depend": "Depends on", "optdepend": "Optional deps", "provides": "Provides", "conflict": "Conflicts",
              "replaces": "Replaces"}
    merged: dict[str, list[str]] = collections.OrderedDict()
    for line in text.splitlines():
        if line.startswith("#") or " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        key = key.strip()
        if key not in labels:
            continue
        if key == "size":
            try:
                value = util.human_size(int(value))
            except ValueError:
                pass
        if key == "builddate":
            try:
                value = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))
            except (ValueError, OverflowError, OSError):
                pass
        merged.setdefault(labels[key], []).append(value.strip())
    return [(k, ", ".join(v)) for k, v in merged.items()]


def _pkg_kind(f: FileInfo) -> str:
    ext = f.ext
    if ext.startswith("pkg.tar"):
        return "alpm"
    return ext


def _package_rows(ctx: Context, f: FileInfo) -> list[tuple[str, str]]:
    kind = _pkg_kind(f)
    path = f.path
    if kind == "alpm":
        if tools.have("pacman"):
            try:
                res = ctx.run(["pacman", "-Qip", "--", path], timeout=60, env={"LC_ALL": "C"})
                rows = parse_kv_output(res.stdout or "")
                if rows:
                    return rows
            except ToolError as exc:
                ctx.log(exc.details)
        text = _tar_member_text(ctx, path, {".PKGINFO"})
        if text is None:
            raise UserError("err.pkg.damaged")
        return _pkginfo_rows(text)
    if kind == "deb":
        control = _ar_extract(ctx, path, "control.tar")
        if control is None:
            raise UserError("err.pkg.damaged")
        text = _tar_member_text(ctx, control, {"control"})
        if text is None:
            raise UserError("err.pkg.damaged")
        return parse_kv_output(text)
    if kind == "rpm":
        if not tools.have("rpm"):
            raise MissingTool("rpm")
        res = ctx.run(["rpm", "-qip", "--", path], timeout=60, env={"LC_ALL": "C"})
        return parse_kv_output(res.stdout or "")
    if kind in ("flatpakref", "flatpakrepo"):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise UserError("err.pkg.damaged", details=str(exc)) from None
        rows: list[tuple[str, str]] = []
        for section in parser.sections():
            for key, value in parser.items(section):
                if key.lower() in ("gpgkey",):
                    continue
                rows.append((key, value))
        return rows
    rows = []
    desc = _file_description(path)
    if desc:
        rows.append(("Type", desc))
    return rows


def _file_description(path: str) -> str:
    if not tools.have("file"):
        return ""
    res = util.run_quiet(["file", "-b", "--", path], timeout=10)
    return (res.stdout or "").strip() if res is not None and res.returncode == 0 else ""


def _is_package(files: list[FileInfo]) -> bool:
    return _files_ok(files) and all(f.category == "package" for f in files)


@action(id="package.info", label="act.pkg.info", icon="info", categories={"package"}, multi="each",
        undoable=False, when=_is_package, description="desc.pkg.info", order=1)
def package_info(ctx: Context):
    """Name, version, description, dependencies… (pacman -Qip, the Debian control file, rpm -qi)."""
    f = ctx.file
    ctx.progress(None, ctx.t("act.pkg.info"))
    rows = [info_row(ctx, "info.grp.package", "info.file.name", ctx.original(f).name),
            info_row(ctx, "info.grp.package", "info.file.size", util.human_size(f.size, ctx.lang))]
    for key, value in _package_rows(ctx, f):
        if value:
            rows.append({"group": ctx.t("info.grp.package"), "key": key, "value": value[:2000]})
    return ctx.result(kind="info", info=rows, message=ctx.original(f).name)


def _package_listing(ctx: Context, f: FileInfo) -> list[tuple[str, int, bool]]:
    kind = _pkg_kind(f)
    entries: list[archive.Entry]
    if kind == "alpm":
        entries = archive.list_entries(ctx, f.path)
        meta = {".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL", ".CHANGELOG"}
        entries = [e for e in entries if (e.name[2:] if e.name.startswith("./") else e.name) not in meta]
    elif kind == "deb":
        data = _ar_extract(ctx, f.path, "data.tar")
        if data is None:
            raise UserError("err.pkg.damaged")
        entries = archive.list_entries(ctx, data)
    elif kind == "rpm":
        if tools.have("bsdtar"):
            entries = archive._list_bsdtar(ctx, f.path)
        elif tools.have("rpm"):
            res = ctx.run(["rpm", "-qlp", "--", f.path], timeout=60)
            entries = [archive.Entry(line.strip()) for line in (res.stdout or "").splitlines() if line.strip()]
        else:
            raise MissingTool("bsdtar")
    else:
        entries = archive.list_entries(ctx, f.path)
    return [(e.name, e.size, e.is_dir) for e in entries]


def _listable(files: list[FileInfo]) -> bool:
    return _is_package(files) and all(f.ext not in ("flatpakref", "flatpakrepo") for f in files)


@action(id="package.files", label="act.pkg.files", icon="list", categories={"package"}, multi="single",
        undoable=False, when=_listable, description="desc.pkg.files", order=2)
def package_files(ctx: Context):
    """The files the package would install, as a tree with sizes."""
    f = ctx.file
    ctx.progress(None, ctx.t("act.pkg.files"))
    root = TreeNode("")
    for name, size, is_dir in _package_listing(ctx, f):
        parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
        if parts:
            root.add_path(parts, None if is_dir else size, is_dir)
    files, folders = root.count()
    header = ctx.t("msg.arc.list_header", name=ctx.original(f).name, files=files, folders=folders,
                   size=util.human_size(root.total(), ctx.lang), packed=util.human_size(f.size, ctx.lang))
    lines = render_tree(root, ctx.lang, more_text=ctx.t("msg.arc.list_more"))
    return ctx.result(kind="text", text=header + "\n\n" + "\n".join(lines),
                      message=i18n.tn("msg.arc.list_summary", files, ctx.lang,
                                      size=util.human_size(root.total(), ctx.lang)))


@action(id="package.extract", label="act.pkg.extract", icon="unarchive", categories={"package"}, multi="each",
        when=_listable, description="desc.pkg.extract", order=4)
def package_extract(ctx: Context):
    """Unpack the package into ``"<name> (estratto)"`` (Debian: data + ``DEBIAN/`` control files)."""
    f = ctx.file
    kind = _pkg_kind(f)
    outdir = ctx.output_dir(f, suffix="sfx.extracted")
    prog = Progress(ctx, label=ctx.t("act.pkg.extract"))
    stats = archive.ExtractStats()
    if kind == "deb":
        data = _ar_extract(ctx, f.path, "data.tar")
        control = _ar_extract(ctx, f.path, "control.tar")
        if data is None:
            raise UserError("err.pkg.damaged")
        stats = archive.extract_raw(ctx, data, outdir, prog=prog.sub(0.0, 0.9))
        if control is not None:
            debian = outdir / "DEBIAN"
            debian.mkdir(exist_ok=True)
            archive.extract_raw(ctx, control, debian, prog=prog.sub(0.9, 1.0))
    elif kind == "rpm" and tools.have("bsdtar"):
        work = outdir
        try:
            ctx.run(["bsdtar", "-x", "-f", f.path, "-C", str(work), "--no-same-owner"], timeout=None)
        except ToolError as exc:
            raise UserError("err.pkg.damaged", details=exc.details) from None
        archive._sanitize_tree(work, stats)
    else:
        stats = archive.extract_raw(ctx, f.path, outdir, prog=prog)
    if stats.links:
        ctx.warn(i18n.tn("warn.arc.links_skipped", stats.links, ctx.lang))
    return ctx.result([outdir], message=i18n.tn("msg.arc.extracted", stats.files, ctx.lang))


def install_command(f: FileInfo, path: str) -> list[str] | None:
    """The command that installs package ``f`` (``None`` when this system cannot install it)."""
    kind = _pkg_kind(f)
    if kind == "alpm" and tools.have("pacman"):
        return ["sudo", "pacman", "-U", "--", path]
    if kind == "flatpakref" and tools.have("flatpak"):
        return ["flatpak", "install", "--user", "--from", path]
    if kind == "flatpak" and tools.have("flatpak"):
        return ["flatpak", "install", "--user", "--bundle", path]
    if kind == "flatpakrepo" and tools.have("flatpak"):
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", f.stem).strip("-") or "repo"
        return ["flatpak", "remote-add", "--user", "--if-not-exists", "--from", name, path]
    if kind == "deb":
        if tools.have("apt"):
            return ["sudo", "apt", "install", path]
        if tools.have("debtap"):
            return ["debtap", path]
    if kind == "rpm":
        for mgr in ("dnf", "zypper"):
            if tools.have(mgr):
                return ["sudo", mgr, "install", path]
    if kind == "snap" and tools.have("snap"):
        return ["sudo", "snap", "install", "--dangerous", path]
    if kind == "msi" and tools.have("wine"):
        return ["wine", "msiexec", "/i", path]
    if kind == "exe" and tools.have("wine"):
        return ["wine", path]
    return None


def _installable(files: list[FileInfo]) -> bool:
    return _is_package(files) and len(files) == 1 and install_command(files[0], files[0].path) is not None


@action(id="package.install", label="act.pkg.install", icon="install_desktop", categories={"package"},
        multi="single", requires=[TERMINALS], when=_installable, undoable=False, description="desc.pkg.install",
        order=3)
def package_install(ctx: Context):
    """Open a terminal with the install command (``sudo pacman -U …``): you type the password there."""
    f = ctx.file
    path = str(persistent_paths(ctx)[0])
    cmd = install_command(f, path)
    if cmd is None:
        raise UserError("err.pkg.no_installer")
    util.launch_terminal(os.path.dirname(path), command=cmd, preferred=ctx.config, hold=True,
                         hold_message=ctx.t("msg.pkg.press_enter"))
    return ctx.result(kind="none", message=ctx.t("msg.pkg.terminal", command=" ".join(shlex.quote(c) for c in cmd)))


# ================================================================================ unknown binaries

def shannon_entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte (0 … 8)."""
    if not data:
        return 0.0
    counts = collections.Counter(data)
    total = len(data)
    return -sum(c / total * math.log2(c / total) for c in counts.values())


def hexdump(data: bytes, start: int = 0) -> str:
    """``xxd``-style dump: offset, 16 bytes in hex (two groups of 8) and printable ASCII."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexes = " ".join(f"{b:02x}" for b in chunk[:8])
        if len(chunk) > 8:
            hexes += "  " + " ".join(f"{b:02x}" for b in chunk[8:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{start + i:08x}  {hexes:<49}  {text}")
    return "\n".join(lines)


def iter_strings(fh: BinaryIO, min_len: int = 4, utf16: bool = False, chunk: int = 4 * 1024 * 1024,
                 on_progress: Any = None) -> Iterator[tuple[int, str]]:
    """Printable ASCII runs (and UTF-16LE runs when ``utf16``) as ``(offset, text)``, like ``strings -a``.

    Reads ``fh`` in chunks; runs crossing a chunk boundary are reported once, whole (runs longer
    than a few chunks are split).  ``on_progress(bytes_read)`` is called after every chunk.
    """
    n = str(max(1, int(min_len))).encode()
    regexes = [(re.compile(rb"[\x20-\x7e\t]{" + n + rb",}"), "ascii")]
    if utf16:
        regexes.append((re.compile(rb"(?:[\x20-\x7e\t]\x00){" + n + rb",}"), "utf-16-le"))
    overlap = min(65536, max(16, chunk // 4))
    base = 0          # absolute offset of buf[0]
    buf = b""
    read = 0
    while True:
        data = fh.read(chunk)
        read += len(data)
        final = not data
        buf += data
        if not buf:
            break
        limit = len(buf)
        force = False
        if not final:
            limit = max(0, len(buf) - overlap)
            force = len(buf) - overlap > max(4 * chunk, 256 * 1024)
        # move the cut back to the start of any run crossing it (it may continue in the next chunk)
        cut = limit
        if not final and not force:
            changed = True
            while changed:
                changed = False
                for rx, _enc in regexes:
                    for m in rx.finditer(buf, max(0, cut - 4 * chunk - overlap), min(len(buf), cut + overlap)):
                        if m.start() < cut < m.end():
                            cut = m.start()
                            changed = True
                            break
        hits: list[tuple[int, str]] = []
        for rx, enc in regexes:
            for m in rx.finditer(buf, 0, len(buf)):
                if m.start() >= cut:
                    break
                end = min(m.end(), cut)
                if end - m.start() < (int(n) * (2 if enc != "ascii" else 1)):
                    continue
                hits.append((base + m.start(), buf[m.start():end].decode(enc, "replace")))
        hits.sort()
        yield from hits
        if on_progress is not None:
            on_progress(read)
        if final:
            break
        buf = buf[cut:]
        base += cut


def _is_unknown(files: list[FileInfo]) -> bool:
    return _files_ok(files)


@action(id="unknown.identify", label="act.unknown.identify", icon="search", categories={"unknown", "package"},
        multi="each", undoable=False, when=_is_unknown, description="desc.unknown.identify", order=1)
def unknown_identify(ctx: Context):
    """What the file really is: ``file`` description, MIME type, magic bytes and entropy."""
    f = ctx.file
    g = "info.grp.identify"
    rows = [info_row(ctx, g, "info.file.name", ctx.original(f).name),
            info_row(ctx, g, "info.file.size", size_text(f.size, ctx.lang))]
    desc = _file_description(f.path)
    if desc:
        rows.append(info_row(ctx, g, "info.unknown.description", desc))
    mime = ""
    if tools.have("file"):
        res = util.run_quiet(["file", "-b", "--mime", "--", f.path], timeout=10)
        mime = (res.stdout or "").strip() if res is not None and res.returncode == 0 else ""
    rows.append(info_row(ctx, g, "info.unknown.mime", mime or f.mime))
    detected = classify(f.path)
    if detected.category not in ("unknown",):
        rows.append(info_row(ctx, g, "info.unknown.looks_like",
                             f"{ctx.t('cat.' + detected.category)} ({detected.format or detected.ext or detected.mime})"))
    head = _read_at(f.path, 0, 16)
    rows.append(info_row(ctx, g, "info.unknown.magic", " ".join(f"{b:02x}" for b in head) or "—"))
    sample = _read_at(f.path, 0, 4 * 1024 * 1024)
    if sample:
        ent = shannon_entropy(sample)
        if ent > 7.5:
            verdict = ctx.t("info.unknown.ent_random")
        elif ent < 5.0:
            verdict = ctx.t("info.unknown.ent_low")
        else:
            verdict = ctx.t("info.unknown.ent_mixed")
        value = f"{ent:.2f}".replace(".", "," if ctx.lang == "it" else ".")
        rows.append(info_row(ctx, g, "info.unknown.entropy", f"{value} bit/byte · {verdict}"))
        printable = sum(1 for b in sample[:65536] if 32 <= b < 127 or b in (9, 10, 13)) / max(1, min(len(sample), 65536))
        rows.append(info_row(ctx, g, "info.unknown.text_ratio", f"{printable * 100:.0f} %"))
    return ctx.result(kind="info", info=rows, message=desc[:120] if desc else ctx.original(f).name)


@action(id="unknown.hex", label="act.unknown.hex", icon="hexagon", categories={"unknown", "package", "appimage", "disk"},
        multi="single", undoable=False, when=_is_unknown, description="desc.unknown.hex", order=4)
def unknown_hex(ctx: Context):
    """Hex dump of the first 4 KB."""
    f = ctx.file
    data = _read_at(f.path, 0, 4096)
    text = hexdump(data)
    if f.size > len(data):
        text += "\n" + ctx.t("msg.unknown.hex_more", size=util.human_size(f.size - len(data), ctx.lang))
    return ctx.result(kind="text", text=text, message=ctx.t("msg.unknown.hex", n=len(data)))


@action(id="unknown.strings", label="act.unknown.strings", icon="text_snippet",
        categories={"unknown", "package", "appimage"}, multi="each", mode="quick", when=_is_unknown,
        description="desc.unknown.strings", order=5, params=[
            P.int("min_len", "prm.unknown.min_len", 4, min=2, max=64),
            P.bool("utf16", "prm.unknown.utf16", True, help="prm.unknown.utf16.help"),
            P.bool("offsets", "prm.unknown.offsets", False),
        ])
def unknown_strings(ctx: Context):
    """Readable text hidden in the file (like ``strings -a``) saved to ``"<name> (stringhe).txt"``."""
    f = ctx.file
    out = ctx.output_path(f, ext="txt", suffix="sfx.strings")
    total = f.size or os.path.getsize(f.path)
    prog = Progress(ctx, total=total or None, label=ctx.t("act.unknown.strings"))
    offsets = ctx.pbool("offsets")
    count = 0
    limit = 200 * 1024 * 1024
    written = 0
    with atomic_target(out) as part, open(f.path, "rb") as src, open(part, "w", encoding="utf-8",
                                                                           newline="\n") as dst:
        last = [0]

        def on_progress(pos: int) -> None:
            ctx.check_cancel()
            prog.add(pos - last[0])
            last[0] = pos

        for offset, text in iter_strings(src, max(2, ctx.pint("min_len", 4)), ctx.pbool("utf16", True),
                                         on_progress=on_progress):
            line = f"{offset:08x}  {text}\n" if offsets else f"{text}\n"
            dst.write(line)
            written += len(line)
            count += 1
            if written > limit:
                ctx.warn(ctx.t("warn.unknown.truncated", size=util.human_size(limit, ctx.lang)))
                break
    if count == 0:
        ctx.warn(ctx.t("warn.unknown.no_strings"))
    return ctx.result([out], message=i18n.tn("msg.unknown.strings", count, ctx.lang))


# =================================================================================== layout

layout("appimage", ["appimage.integrate", "appimage.unintegrate", "appimage.run", "file.chmodx", "appimage.extract",
                    "appimage.info", "disk.verify", "unknown.hex"])
group("disk.mount", "grp.mount", "album", ["disk.mount", "disk.unmount"], default="disk.mount")
layout("disk", ["disk.mount", "disk.unmount", "disk.verify", "disk.extract", "disk.info", "file.checksum",
                "unknown.hex"])
layout("package", ["package.info", "package.files", "package.install", "package.extract", "disk.verify",
                   "file.checksum", "unknown.identify", "unknown.strings"])
layout("unknown", ["unknown.identify", "file.checksum", "file.zip", "unknown.hex", "unknown.strings",
                   "file.open_with", "archive.join", "file.encrypt"])
