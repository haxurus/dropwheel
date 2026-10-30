"""Folder actions (category ``folder``).

Top ring: Compress → (ZIP★ · 7Z · TAR.ZST · TAR.GZ · TAR.XZ · ZIP/7Z with password), Images → PDF,
Batch rename…, Size breakdown, Find duplicates, Checksums → (create SHA256SUMS★ · verify), Contents
→ TXT, More → (Flatten, Count by type, Terminal here).  With several folders, Compress puts them
all in one archive.

Rules (feature catalogue §2.15): symlinks are never followed, hidden files and the usual heavy
folders (``.git``, ``node_modules``…) are skipped by the scanning actions unless asked otherwise,
unreadable sub-folders are counted and reported, nothing inside the dropped folder is modified
(except by Batch rename, which renames the folder's own entries and says so).
"""

from __future__ import annotations

import hashlib
import inspect
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .. import i18n, tools, util
from ..detect import FileInfo, ext_category
from ..errors import MissingTool, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout
from . import archive
from .common import (
    HASH_ALGOS, Progress, TreeNode, _check_space, _rename_result, copy_entry, hash_file, info_row, plan_renames,
    render_tree, tree_stats,
)

__all__ = ["walk_files", "find_duplicates", "images_in", "size_breakdown", "parse_sums", "sums_line",
           "DuplicateGroup", "Breakdown"]

#: Folders skipped by the scanning actions unless "include hidden" is on.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", ".cache", ".Trash",
                       ".Trash-1000", ".tox", ".mypy_cache", ".pytest_cache"})
_PARTIAL = 64 * 1024
_SCAN_LIMIT = 2_000_000


# ================================================================================ scanning

@dataclass
class ScanStats:
    """Counters filled while walking."""

    errors: int = 0
    skipped_links: int = 0


