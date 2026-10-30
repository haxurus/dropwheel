"""Actions for links dropped from a browser (category ``url``: http/https, ftp when classified so).

Top ring: Download★ (curl, Python fallback; name from Content-Disposition, else the URL), Media →
(yt-dlp: video MP4★ · audio MP3 · choose quality…), QR code → (show★ · save PNG), Copy → (link★ ·
Markdown link · clean link), Open in browser, Save page as PDF (headless Chromium), Shortcut.

Privacy: the only host ever contacted is the one of the dropped URL (yt-dlp may follow the
service's own CDN); no shortener, no third-party service.  Downloads go to ``$XDG_DOWNLOAD_DIR``
(or ``output.folder`` when ``output.location = "folder"``), written to a hidden ``.part`` file and
renamed without clobbering anything.
"""

from __future__ import annotations

import hashlib
import html
import mimetypes
import os
import re
import secrets
import shutil
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config as cfgmod
from .. import i18n, tools, util
from ..detect import EXT_TABLE, FileInfo
from ..errors import MissingTool, ToolError, UserError
from ..jobs import Context, atomic_target
from ..registry import P, action, group, layout
from .common import Progress

__all__ = ["filename_from_content_disposition", "filename_from_url", "guess_extension", "clean_url",
           "page_title", "download_dir", "parse_headers", "YTDLP_FORMATS"]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Dropwheel/1"
_SCHEMES = ("http", "https", "ftp", "ftps")
_PCT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_TRACKING = re.compile(r"^(utm_[a-z_]+|fbclid|gclid|gclsrc|dclid|gbraid|wbraid|msclkid|mc_cid|mc_eid|igshid|"
                       r"si|ref_src|ref_url|yclid|_hsenc|_hsmi|mkt_tok|vero_id|oly_anon_id|oly_enc_id|"
                       r"spm|scm|trk|trkCampaign|sc_campaign|sc_channel|feature|pp)$", re.I)
_KEEP_PARAMS_HOSTS = {"youtube.com": {"v", "list", "t", "index"}, "youtu.be": {"t", "list"}}


# ================================================================================ pure helpers

def _url_of(f: FileInfo) -> str:
    url = (f.uri or "").strip()
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in _SCHEMES:
        raise UserError("err.web.bad_url", url=url[:200])
    return url


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def filename_from_content_disposition(value: str | None) -> str:
    """The file name of a ``Content-Disposition`` header (RFC 6266 / RFC 5987), basename only, or ``""``."""
    if not value:
        return ""
    name = ""
    m = re.search(r"filename\*\s*=\s*([^;]+)", value, re.I)
    if m:
        raw = m.group(1).strip().strip('"')
        charset, _sep, rest = raw.partition("'")
        if _sep:
            _lang, _sep2, encoded = rest.partition("'")
            try:
                name = urllib.parse.unquote(encoded, encoding=charset or "utf-8", errors="replace")
            except LookupError:
                name = urllib.parse.unquote(encoded)
    if not name:
        m = re.search(r'filename\s*=\s*"((?:[^"\\]|\\.)*)"', value, re.I)
        if m:
            name = re.sub(r"\\(.)", r"\1", m.group(1))
        else:
            m = re.search(r"filename\s*=\s*([^;\s]+)", value, re.I)
            if m:
                name = urllib.parse.unquote(m.group(1))
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    if name in ("", ".", ".."):
        return ""
    return util.safe_filename(name)


def filename_from_url(url: str) -> str:
    """Last path segment of ``url`` (percent-decoded) or ``""``."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    segs = [s for s in (parts.path or "").split("/") if s]
    if not segs:
        return ""
    name = urllib.parse.unquote(segs[-1]).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name in ("", ".", ".."):
        return ""
    return util.safe_filename(name)


_EXT_OVERRIDES = {"text/html": "html", "application/xhtml+xml": "html", "image/jpeg": "jpg", "text/plain": "txt",
                  "application/pdf": "pdf", "image/svg+xml": "svg", "application/json": "json",
                  "application/zip": "zip", "audio/mpeg": "mp3", "video/mp4": "mp4", "image/webp": "webp",
                  "application/x-7z-compressed": "7z", "application/gzip": "gz", "text/csv": "csv",
                  "application/xml": "xml", "text/xml": "xml"}


def guess_extension(content_type: str | None) -> str:
    """Extension (no dot) for a ``Content-Type`` or ``""``."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not mime or mime in ("application/octet-stream", "binary/octet-stream"):
        return ""
    if mime in _EXT_OVERRIDES:
        return _EXT_OVERRIDES[mime]
    ext = mimetypes.guess_extension(mime) or ""
    return ext.lstrip(".")