def walk_files(root: str | os.PathLike, *, ctx: Context | None = None, hidden: bool = False,
               skip_dirs: frozenset = SKIP_DIRS, recursive: bool = True, stats: ScanStats | None = None,
               dirs: bool = False) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield ``(path, lstat)`` of the regular files below ``root`` (and of folders when ``dirs``).

    Symlinks are never followed (and not yielded); hidden entries and ``skip_dirs`` are skipped
    unless ``hidden``; unreadable folders are counted in ``stats.errors``.
    """
    stats = stats if stats is not None else ScanStats()
    stack = [os.fspath(root)]
    seen = 0
    while stack:
        current = stack.pop()
        if ctx is not None:
            ctx.check_cancel()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: util.natural_key(e.name))
        except OSError:
            stats.errors += 1
            continue
        subdirs: list[str] = []
        for entry in entries:
            seen += 1
            if seen > _SCAN_LIMIT:
                return
            name = entry.name
            if not hidden and (name.startswith(".") or name in skip_dirs):
                continue
            try:
                if entry.is_symlink():
                    stats.skipped_links += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if dirs:
                        yield Path(entry.path), entry.stat(follow_symlinks=False)
                    if recursive:
                        subdirs.append(entry.path)
                    continue
                if entry.is_file(follow_symlinks=False):
                    yield Path(entry.path), entry.stat(follow_symlinks=False)
            except OSError:
                stats.errors += 1
        stack.extend(reversed(subdirs))


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _pct(part: int, total: int, lang: str) -> str:
    if total <= 0:
        return "0 %"
    value = part * 100.0 / total
    text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    return (text.replace(".", ",") if lang == "it" else text) + " %"


def _folders_only(files: list[FileInfo]) -> bool:
    return bool(files) and all(f.is_dir and f.exists for f in files)


# ================================================================================ compress

def _compress(ctx: Context, fmt: str, password_required: bool = False):
    password = ctx.pstr("password")
    if password_required and not password:
        raise UserError("err.missing_param", param=ctx.t("prm.file.password"))
    confirm = ctx.pstr("confirm")
    if confirm and confirm != password:
        raise UserError("err.file.password_mismatch")
    paths = [Path(f.path) for f in ctx.files]
    before = sum(tree_stats(p, ctx)[0] for p in paths)
    outputs = archive.create_archive(ctx, paths, fmt, password=password, level=ctx.pstr("level", "normal"),
                                     volume_mb=ctx.pint("split", 0))
    return ctx.result(outputs, size_before=before or None)


def _compress_params(with_password: bool, fmt: str) -> list:
    params: list = [
        P.select("level", "prm.arc.level", options=[("fast", "opt.arc.level.fast"), ("normal", "opt.arc.level.normal"),
                                                    ("max", "opt.arc.level.max")], default="normal"),
    ]
    if with_password:
        params += [P.password("password", "prm.file.password", default="", required=True),
                   P.password("confirm", "prm.file.confirm", default="")]
    elif fmt in ("zip", "7z"):
        params.append(P.password("password", "prm.arc.password", default="", help="prm.arc.password.help"))
    params.append(P.select("split", "prm.arc.split", options=[(0, "opt.arc.split.none"), (25, "25 MB"),
                                                               (100, "100 MB"), (700, "700 MB"), (1000, "1 GB"),
                                                               (4000, "4 GB")], default=0))
    return params


def _register_compress(fmt: str, order: int, with_password: bool = False) -> None:
    key = fmt.replace(".", "_") + ("_password" if with_password else "")
    label = archive.FORMAT_TEXT[fmt] if not with_password else f"act.folder.{key}"

    def fn(ctx: Context):
        return _compress(ctx, fmt, with_password)

    fn.__name__ = f"folder_compress_{key}"
    fn.__doc__ = f"Compress the folders into one {archive.FORMAT_TEXT[fmt]} archive."
    requires: list = []
    if fmt == "7z" or with_password:
        requires.append("7z")
    if fmt == "tar.zst":
        requires += list(archive._ZSTD_REQ)
    action(id=f"folder.compress.{key}", label=label, text=None if with_password else archive.FORMAT_TEXT[fmt],
           icon="lock" if with_password else "", categories={"folder"}, multi="any",
           mode="dialog" if with_password else "quick", dialog="form" if with_password else None,
           requires=requires, description="desc.folder.compress_password" if with_password else "desc.folder.compress",
           order=order, params=_compress_params(with_password, fmt))(fn)


for _i, _fmt in enumerate(archive.CREATE_FORMATS):
    _register_compress(_fmt, _i + 1)
_register_compress("zip", 10, with_password=True)
_register_compress("7z", 11, with_password=True)


# ================================================================================ size breakdown

@dataclass
class Breakdown:
    """Result of :func:`size_breakdown`."""

    total: int = 0
    files: int = 0
    folders: int = 0
    children: list[tuple[str, bool, int, int]] = field(default_factory=list)   # name, is_dir, size, files
    largest: list[tuple[int, str]] = field(default_factory=list)                 # size, relative path
    errors: int = 0


def size_breakdown(root: str | os.PathLike, ctx: Context | None = None, top: int = 20,
                   on_progress: Callable[[int], None] | None = None) -> Breakdown:
    """Recursive sizes of ``root``'s children and its largest files (hidden files included)."""
    import heapq

    root_p = Path(root)
    out = Breakdown()
    child_sizes: dict[str, list] = {}
    heap: list[tuple[int, str]] = []
    stats = ScanStats()
    try:
        with os.scandir(root_p) as it:
            top_entries = list(it)
    except OSError as exc:
        raise UserError("err.not_readable", details=str(exc), name=root_p.name) from None
    for entry in top_entries:
        try:
            is_dir = entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
        except OSError:
            continue
        child_sizes[entry.name] = [is_dir, 0, 0]
    count = 0
    for path, st in walk_files(root_p, ctx=ctx, hidden=True, skip_dirs=frozenset(), stats=stats, dirs=True):
        rel = _rel(path, root_p)
        head = rel.split("/", 1)[0]
        if stat.S_ISDIR(st.st_mode):
            out.folders += 1
            continue
        size = st.st_size
        out.total += size
        out.files += 1
        bucket = child_sizes.get(head)
        if bucket is not None:
            bucket[1] += size
            bucket[2] += 1
        if len(heap) < top:
            heapq.heappush(heap, (size, rel))
        elif size > heap[0][0]:
            heapq.heapreplace(heap, (size, rel))
        count += 1
        if on_progress is not None and count % 500 == 0:
            on_progress(count)
    out.errors = stats.errors
    out.children = sorted(((name, v[0], v[1], v[2]) for name, v in child_sizes.items()), key=lambda x: -x[2])[:top]
    out.largest = sorted(heap, key=lambda x: -x[0])
    return out


@action(id="folder.size", label="act.folder.size", icon="pie_chart", categories={"folder"}, multi="each",
        undoable=False, when=_folders_only, description="desc.folder.size", order=40)
def folder_size(ctx: Context):
    """Total size, the largest sub-folders/files at the first level and the 20 largest files."""
    f = ctx.file
    lang = ctx.lang
    ctx.progress(None, ctx.t("msg.file.measuring"))
    res = size_breakdown(f.path, ctx, on_progress=lambda n: ctx.progress(None, ctx.t("msg.folder.scanned", n=n)))
    rows = [
        info_row(ctx, "info.grp.total", "info.file.size", util.human_size(res.total, lang)),
        info_row(ctx, "info.grp.total", "info.folder.files", res.files),
        info_row(ctx, "info.grp.total", "info.folder.folders", res.folders),
    ]
    if res.errors:
        rows.append(info_row(ctx, "info.grp.total", "info.folder.unreadable", res.errors))
    group_children = "info.grp.children"
    for name, is_dir, size, files in res.children:
        label = name + ("/" if is_dir else "")
        value = f"{util.human_size(size, lang)} · {_pct(size, res.total, lang)}"
        if is_dir:
            value += " · " + i18n.tn("msg.folder.n_files", files, lang)
        rows.append({"group": ctx.t(group_children), "key": label, "value": value})
    for size, rel in res.largest:
        rows.append({"group": ctx.t("info.grp.largest"), "key": rel, "value": util.human_size(size, lang)})
    if res.errors:
        ctx.warn(i18n.tn("warn.folder.unreadable", res.errors, lang))
    return ctx.result(kind="info", info=rows, message=ctx.t("msg.folder.size", name=ctx.original(f).name,
                                                            size=util.human_size(res.total, lang)))


# ================================================================================ count by type

@action(id="folder.types", label="act.folder.types", icon="category", categories={"folder"}, multi="each",
        undoable=False, when=_folders_only, description="desc.folder.types", order=70,
        params=[P.bool("hidden", "prm.folder.hidden", False)])
def folder_types(ctx: Context):
    """How many files of each kind (images, documents, videos…) and the most common extensions."""
    f = ctx.file
    lang = ctx.lang
    by_cat: dict[str, list[int]] = {}
    by_ext: dict[str, list[int]] = {}
    stats = ScanStats()
    total = 0
    count = 0
    for path, st in walk_files(f.path, ctx=ctx, hidden=ctx.pbool("hidden"), stats=stats):
        stem, ext = util.split_name(path.name)
        hit = ext_category(ext, path.name) if ext else ext_category("", path.name)
        cat = hit[0] if hit else "unknown"
        for table, key in ((by_cat, cat), (by_ext, ext or "")):
            slot = table.setdefault(key, [0, 0])
            slot[0] += 1
            slot[1] += st.st_size
        total += st.st_size
        count += 1
        if count % 1000 == 0:
            ctx.progress(None, ctx.t("msg.folder.scanned", n=count))
    rows = [info_row(ctx, "info.grp.total", "info.folder.files", count),
            info_row(ctx, "info.grp.total", "info.file.size", util.human_size(total, lang))]
    for cat, (n, size) in sorted(by_cat.items(), key=lambda kv: (-kv[1][0], kv[0])):
        rows.append({"group": ctx.t("info.grp.by_type"), "key": ctx.t(f"cat.{cat}"),
                     "value": f"{i18n.tn('msg.folder.n_files', n, lang)} · {util.human_size(size, lang)}"})
    for ext, (n, size) in sorted(by_ext.items(), key=lambda kv: (-kv[1][0], kv[0]))[:20]:
        rows.append({"group": ctx.t("info.grp.by_ext"), "key": f".{ext}" if ext else ctx.t("info.folder.no_ext"),
                     "value": f"{n} · {util.human_size(size, lang)}"})
    if stats.errors:
        ctx.warn(i18n.tn("warn.folder.unreadable", stats.errors, lang))
    return ctx.result(kind="info", info=rows, message=i18n.tn("msg.folder.n_files", count, lang))


# ================================================================================ contents → TXT

@action(id="folder.list_txt", label="act.folder.list_txt", icon="list_alt", categories={"folder"}, multi="each",
        mode="quick", when=_folders_only, description="desc.folder.list_txt", order=60,
        params=[P.bool("hidden", "prm.folder.hidden", False), P.bool("sizes", "prm.folder.sizes", True)])
def folder_list_txt(ctx: Context):
    """Write the folder tree (with sizes) to ``"<folder> (elenco).txt"``."""
    f = ctx.file
    root = Path(f.path)
    tree = TreeNode("")
    stats = ScanStats()
    count = 0
    for path, st in walk_files(root, ctx=ctx, hidden=ctx.pbool("hidden"), stats=stats, dirs=True):
        parts = _rel(path, root).split("/")
        is_dir = stat.S_ISDIR(st.st_mode)
        tree.add_path(parts, None if is_dir else st.st_size, is_dir)
        count += 1
        if count % 1000 == 0:
            ctx.progress(None, ctx.t("msg.folder.scanned", n=count))
    files, folders = tree.count()
    name = ctx.original(f).name
    header = [
        f"{name}/",
        ctx.t("msg.folder.list_header", files=files, folders=folders, size=util.human_size(tree.total(), ctx.lang)),
        datetime.now().strftime("%d/%m/%Y %H:%M" if ctx.lang == "it" else "%Y-%m-%d %H:%M"),
        "",
    ]
    lines = render_tree(tree, ctx.lang, sizes=ctx.pbool("sizes", True), max_lines=200_000,
                        more_text=ctx.t("msg.arc.list_more"))
    out = ctx.output_path(f, ext="txt", suffix="sfx.listing")
    with atomic_target(out) as part:
        part.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    if stats.errors:
        ctx.warn(i18n.tn("warn.folder.unreadable", stats.errors, ctx.lang))
    return ctx.result([out], message=i18n.tn("msg.folder.n_files", files, ctx.lang))