def _has_known_ext(name: str) -> bool:
    _stem, ext = util.split_name(name)
    return bool(ext) and (ext in EXT_TABLE or ext in util.MULTI_EXTS or len(ext) <= 4)


def choose_name(content_disposition: str | None, final_url: str, content_type: str | None) -> str:
    """Download file name: Content-Disposition, else URL, else ``download-YYYYMMDD-HHMMSS``; the
    extension of the content type is appended when the name has none."""
    name = filename_from_content_disposition(content_disposition) or filename_from_url(final_url)
    if not name:
        name = "download-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    if not _has_known_ext(name):
        ext = guess_extension(content_type)
        if ext:
            name = f"{name}.{ext}"
    return util.safe_filename(name)


def parse_headers(text: str) -> dict[str, str]:
    """Headers of the **last** response in a curl ``--dump-header`` file (lower-case keys)."""
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    last: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        if lines and lines[0].upper().startswith(("HTTP/", "RTSP/")):
            last = lines
    headers: dict[str, str] = {}
    for line in last[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    if last:
        headers[":status"] = last[0]
    return headers


def clean_url(url: str) -> str:
    """Remove tracking parameters (utm_*, fbclid, gclid, si…) keeping the meaningful ones."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    keep_only = None
    for known, allowed in _KEEP_PARAMS_HOSTS.items():
        if host == known or host.endswith("." + known):
            keep_only = allowed
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if (k in keep_only if keep_only is not None else not _TRACKING.match(k))]
    query = urllib.parse.urlencode(kept, doseq=True)
    fragment = parts.fragment
    if fragment.startswith(":~:text="):
        fragment = ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def download_dir(config: dict | None) -> Path:
    """Where downloads go: ``output.folder`` with ``location = "folder"``, else ``$XDG_DOWNLOAD_DIR``."""
    if cfgmod.get(config, "output.location") == "folder":
        return cfgmod.output_folder(config)
    return util.download_dir()


def _target_dir(ctx: Context) -> Path:
    for cand in (download_dir(ctx.config), util.fallback_output_dir()):
        try:
            cand.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(cand, os.W_OK | os.X_OK):
            return cand
    raise UserError("err.not_writable", dir=str(download_dir(ctx.config)))


def _commit(ctx: Context, part: Path, name: str, directory: Path) -> Path:
    """Move ``part`` to a unique ``name`` in ``directory`` without clobbering anything."""
    for _ in range(50):
        out = ctx.output_path(name=name, directory=directory)
        try:
            os.link(part, out)
        except FileExistsError:
            continue
        except (OSError, NotImplementedError):
            if os.path.lexists(out):
                continue
            os.replace(part, out)
            return out
        try:
            os.unlink(part)
        except OSError:
            pass
        return out
    raise UserError("err.no_free_name", name=name)


# ================================================================================ HTTP helpers

def _urlopen(url: str, timeout: float = 20.0) -> Any:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                                               "Accept-Language": "it,en;q=0.8"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - the user's own URL
    except urllib.error.HTTPError as exc:
        raise UserError("err.web.http", details=str(exc), code=exc.code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UserError("err.network", details=str(reason)) from None


def _decode_html(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    charset = m.group(1) if m else ""
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
        charset = m.group(1).decode("ascii", "replace") if m else "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def page_title(url: str, timeout: float = 15.0, max_bytes: int = 512 * 1024) -> str:
    """``og:title`` or ``<title>`` of an HTML page (``""`` when not HTML or unreachable)."""
    try:
        resp = _urlopen(url, timeout)
    except UserError:
        return ""
    try:
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            return ""
        raw = resp.read(max_bytes)
    except OSError:
        return ""
    finally:
        try:
            resp.close()
        except OSError:
            pass
    text = _decode_html(raw, ctype)
    m = re.search(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)", text, re.I) or \
        re.search(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:title", text, re.I) or \
        re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:300]


# ================================================================================ download

def _curl_error(exc: ToolError) -> UserError:
    text = exc.details or ""
    m = re.search(r"curl: \((\d+)\)", text)
    code = int(m.group(1)) if m else None
    http = re.search(r"returned error:\s*(\d{3})", text)
    if code == 22 or http:
        return UserError("err.web.http", details=text, code=http.group(1) if http else "?")
    if code in (6, 7, 5):
        return UserError("err.network", details=text)
    if code == 28:
        return UserError("err.web.timeout", details=text)
    if code in (35, 51, 58, 59, 60, 77, 83, 90, 91):
        return UserError("err.web.tls", details=text)
    if code == 23:
        return UserError("err.disk_full", details=text)
    return exc


def _download_curl(ctx: Context, url: str, part: Path, hdr: Path, prog: Progress) -> tuple[str, dict]:
    def on_line(line: str) -> None:
        found = _PCT_RE.findall(line)
        if found:
            try:
                prog.frac(float(found[-1].replace(",", ".")) / 100.0)
            except ValueError:
                pass

    argv = ["curl", "--fail", "--location", "--max-redirs", "10", "--proto", "=http,https,ftp,ftps",
            "--proto-redir", "=http,https,ftp,ftps", "--connect-timeout", "20", "--retry", "3", "--retry-delay", "2",
            "--speed-time", "60", "--speed-limit", "1", "--compressed", "--user-agent", USER_AGENT,
            "--dump-header", str(hdr), "--output", str(part),
            "--write-out", "%{url_effective}\\n%{content_type}\\n", "--progress-bar", "--", url]
    try:
        res = ctx.run(argv, on_line=on_line, stderr_lines=True)
    except ToolError as exc:
        raise _curl_error(exc) from None
    out_lines = (res.stdout or "").splitlines()
    final = out_lines[0].strip() if out_lines else url
    try:
        headers = parse_headers(hdr.read_text(encoding="latin-1"))
    except OSError:
        headers = {}
    if len(out_lines) > 1 and out_lines[1].strip() and "content-type" not in headers:
        headers["content-type"] = out_lines[1].strip()
    return final or url, headers


def _download_python(ctx: Context, url: str, part: Path, prog: Progress) -> tuple[str, dict]:
    resp = _urlopen(url, 30)
    try:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        total = int(headers.get("content-length", "0") or 0)
        prog.total = total or None
        with open(part, "wb") as fh:
            while True:
                ctx.check_cancel()
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                prog.add(len(chunk))
        final = resp.geturl() or url
    except OSError as exc:
        if getattr(exc, "errno", None) == 28:
            raise UserError("err.disk_full", details=str(exc)) from None
        raise UserError("err.network", details=str(exc)) from None
    finally:
        resp.close()
    return final, headers


@action(id="url.download", label="act.url.download", icon="download", categories={"url"}, multi="each",
        description="desc.url.download", order=1)
def url_download(ctx: Context):
    """Download the link into the Downloads folder (curl, or Python when curl is missing)."""
    url = _url_of(ctx.file)
    target = _target_dir(ctx)
    part = target / f".dropwheel-dl-{secrets.token_hex(4)}.part"
    hdr = ctx.tmpdir() / f"headers-{secrets.token_hex(3)}.txt"
    prog = Progress(ctx, label=ctx.t("msg.url.downloading", host=_host(url)))
    prog.frac(None, force=True)
    try:
        if tools.have("curl"):
            final, headers = _download_curl(ctx, url, part, hdr, prog)
        else:
            final, headers = _download_python(ctx, url, part, prog)
        if not part.exists():
            raise UserError("err.no_output", name=filename_from_url(url) or url)
        name = choose_name(headers.get("content-disposition"), final, headers.get("content-type"))
        out = _commit(ctx, part, name, target)
    finally:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
    size = out.stat().st_size if out.exists() else 0
    if size == 0:
        ctx.warn(ctx.t("warn.url.empty"))
    return ctx.result([out], message=ctx.t("msg.url.downloaded", size=util.human_size(size, ctx.lang)))


# ================================================================================ media (yt-dlp)

#: preset → yt-dlp arguments
YTDLP_FORMATS: dict[str, list[str]] = {
    "video": ["-f", "bv*+ba/b", "-S", "res:1080,vcodec:h264,acodec:aac,ext", "--merge-output-format", "mp4",
              "--remux-video", "mp4", "--embed-metadata", "--embed-chapters"],
    "audio_mp3": ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0", "--embed-metadata",
                  "--embed-thumbnail"],
    "best": ["-f", "bv*+ba/b", "--embed-metadata", "--embed-chapters"],
    "2160": ["-f", "bv*+ba/b", "-S", "res:2160,ext", "--merge-output-format", "mp4/mkv", "--embed-metadata"],
    "1440": ["-f", "bv*+ba/b", "-S", "res:1440,ext", "--merge-output-format", "mp4/mkv", "--embed-metadata"],
    "1080": ["-f", "bv*+ba/b", "-S", "res:1080,vcodec:h264,acodec:aac,ext", "--merge-output-format", "mp4",
             "--embed-metadata"],
    "720": ["-f", "bv*+ba/b", "-S", "res:720,vcodec:h264,acodec:aac,ext", "--merge-output-format", "mp4",
            "--embed-metadata"],
    "480": ["-f", "bv*+ba/b", "-S", "res:480,vcodec:h264,acodec:aac,ext", "--merge-output-format", "mp4",
            "--embed-metadata"],
    "audio_m4a": ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a", "--embed-metadata", "--embed-thumbnail"],
    "audio_opus": ["-f", "ba/b", "-x", "--audio-format", "opus", "--embed-metadata"],
}
_YT_PROG = re.compile(r"^\[dwprog\]\s*(\d{1,3}(?:\.\d+)?)%")
_YT_STAGE = re.compile(r"^\[(Merger|ExtractAudio|VideoRemuxer|VideoConvertor|EmbedThumbnail|Metadata|"
                       r"EmbedSubtitle|FixupM3u8|FixupM4a|FixupStretched|FixupDuplicateMoov)\]")
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp", ".frag")
_THUMB_SUFFIXES = (".webp", ".jpg", ".jpeg", ".png")


def _ytdlp_error(exc: ToolError) -> UserError:
    text = exc.details or ""
    low = text.lower()
    if "unsupported url" in low:
        return UserError("err.web.unsupported", details=text)
    if "javascript runtime" in low or "jsinterp" in low or "n challenge" in low or "signature extraction" in low \
            or "deno" in low:
        return UserError("err.web.js_runtime", details=text, install=tools.install_hint(["deno"]))
    if "sign in" in low or "login" in low or "cookies" in low or "age-restricted" in low:
        return UserError("err.web.login", details=text)
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return MissingTool("ffmpeg", details=text)
    if "unavailable" in low or "private video" in low or "removed" in low or "http error 404" in low:
        return UserError("err.web.unavailable", details=text)
    if "unable to download" in low or "getaddrinfo" in low or "timed out" in low or "network" in low:
        return UserError("err.network", details=text)
    return exc


def _run_ytdlp(ctx: Context, url: str, preset: str, *, subtitles: bool = False, thumbnail: bool = False,
               playlist: bool = False) -> list[Path]:
    target = _target_dir(ctx)
    staging = target / f".dropwheel-media-{secrets.token_hex(4)}"
    staging.mkdir()
    args = list(YTDLP_FORMATS.get(preset, YTDLP_FORMATS["video"]))
    if thumbnail and "--embed-thumbnail" not in args:
        args.append("--embed-thumbnail")
    if subtitles:
        args += ["--write-subs", "--sub-langs", "it.*,en.*", "--embed-subs"]
    if util.needs_portable_names(target):
        args.append("--windows-filenames")
    argv = ["yt-dlp", "--newline", "--no-colors", "--no-mtime", "--no-part", "--no-cache-dir",
            "--yes-playlist" if playlist else "--no-playlist",
            "--progress-template", "download:[dwprog] %(progress._percent_str)s",
            "--paths", f"home:{staging}", "--paths", f"temp:{staging}",
            "-o", "%(title).150B [%(id)s].%(ext)s", *args, "--", url]
    label = ctx.t("msg.url.media", host=_host(url))
    prog = Progress(ctx, label=label)
    parts = {"n": 0}

    def on_line(line: str) -> None:
        text = line.strip()
        m = _YT_PROG.match(text)
        if m:
            try:
                prog.frac(float(m.group(1)) / 100.0)
            except ValueError:
                pass
            return
        if text.startswith("[download] Destination:"):
            parts["n"] += 1
            prog.frac(0.0, f"{label} · {parts['n']}" if parts["n"] > 1 else label, force=True)
        elif _YT_STAGE.match(text):
            prog.frac(None, ctx.t("msg.url.processing"), force=True)

    try:
        try:
            ctx.run(argv, on_line=on_line)
        except ToolError as exc:
            raise _ytdlp_error(exc) from None
        files = [e for e in sorted(staging.iterdir(), key=lambda p: util.natural_key(p.name))
                 if e.is_file() and not e.name.lower().endswith(_PARTIAL_SUFFIXES)]
        media = [e for e in files if e.suffix.lower() not in _THUMB_SUFFIXES]
        produced = media or files
        if not produced:
            raise UserError("err.no_output", name=url[:200])
        outputs = [_commit(ctx, p, p.name, target) for p in produced]
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return outputs


def _media(ctx: Context, preset: str):
    url = _url_of(ctx.file)
    outs = _run_ytdlp(ctx, url, preset)
    return ctx.result(outs, message=i18n.tn("msg.url.media_done", len(outs), ctx.lang))


@action(id="url.media.video", label="act.url.video", icon="movie", categories={"url"}, multi="each",
        requires=["yt-dlp", "ffmpeg"], description="desc.url.video", order=1)
def url_media_video(ctx: Context):
    """Video up to 1080p as MP4 (H.264/AAC preferred) with yt-dlp."""
    return _media(ctx, "video")


@action(id="url.media.audio", label="act.url.audio", icon="music_note", categories={"url"}, multi="each",
        requires=["yt-dlp", "ffmpeg"], description="desc.url.audio", order=2)
def url_media_audio(ctx: Context):
    """Audio only, as MP3 (best quality, cover and tags embedded)."""
    return _media(ctx, "audio_mp3")


@action(id="url.media.choose", label="act.url.choose", icon="tune", categories={"url"}, multi="each",
        mode="dialog", dialog="form", requires=["yt-dlp", "ffmpeg"], description="desc.url.choose", order=3,
        params=[
            P.select("quality", "prm.url.quality", options=[
                ("best", "opt.url.q.best"), ("2160", "2160p (4K)"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p"),
                ("480", "480p"), ("audio_mp3", "opt.url.q.mp3"), ("audio_m4a", "opt.url.q.m4a"),
                ("audio_opus", "opt.url.q.opus")], default="1080"),
            P.bool("subtitles", "prm.url.subtitles", False),
            P.bool("thumbnail", "prm.url.thumbnail", False),
            P.bool("playlist", "prm.url.playlist", False, help="prm.url.playlist.help"),
        ])
def url_media_choose(ctx: Context):
    """yt-dlp download with a chosen quality, subtitles, cover and playlist options."""
    url = _url_of(ctx.file)
    outs = _run_ytdlp(ctx, url, ctx.pstr("quality", "1080"), subtitles=ctx.pbool("subtitles"),
                      thumbnail=ctx.pbool("thumbnail"), playlist=ctx.pbool("playlist"))
    return ctx.result(outs, message=i18n.tn("msg.url.media_done", len(outs), ctx.lang))


# ================================================================================ QR

def _qr_cache(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    return util.cache_dir() / "qr" / f"{digest}.png"


def _make_qr(url: str, out: Path) -> Path:
    from .. import share

    made = share.make_qr(url, out)
    if not made:
        raise MissingTool("py:qrcode")
    return Path(made)


@action(id="url.qr.show", label="act.url.qr", icon="qr_code_2", categories={"url"}, multi="single",
        requires=[("py:qrcode", "qrencode")], undoable=False, description="desc.url.qr", order=1)
def url_qr_show(ctx: Context):
    """Show a QR code of the link (scan it with the phone)."""
    url = _url_of(ctx.file)
    path = _qr_cache(url)
    if not path.exists():
        _make_qr(url, path)
    return ctx.result(kind="qr", text=url, url=url, qr=str(path), message=ctx.t("msg.url.qr"))


@action(id="url.qr.save", label="act.url.qr_save", icon="qr_code", categories={"url"}, multi="each",
        requires=[("py:qrcode", "qrencode")], description="desc.url.qr_save", order=2)
def url_qr_save(ctx: Context):
    """Save the QR code as a PNG in the Downloads folder."""
    url = _url_of(ctx.file)
    target = _target_dir(ctx)
    host = _host(url) or "link"
    out = ctx.output_path(name=ctx.t("msg.url.qr_name", host=host), ext="png", directory=target)
    with atomic_target(out) as part:
        _make_qr(url, part)
    return ctx.result([out])


# ================================================================================ copy

def _copy(ctx: Context, lines: list[str]):
    text = "\n".join(lines)
    copied = util.copy_text(text)
    return ctx.result(kind="text", text=text, message=ctx.t("msg.url.copied" if copied else "msg.file.path_shown"))


@action(id="url.copy.link", label="act.url.copy_link", icon="link", categories={"url"}, multi="any",
        undoable=False, description="desc.url.copy_link", order=1)
def url_copy_link(ctx: Context):
    """Copy the link(s) as text."""
    return _copy(ctx, [_url_of(f) for f in ctx.files])


@action(id="url.copy.markdown", label="act.url.copy_md", icon="markdown", categories={"url"}, multi="any",
        undoable=False, description="desc.url.copy_md", order=2)
def url_copy_markdown(ctx: Context):
    """Copy ``[page title](link)`` (the title is read from the page itself)."""
    lines = []
    prog = Progress(ctx, total=len(ctx.files), label=ctx.t("act.url.copy_md"))
    for f in ctx.files:
        ctx.check_cancel()
        url = _url_of(f)
        title = page_title(url) or _host(url) or url
        title = title.replace("[", "\\[").replace("]", "\\]")
        prefix = "- " if len(ctx.files) > 1 else ""
        lines.append(f"{prefix}[{title}]({url.replace(')', '%29').replace(' ', '%20')})")
        prog.add(1)
    return _copy(ctx, lines)


@action(id="url.copy.clean", label="act.url.copy_clean", icon="cleaning_services", categories={"url"},
        multi="any", undoable=False, description="desc.url.copy_clean", order=3)
def url_copy_clean(ctx: Context):
    """Copy the link without tracking parameters (utm_*, fbclid, gclid, si…)."""
    return _copy(ctx, [clean_url(_url_of(f)) for f in ctx.files])


@action(id="url.save_list", label="act.url.save_list", icon="list_alt", categories={"url"}, multi="combine",
        description="desc.url.save_list", order=60)
def url_save_list(ctx: Context):
    """Save the dropped links in a text file (one per line)."""
    urls = [_url_of(f) for f in ctx.files]
    name = ctx.t("msg.url.list_name", date=datetime.now().strftime("%Y-%m-%d %H.%M"))
    out = ctx.output_path(name=name, ext="txt", directory=_target_dir(ctx))
    with atomic_target(out) as part:
        part.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return ctx.result([out], message=i18n.tn("msg.url.saved_list", len(urls), ctx.lang))


# ================================================================================ open / shortcut / pdf

@action(id="url.open", label="act.url.open", icon="open_in_browser", categories={"url"}, multi="any",
        requires=[("xdg-open", "gio")], undoable=False, description="desc.url.open", order=40)
def url_open(ctx: Context):
    """Open the link(s) in the default browser."""
    urls = [_url_of(f) for f in ctx.files][:20]
    failed = [u for u in urls if not util.open_path(u)]
    if failed:
        raise UserError("err.open_failed", name=failed[0][:200])
    return ctx.result(kind="none", message=ctx.t("msg.file.opened"))


def _shortcut_text(fmt: str, url: str, title: str) -> str:
    if fmt == "url":
        return f"[InternetShortcut]\r\nURL={url}\r\n"
    if fmt == "html":
        esc = html.escape(url, quote=True)
        return ('<!doctype html>\n<html><head><meta charset="utf-8">\n'
                f'<meta http-equiv="refresh" content="0; url={esc}">\n<title>{html.escape(title)}</title></head>\n'
                f'<body><a href="{esc}">{html.escape(title)}</a></body></html>\n')
    safe_title = title.replace("\n", " ").replace("\r", " ")
    return ("[Desktop Entry]\nVersion=1.0\nType=Link\n"
            f"Name={safe_title}\nURL={url}\nIcon=text-html\n")


@action(id="url.shortcut", label="act.url.shortcut", icon="bookmark_add", categories={"url"}, multi="each",
        mode="quick", description="desc.url.shortcut", order=50,
        params=[P.select("format", "prm.url.shortcut_format", options=[
            ("desktop", "opt.url.sc.desktop"), ("url", "opt.url.sc.url"), ("html", "opt.url.sc.html")],
            default="desktop")])
def url_shortcut(ctx: Context):
    """Save a link file that opens the page (``.desktop``, Windows ``.url`` or an HTML redirect)."""
    url = _url_of(ctx.file)
    fmt = ctx.pstr("format", "desktop")
    ctx.progress(None, ctx.t("act.url.shortcut"))
    title = page_title(url) or _host(url) or "link"
    name = util.safe_filename(title)[:120].strip(" .") or "link"
    out = ctx.output_path(name=name, ext=fmt if fmt != "desktop" else "desktop", directory=_target_dir(ctx))
    with atomic_target(out) as part:
        part.write_text(_shortcut_text(fmt, url, title), encoding="utf-8", newline="")
    return ctx.result([out])


BROWSERS = ("chromium", "google-chrome-stable", "google-chrome", "brave", "brave-browser", "microsoft-edge-stable",
            "vivaldi-stable", "thorium-browser")


def _browser() -> str | None:
    for name in BROWSERS:
        if tools.have(name):
            return name
    return None


@action(id="url.pdf", label="act.url.pdf", icon="picture_as_pdf", categories={"url"}, multi="each",
        requires=[BROWSERS], description="desc.url.pdf", order=45)
def url_pdf(ctx: Context):
    """Print the page to PDF with headless Chromium (private, throw-away profile)."""
    url = _url_of(ctx.file)
    browser = _browser()
    if browser is None:
        raise MissingTool("chromium")
    ctx.progress(None, ctx.t("msg.url.rendering", host=_host(url)))
    title = page_title(url) or ctx.file.stem or _host(url) or "page"
    name = util.safe_filename(title)[:150].strip(" .") or "page"
    out = ctx.output_path(name=name, ext="pdf", directory=_target_dir(ctx))
    profile = ctx.tmpdir() / "chromium-profile"
    profile.mkdir(parents=True, exist_ok=True)
    with atomic_target(out) as part:
        argv = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--hide-scrollbars", f"--user-data-dir={profile}",
                "--no-pdf-header-footer", "--virtual-time-budget=15000", "--run-all-compositor-stages-before-draw",
                f"--print-to-pdf={part}"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            argv.append("--no-sandbox")
        argv.append(url)
        try:
            ctx.run(argv, timeout=180)
        except ToolError as exc:
            raise UserError("err.web.pdf_failed", details=exc.details) from None
        if not part.exists() or part.stat().st_size == 0:
            raise UserError("err.web.pdf_failed")
    return ctx.result([out])


# =================================================================================== layout

group("url.media", "grp.media", "video_library", ["url.media.video", "url.media.audio", "url.media.choose"],
      default="url.media.video")
group("url.qr", "grp.qr", "qr_code_2", ["url.qr.show", "url.qr.save"], default="url.qr.show")
group("url.copy", "grp.copy", "content_copy", ["url.copy.link", "url.copy.markdown", "url.copy.clean"],
      default="url.copy.link")

layout("url", ["url.download", "url.media", "url.qr", "url.copy", "url.open", "url.pdf", "url.shortcut"])
layout("multi:url", ["url.download", "url.media", "url.copy", "url.save_list", "url.open", "url.qr", "url.pdf"])