# ================================================================================ images → PDF

_IMG_SKIP_EXTS = {"svg", "svgz", "psd", "xcf", "ico", "icns"}
PAGE_SIZES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}


def _is_image_name(name: str) -> bool:
    _stem, ext = util.split_name(name)
    if not ext or ext in _IMG_SKIP_EXTS:
        return False
    hit = ext_category(ext, name)
    return bool(hit and hit[0] == "image")


def images_in(folder: str | os.PathLike, *, recursive: bool = False, hidden: bool = False,
              ctx: Context | None = None) -> list[Path]:
    """Images of ``folder`` in natural order of their relative paths."""
    root = Path(folder)
    found = [p for p, _st in walk_files(root, ctx=ctx, hidden=hidden, recursive=recursive) if _is_image_name(p.name)]
    return util.natural_sorted(found, key=lambda p: _rel(p, root))


_HAS_IMAGES_CACHE: dict[tuple[str, float], bool] = {}


def _has_images(files: list[FileInfo]) -> bool:
    for f in files:
        if not (f.is_dir and f.exists):
            return False
        try:
            key = (f.path, os.stat(f.path).st_mtime)
        except OSError:
            return False
        hit = _HAS_IMAGES_CACHE.get(key)
        if hit is None:
            hit = False
            try:
                with os.scandir(f.path) as it:
                    for n, entry in enumerate(it):
                        if n > 5000:
                            break
                        if not entry.name.startswith(".") and _is_image_name(entry.name):
                            try:
                                if entry.is_file():
                                    hit = True
                                    break
                            except OSError:
                                continue
            except OSError:
                hit = False
            if len(_HAS_IMAGES_CACHE) > 256:
                _HAS_IMAGES_CACHE.clear()
            _HAS_IMAGES_CACHE[key] = hit
        if not hit:
            return False
    return True


def _open_pil(path: Path) -> Any:
    from PIL import Image

    try:
        return Image.open(path)
    except Exception:
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
            return Image.open(path)
        except Exception:
            return None


def _fit(box_w: float, box_h: float, w: float, h: float) -> tuple[float, float, float, float]:
    scale = min(box_w / w, box_h / h)
    nw, nh = w * scale, h * scale
    x0, y0 = (box_w - nw) / 2, (box_h - nh) / 2
    return x0, y0, x0 + nw, y0 + nh


def _page_for(page: str, w: int, h: int) -> tuple[float, float]:
    if page in PAGE_SIZES:
        pw, ph = PAGE_SIZES[page]
        return (ph, pw) if w > h else (pw, ph)
    pw, ph = w * 72.0 / 96.0, h * 72.0 / 96.0
    scale = min(1.0, 14400.0 / max(pw, ph))
    return max(3.0, pw * scale), max(3.0, ph * scale)


def _prepared_image(ctx: Context, path: Path, index: int) -> tuple[Path, int, int] | None:
    """A file PyMuPDF can embed as-is (EXIF orientation applied) and its pixel size; ``None`` if unreadable."""
    try:
        from PIL import ImageOps
    except ImportError:
        return None
    im = _open_pil(path)
    if im is None:
        return None
    with im:
        try:
            orientation = im.getexif().get(0x0112, 1)
        except Exception:
            orientation = 1
        fmt = (im.format or "").upper()
        if fmt in ("JPEG", "PNG") and orientation in (1, None) and not getattr(im, "is_animated", False):
            return path, im.width, im.height
        try:
            im.seek(0)
        except EOFError:
            pass
        fixed = ImageOps.exif_transpose(im)
        if fixed.mode in ("RGBA", "LA", "PA") or (fixed.mode == "P" and "transparency" in fixed.info):
            fixed = fixed.convert("RGBA")
            target = ctx.tmpdir() / f"img-{index:05d}.png"
            fixed.save(target, "PNG")
        else:
            if fixed.mode not in ("RGB", "L"):
                fixed = fixed.convert("RGB")
            target = ctx.tmpdir() / f"img-{index:05d}.jpg"
            fixed.save(target, "JPEG", quality=92, subsampling=0)
        return target, fixed.width, fixed.height


def _pdf_pymupdf(ctx: Context, images: list[Path], part: Path, page: str, prog: Progress) -> int:
    import pymupdf  # type: ignore

    doc = pymupdf.open()
    added = 0
    try:
        for i, path in enumerate(images):
            ctx.check_cancel()
            prog.frac(i / len(images), path.name)
            prepared = _prepared_image(ctx, path, i)
            if prepared is None:
                try:
                    pix = pymupdf.Pixmap(str(path))
                    prepared = (path, pix.width, pix.height)
                except Exception:
                    ctx.log(f"skipped unreadable image {path}")
                    continue
            src, w, h = prepared
            pw, ph = _page_for(page, w, h)
            pg = doc.new_page(width=pw, height=ph)
            rect = pymupdf.Rect(*_fit(pw, ph, w, h))
            try:
                pg.insert_image(rect, filename=str(src), keep_proportion=True)
            except Exception as exc:
                doc.delete_page(pg.number)
                ctx.log(f"skipped {path}: {exc}")
                continue
            added += 1
        if added:
            doc.save(str(part), garbage=3, deflate=True)
    finally:
        doc.close()
    return added


def _pdf_pillow(ctx: Context, images: list[Path], part: Path, page: str, prog: Progress) -> int:
    from PIL import Image, ImageOps

    dpi = 150
    added = 0
    for i, path in enumerate(images):
        ctx.check_cancel()
        prog.frac(i / len(images), path.name)
        im = _open_pil(path)
        if im is None:
            continue
        with im:
            fixed = ImageOps.exif_transpose(im)
            if fixed.mode in ("RGBA", "LA", "PA", "P"):
                rgba = fixed.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                fixed = bg
            elif fixed.mode != "RGB":
                fixed = fixed.convert("RGB")
            if page in PAGE_SIZES:
                pw, ph = _page_for(page, fixed.width, fixed.height)
                cw, ch = int(pw / 72 * dpi), int(ph / 72 * dpi)
                canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
                x0, y0, x1, y1 = _fit(cw, ch, fixed.width, fixed.height)
                size = (max(1, int(x1 - x0)), max(1, int(y1 - y0)))
                canvas.paste(fixed.resize(size, Image.LANCZOS), (int(x0), int(y0)))
                fixed = canvas
                res = dpi
            else:
                res = 96
            fixed.save(part, "PDF", resolution=res, append=added > 0)
            added += 1
    return added


def _delegate_images_pdf(ctx: Context, images: list[Path], part: Path, page: str) -> bool:
    """Use ``actions.image.images_to_pdf(ctx, paths, out, *, page=…)`` when act-image provides it."""
    try:
        from . import image as image_mod  # type: ignore[attr-defined]
    except Exception:
        return False
    fn = getattr(image_mod, "images_to_pdf", None)
    if not callable(fn):
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    positional = [p for p in params.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if len(positional) < 3:
        return False
    kwargs = {"page": page} if "page" in params else {}
    fn(ctx, list(images), part, **kwargs)
    return os.path.exists(part) and os.path.getsize(part) > 0


@action(id="folder.images_pdf", label="act.folder.images_pdf", icon="picture_as_pdf", categories={"folder"},
        multi="each", mode="quick", requires=[("py:pymupdf", "py:PIL")], when=_has_images,
        description="desc.folder.images_pdf", order=20, params=[
            P.select("page", "prm.folder.page", options=[("a4", "A4"), ("letter", "Letter"),
                                                         ("image", "opt.folder.page_image")], default="a4"),
            P.bool("recursive", "prm.folder.recursive", False),
        ])
def folder_images_pdf(ctx: Context):
    """One PDF with every image of the folder, one per page, in natural name order."""
    f = ctx.file
    images = images_in(f.path, recursive=ctx.pbool("recursive"), ctx=ctx)
    if not images:
        raise UserError("err.folder.no_images")
    page = ctx.pstr("page", "a4")
    out = ctx.output_path(f, ext="pdf")
    prog = Progress(ctx, label=ctx.t("act.folder.images_pdf"))
    added = len(images)
    with atomic_target(out) as part:
        if not _delegate_images_pdf(ctx, images, part, page):
            if tools.have("py:pymupdf"):
                added = _pdf_pymupdf(ctx, images, part, page, prog)
            elif tools.have("py:PIL"):
                added = _pdf_pillow(ctx, images, part, page, prog)
            else:
                raise MissingTool("py:pymupdf")
            if not added:
                raise UserError("err.folder.no_images")
    skipped = len(images) - added
    if skipped > 0:
        ctx.warn(i18n.tn("warn.folder.images_skipped", skipped, ctx.lang))
    return ctx.result([out], message=i18n.tn("msg.folder.pages", added, ctx.lang))


# ================================================================================ duplicates

@dataclass
class DuplicateGroup:
    """Files with identical content."""

    size: int
    digest: str
    paths: list[Path]

    @property
    def wasted(self) -> int:
        """Bytes that removing all but one copy would free."""
        return self.size * (len(self.paths) - 1)


def _partial_digest(path: Path, size: int) -> bytes:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        h.update(fh.read(_PARTIAL))
        if size > 2 * _PARTIAL:
            fh.seek(-_PARTIAL, os.SEEK_END)
            h.update(fh.read(_PARTIAL))
    return h.digest()


def _full_digest(ctx: Context | None, path: Path, prog: Progress | None) -> bytes:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as fh:
        while True:
            if ctx is not None:
                ctx.check_cancel()
            chunk = fh.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            if prog is not None:
                prog.add(len(chunk))
    return h.digest()


def find_duplicates(roots: Sequence[str | os.PathLike], *, ctx: Context | None = None, hidden: bool = False,
                    min_size: int = 1, prog: Progress | None = None,
                    stats: ScanStats | None = None) -> list[DuplicateGroup]:
    """Groups of identical files below ``roots``: size → partial hash (head+tail) → full BLAKE2b.

    Hard links to the same inode count once; symlinks are ignored.  Never modifies anything.
    """
    stats = stats if stats is not None else ScanStats()
    by_size: dict[int, list[Path]] = {}
    inodes: set[tuple[int, int]] = set()
    for root in roots:
        for path, st in walk_files(root, ctx=ctx, hidden=hidden, stats=stats):
            if st.st_size < max(1, min_size):
                continue
            ino = (st.st_dev, st.st_ino)
            if st.st_ino and ino in inodes:
                continue
            inodes.add(ino)
            by_size.setdefault(st.st_size, []).append(path)
    stage2: dict[tuple[int, bytes], list[Path]] = {}
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for p in paths:
            if ctx is not None:
                ctx.check_cancel()
            try:
                stage2.setdefault((size, _partial_digest(p, size)), []).append(p)
            except OSError:
                stats.errors += 1
    full_total = sum(size * len(paths) for (size, _d), paths in stage2.items()
                     if len(paths) >= 2 and size > 2 * _PARTIAL)
    if prog is not None:
        prog.total = full_total or None
    groups: list[DuplicateGroup] = []
    for (size, partial), paths in stage2.items():
        if len(paths) < 2:
            continue
        if size <= 2 * _PARTIAL:
            groups.append(DuplicateGroup(size, partial.hex(), sorted(paths, key=lambda p: util.natural_key(str(p)))))
            continue
        stage3: dict[bytes, list[Path]] = {}
        for p in paths:
            try:
                stage3.setdefault(_full_digest(ctx, p, prog), []).append(p)
            except OSError:
                stats.errors += 1
        for digest, same in stage3.items():
            if len(same) >= 2:
                groups.append(DuplicateGroup(size, digest.hex(), sorted(same, key=lambda p: util.natural_key(str(p)))))
    groups.sort(key=lambda g: (-g.wasted, str(g.paths[0])))
    return groups


@action(id="folder.duplicates", label="act.folder.duplicates", icon="content_copy", categories={"folder"},
        multi="any", mode="quick", undoable=False, when=_folders_only, description="desc.folder.duplicates",
        order=50, params=[P.bool("hidden", "prm.folder.hidden", False),
                          P.int("min_kb", "prm.folder.min_size", 0, min=0, max=10_000_000, unit="kB")])
def folder_duplicates(ctx: Context):
    """Report identical files (never deletes anything); several folders are compared together."""
    roots = [Path(f.path) for f in ctx.files]
    originals = [Path(ctx.original(f).path) for f in ctx.files]
    stats = ScanStats()
    ctx.progress(None, ctx.t("act.folder.duplicates"))
    prog = Progress(ctx, label=ctx.t("act.folder.duplicates"))
    groups = find_duplicates(roots, ctx=ctx, hidden=ctx.pbool("hidden"), min_size=max(1, ctx.pint("min_kb", 0) * 1000),
                             prog=prog, stats=stats)
    if stats.errors:
        ctx.warn(i18n.tn("warn.folder.unreadable", stats.errors, ctx.lang))
    if not groups:
        return ctx.result(kind="none", message=ctx.t("msg.folder.no_duplicates"))
    lang = ctx.lang

    def shown(p: Path) -> str:
        for root, orig in zip(roots, originals):
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            return f"{orig.name}/{rel}" if len(roots) > 1 else rel
        return str(p)

    wasted = sum(g.wasted for g in groups)
    lines = [ctx.t("msg.folder.dup_header", groups=len(groups), size=util.human_size(wasted, lang)), ""]
    for i, g in enumerate(groups, 1):
        lines.append(f"[{i}] {util.human_size(g.size, lang)} × {len(g.paths)}")
        lines.extend(f"    {shown(p)}" for p in g.paths)
        lines.append("")
    return ctx.result(kind="text", text="\n".join(lines).rstrip() + "\n",
                      message=i18n.tn("msg.folder.duplicates", len(groups), lang, size=util.human_size(wasted, lang)))


# ================================================================================ batch rename

@action(id="folder.rename", label="act.folder.rename", icon="drive_file_rename_outline", categories={"folder"},
        multi="each", mode="dialog", dialog="form", undoable=False, when=_folders_only,
        description="desc.folder.rename", order=30, params=[
            P.text("pattern", "prm.folder.pattern", default="{name}", required=True, help="prm.folder.pattern.help"),
            P.text("find", "prm.folder.find", default=""),
            P.text("replace", "prm.folder.replace", default=""),
            P.bool("regex", "prm.folder.regex", False),
            P.select("case", "prm.folder.case", options=[("keep", "opt.folder.case.keep"), ("lower", "opt.folder.case.lower"),
                                                         ("upper", "opt.folder.case.upper"), ("title", "opt.folder.case.title")],
                     default="keep"),
            P.int("start", "prm.file.start", 1, min=0, max=1_000_000),
            P.bool("folders", "prm.folder.include_folders", False),
            P.bool("dry_run", "prm.file.dry_run", False),
        ])
def folder_rename(ctx: Context):
    """Rename the files inside the folder with a pattern (``{name}`` ``{n}`` ``{n:03}`` ``{date}``
    ``{ext}`` ``{parent}``), find/replace (optionally regex) and case changes; the result lists every
    change, "Preview only" changes nothing."""
    root = Path(ctx.original(ctx.file).path)
    entries = [p for p, st in walk_files(root, ctx=ctx, recursive=False, dirs=ctx.pbool("folders"))]
    if not entries:
        raise UserError("err.folder.empty")
    entries = util.natural_sorted(entries, key=lambda p: p.name)
    pattern = ctx.pstr("pattern", "{name}").strip() or "{name}"
    plan = plan_renames(entries, pattern, start=ctx.pint("start", 1), find=ctx.pstr("find"),
                        replace=ctx.pstr("replace"), regex=ctx.pbool("regex"), case=ctx.pstr("case", "keep"))
    return _rename_result(ctx, plan, dry_run=ctx.pbool("dry_run"))


# ================================================================================ flatten

@action(id="folder.flatten", label="act.folder.flatten", icon="layers_clear", categories={"folder"}, multi="each",
        when=_folders_only, description="desc.folder.flatten", order=80,
        params=[P.bool("hidden", "prm.folder.hidden", False)])
def folder_flatten(ctx: Context):
    """Copy every file of the folder tree into one new folder ``"<name> (appiattito)"`` (no sub-folders;
    clashing names get " 2", " 3"…).  The original folder is not touched."""
    f = ctx.file
    files = [(p, st) for p, st in walk_files(f.path, ctx=ctx, hidden=ctx.pbool("hidden"))]
    if not files:
        raise UserError("err.folder.empty")
    total = sum(st.st_size for _p, st in files)
    outdir = ctx.output_dir(f, suffix="sfx.flattened")
    _check_space(outdir, total)
    prog = Progress(ctx, total=total or None, label=ctx.t("act.folder.flatten"))
    ci = util.is_case_insensitive_fs(outdir)
    for path, _st in files:
        ctx.check_cancel()
        target = util.unique_path(outdir / util.safe_filename(path.name), ci=ci)
        copy_entry(ctx, path, target, prog)
    return ctx.result([outdir], message=i18n.tn("msg.folder.flattened", len(files), ctx.lang))


# ================================================================================ checksums

def _escape_sum_name(name: str) -> tuple[str, str]:
    if "\\" in name or "\n" in name or "\r" in name:
        return "\\", name.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return "", name


def sums_line(digest: str, rel: str) -> str:
    """One GNU coreutils line (``sha256sum -c`` compatible, with its backslash escaping)."""
    prefix, name = _escape_sum_name(rel)
    return f"{prefix}{digest}  {name}"


def _unescape(name: str) -> str:
    out = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "\\" and i + 1 < len(name):
            nxt = name[i + 1]
            out.append({"n": "\n", "r": "\r", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_sums(text: str) -> list[tuple[str, str, str]]:
    """Parse GNU (``HASH  name``, ``\\HASH  esc``) and BSD (``SHA256 (name) = HASH``) lines into
    ``(algo, hex, name)``; the algorithm is guessed from the digest length for GNU lines."""
    import re

    bsd = re.compile(r"^(SHA256|SHA512|SHA384|SHA224|SHA1|MD5|BLAKE2b)\s*\((.+)\)\s*=\s*([0-9a-fA-F]+)\s*$", re.I)
    gnu = re.compile(r"^(\\?)([0-9a-fA-F]{32,128})\s[ *](.+)$")
    by_len = {32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512"}
    out: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = bsd.match(line.strip())
        if m:
            algo = m.group(1).lower().replace("blake2b", "blake2")
            out.append((algo, m.group(3).lower(), m.group(2)))
            continue
        m = gnu.match(line)
        if m:
            name = _unescape(m.group(3)) if m.group(1) else m.group(3)
            algo = by_len.get(len(m.group(2)), "")
            if algo:
                out.append((algo, m.group(2).lower(), name))
    return out


def _sums_candidates(f: FileInfo) -> list[Path]:
    p = Path(f.path)
    cands = [p.parent / f"{p.name}.sha256", p.parent / f"{p.name}.sha256sum", p.parent / f"{p.name}.sha512",
             p / "SHA256SUMS", p / "SHA256SUMS.txt", p / "sha256sums.txt", p / "SHA512SUMS", p / "CHECKSUMS"]
    return [c for c in cands if c.is_file()]


def _has_sums(files: list[FileInfo]) -> bool:
    return _folders_only(files) and all(_sums_candidates(f) for f in files)


@action(id="folder.checksum.create", label="act.folder.sums_create", icon="fingerprint", categories={"folder"},
        multi="each", when=_folders_only, description="desc.folder.sums_create", order=1,
        params=[P.bool("hidden", "prm.folder.hidden", False)])
def folder_sums_create(ctx: Context):
    """Write ``"<folder>.sha256"`` next to the folder (check it later with ``sha256sum -c``)."""
    f = ctx.file
    root = Path(f.path)
    name = ctx.original(f).name
    files = [p for p, _st in walk_files(root, ctx=ctx, hidden=ctx.pbool("hidden"))]
    if not files:
        raise UserError("err.folder.empty")
    total = sum(p.stat().st_size for p in files)
    prog = Progress(ctx, total=total or None, label=HASH_ALGOS["sha256"])
    lines = []
    for p in files:
        digest = hash_file(ctx, p, "sha256", prog)
        lines.append(sums_line(digest, f"{name}/{_rel(p, root)}"))
    out = ctx.output_path(f, name=f"{name}.sha256")
    with atomic_target(out) as part:
        part.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return ctx.result([out], message=i18n.tn("msg.folder.sums_created", len(lines), ctx.lang))


@action(id="folder.checksum.verify", label="act.folder.sums_verify", icon="verified", categories={"folder"},
        multi="each", undoable=False, when=_has_sums, description="desc.folder.sums_verify", order=2)
def folder_sums_verify(ctx: Context):
    """Check the files against ``"<folder>.sha256"`` next to it or ``SHA256SUMS`` inside it."""
    f = ctx.file
    cands = _sums_candidates(f)
    if not cands:
        raise UserError("err.folder.no_sums")
    sums_file = cands[0]
    try:
        entries = parse_sums(sums_file.read_text(encoding="utf-8", errors="surrogateescape"))
    except OSError as exc:
        raise UserError("err.not_readable", details=str(exc), name=sums_file.name) from None
    if not entries:
        raise UserError("err.folder.no_sums")
    base = sums_file.parent
    total = 0
    for _a, _d, name in entries:
        try:
            total += (base / name).stat().st_size
        except OSError:
            pass
    prog = Progress(ctx, total=total or None, label=ctx.t("act.folder.sums_verify"))
    ok = 0
    bad: list[str] = []
    missing: list[str] = []
    for algo, digest, name in entries:
        ctx.check_cancel()
        target = (base / name)
        if not target.is_file():
            missing.append(name)
            continue
        if algo not in HASH_ALGOS:
            continue
        actual = hash_file(ctx, target, algo, prog)
        if actual.lower() == digest:
            ok += 1
        else:
            bad.append(name)
    lines = [ctx.t("msg.folder.sums_report", ok=ok, bad=len(bad), missing=len(missing), file=sums_file.name), ""]
    if bad:
        lines.append(ctx.t("msg.folder.sums_bad"))
        lines.extend(f"  ✗ {n}" for n in bad)
        lines.append("")
    if missing:
        lines.append(ctx.t("msg.folder.sums_missing"))
        lines.extend(f"  ? {n}" for n in missing)
    if bad or missing:
        ctx.warn(ctx.t("warn.folder.sums_failed", bad=len(bad), missing=len(missing)))
        return ctx.result(kind="text", text="\n".join(lines).rstrip() + "\n",
                          message=ctx.t("msg.folder.sums_failed", bad=len(bad), missing=len(missing)))
    return ctx.result(kind="text", text="\n".join(lines).rstrip() + "\n",
                      message=i18n.tn("msg.folder.sums_ok", ok, ctx.lang))


# =================================================================================== layout

group("folder.compress", "grp.compress", "folder_zip",
      [f"folder.compress.{fmt.replace('.', '_')}" for fmt in archive.CREATE_FORMATS]
      + ["folder.compress.zip_password", "folder.compress.7z_password"], default="folder.compress.zip")
group("folder.checksum", "grp.checksum", "fingerprint", ["folder.checksum.create", "folder.checksum.verify"],
      default="folder.checksum.create")
# ".more" suffix: job labels show the action alone
group("folder.page.more", "grp.more", "more_horiz", ["folder.flatten", "folder.types", "file.terminal"],
      default="folder.flatten")

layout("folder", ["folder.compress", "folder.images_pdf", "folder.rename", "folder.size", "folder.duplicates",
                  "folder.checksum", "folder.list_txt", "folder.page.more"])
layout("multi:folder", ["folder.compress", "folder.duplicates", "folder.size", "folder.images_pdf", "folder.rename",
                        "folder.checksum", "folder.list_txt", "folder.page.more"])
