"""Video and audio actions (categories ``video`` and ``audio``) plus media combinations.

Video ring: Convert → MP4★/WebM/MKV/MOV/GIF/MP3/M4A/(OPUS/FLAC/WAV) · Compress → Balanced★/High/Small/
WhatsApp-Telegram/Target size… · Trim… · Crop… · Resize → 1080★/720/480/1440/2160 · Edit → (Mute, Rotate→,
Flip→, Speed→, Reverse, Loop…, Stabilize, Fade) · Extract → (Audio track★, Snapshot, Frames…, Contact sheet,
Subtitles) · More → (Subtitles → burn/embed/generate, Split…, Metadata → edit/strip/GPS).

Audio ring: Convert → MP3★/M4A/OPUS/OGG/FLAC/WAV/AIFF/M4R · Compress → · Trim… · Normalize → −14★/−16/−23 LUFS ·
Tags… · Transcribe → TXT★/SRT/VTT · Edit → (Speed→, Fade, Remove silence, Noise reduction, Volume→,
Channels→, Reverse) · More → (Visualize→, Extract cover).

Multi: videos → Join★ (concat demuxer when the streams match, else concat filter scaled to the first clip),
Convert/Compress/Extract audio/Mute each…; audio → Join★, Convert/Normalize/Compress each, Playlist M3U;
mixed: image + audio → still-image MP4, audio + video → MP3 each.

ffmpeg 9 rules followed everywhere: ``-fps_mode`` (never ``-vsync``), even dimensions, ``yuv420p`` for
H.264, ``-movflags +faststart``, HDR (PQ/HLG) kept as 10-bit HEVC/VP9/AV1 or tone-mapped with ``zscale``
for H.264/GIF/frames (with a warning), VFR passthrough, audio stream-copied when the container allows it,
VA-API/NVENC encoding for H.264/HEVC when ``video.hwaccel`` allows it (probed once, silently falling back to
the CPU), file names passed as ``file:<absolute path>`` (spaces, unicode, leading dashes are safe) and
helper files (concat lists, subtitles, two-pass logs) addressed relative to the job temp dir.
"""

from __future__ import annotations

import base64
import math
import dataclasses
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import config as cfgmod
from .. import i18n, tools, util
from ..detect import FileInfo
from ..errors import Cancelled, MissingTool, UserError
from ..jobs import Context, Result, atomic_target
from ..registry import GroupSpec, P, action, group, layout
from . import _media_util as mu

__all__ = ["VEnc", "media_info", "run_ffmpeg"]

FF = ["ffmpeg", "ffprobe"]
VAAPI_DEVICE = "/dev/dri/renderD128"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
MP4_FAMILY = frozenset({"mp4", "m4v", "mov", "qt", "3gp", "3g2"})
EDIT_CONTAINERS = frozenset({"mp4", "m4v", "mov", "mkv", "webm"})
RINGTONE_MAX = 40.0
_MB = 1_000_000


# ======================================================================================== selection

def _audio_files_ok(files: Sequence[FileInfo]) -> bool:
    """Real audio (no playlists / cue sheets / MIDI)."""
    return all(f.ext not in mu.PLAYLIST_EXTS and f.ext not in mu.UNDECODABLE_EXTS for f in files)


def _not_ext(*exts: str) -> Callable[[list[FileInfo]], bool]:
    """``when``: at least one file is not already in one of ``exts`` (and is decodable media)."""
    banned = frozenset(exts)

    def when(files: list[FileInfo]) -> bool:
        return _audio_files_ok(files) and any(f.ext not in banned for f in files)

    return when


def _mp4_family(files: list[FileInfo]) -> bool:
    return all(f.ext in MP4_FAMILY for f in files)


def _has_sidecar(files: list[FileInfo]) -> bool:
    if len(files) > 20:
        return True
    return any(f.path and mu.sidecar_subtitles(f.path) for f in files)


def _mixed_audio_video(files: list[FileInfo]) -> bool:
    cats = {f.category for f in files}
    return len(files) >= 2 and cats == {"audio", "video"} and _audio_files_ok([f for f in files
                                                                              if f.category == "audio"])


def _image_plus_audio(files: list[FileInfo]) -> bool:
    if len(files) != 2:
        return False
    images = [f for f in files if f.category == "image"]
    audios = [f for f in files if f.category == "audio"]
    return len(images) == 1 and len(audios) == 1 and _audio_files_ok(audios)


# ===================================================================================== capabilities

_CAPS_LOCK = threading.Lock()
_CAPS: dict[tuple[str, str], frozenset[str] | None] = {}
_HW_STATE: dict[tuple[str, str], bool] = {}
_HW_FAILURES: dict[tuple[str, str], int] = {}
_LINE_RE = re.compile(r"^\s*([A-Z.|]{3,6})\s+([A-Za-z0-9_.-]+)\s")


def _ff_names(ctx: Context, kind: str) -> frozenset[str] | None:
    """Encoders (``kind="encoders"``) or filters (``"filters"``) of the installed ffmpeg; ``None`` if unknown."""
    exe = tools.path("ffmpeg") or "ffmpeg"
    key = (exe, kind)
    with _CAPS_LOCK:
        if key in _CAPS:
            return _CAPS[key]
    names: frozenset[str] | None
    try:
        res = ctx.run(["ffmpeg", "-hide_banner", "-nostdin", f"-{kind}"], timeout=30)
    except Cancelled:
        raise
    except UserError as exc:
        ctx.log(f"ffmpeg -{kind} failed: {exc}")
        names = None
    else:
        # "-encoders" has a "------" separator after the legend, "-filters" has none: skip the
        # legend lines ("V..... = Video", "T.. = Timeline support") instead
        found = set()
        for line in (res.stdout or "").splitlines():
            if " = " in line or line.strip().startswith("---"):
                continue
            m = _LINE_RE.match(line)
            if m:
                found.add(m.group(2))
        names = frozenset(found) if found else None
    with _CAPS_LOCK:
        _CAPS[key] = names
    return names


def _have_encoder(ctx: Context, name: str) -> bool:
    names = _ff_names(ctx, "encoders")
    return True if names is None else name in names


def _have_filter(ctx: Context, name: str) -> bool:
    names = _ff_names(ctx, "filters")
    return True if names is None else name in names


def _encoder(ctx: Context, *candidates: str) -> str:
    """First available encoder among ``candidates``; raises a readable error otherwise."""
    for name in candidates:
        if _have_encoder(ctx, name):
            return name
    raise UserError("media.err.encoder", details=ctx.t("media.err.encoder_help"), encoder=candidates[0])


def _need_filter(ctx: Context, name: str) -> None:
    if not _have_filter(ctx, name):
        raise UserError("media.err.filter", details=ctx.t("media.err.encoder_help"), filter=name)


def _hw_probe(ctx: Context, mode: str, codec: str) -> bool:
    """Encode a tiny synthetic clip with the hardware encoder (cached per mode/codec)."""
    key = (mode, codec)
    with _CAPS_LOCK:
        if key in _HW_STATE:
            return _HW_STATE[key]
    ok = False
    if mode == "vaapi":
        if os.name == "posix" and os.path.exists(VAAPI_DEVICE) and os.access(VAAPI_DEVICE, os.R_OK | os.W_OK):
            argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-init_hw_device",
                    f"vaapi=dw:{VAAPI_DEVICE}", "-filter_hw_device", "dw", "-f", "lavfi", "-i",
                    "color=c=black:s=320x240:r=25:d=0.2", "-vf", "format=nv12,hwupload",
                    "-c:v", f"{codec}_vaapi", "-frames:v", "3", "-f", "null", "-"]
        else:
            argv = []
    else:
        argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                "color=c=black:s=320x240:r=25:d=0.2", "-c:v", f"{codec}_nvenc", "-frames:v", "3", "-f", "null",
                "-"]
    if argv:
        try:
            ctx.run(argv, timeout=20)
            ok = True
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"{mode} {codec} probe failed: {exc} {exc.details}")
            ok = False
    with _CAPS_LOCK:
        _HW_STATE[key] = ok
    return ok


def _hw_for(ctx: Context, codec: str) -> str | None:
    """Hardware encoder family to try for ``codec`` (``"vaapi"``/``"nvenc"``) or ``None``."""
    if codec not in ("h264", "hevc"):
        return None
    mode = str(cfgmod.get(ctx.config, "video.hwaccel", "auto") or "auto").lower()
    if mode == "none":
        return None
    family = "nvenc" if mode == "nvenc" else "vaapi"
    with _CAPS_LOCK:
        if _HW_FAILURES.get((family, codec), 0) >= 2:
            return None
    return family if _hw_probe(ctx, family, codec) else None


def _hw_failed(family: str, codec: str) -> None:
    with _CAPS_LOCK:
        _HW_FAILURES[(family, codec)] = _HW_FAILURES.get((family, codec), 0) + 1


def _hw_succeeded(family: str, codec: str) -> None:
    with _CAPS_LOCK:
        _HW_FAILURES.pop((family, codec), None)


# ======================================================================================== encoders

def _cfg_crf(ctx: Context) -> int:
    try:
        return max(0, min(51, int(cfgmod.get(ctx.config, "video.crf", 23))))
    except (TypeError, ValueError):
        return 23


def _cfg_preset(ctx: Context) -> str:
    value = str(cfgmod.get(ctx.config, "video.preset", "medium") or "medium")
    return value if re.match(r"^[a-z]+$", value) else "medium"


@dataclass
class VEnc:
    """A video encoder choice: codec, quality, optional hardware path."""

    codec: str                    #: h264 | hevc | vp9 | av1
    crf: int = 23
    preset: str = "medium"
    hw: str | None = None         #: None | "vaapi" | "nvenc"
    ten_bit: bool = False
    vs: mu.VStream | None = None
    bitrate: int | None = None    #: kbit/s (ABR) instead of CRF
    encoder: str | None = None    #: software encoder override (AV1 fallbacks)

    def input_args(self) -> list[str]:
        """Options placed before the first ``-i``."""
        if self.hw == "vaapi":
            return ["-init_hw_device", f"vaapi=dw:{VAAPI_DEVICE}", "-filter_hw_device", "dw"]
        return []

    def chain(self, filters: Sequence[str]) -> str | None:
        """Comma-joined video filter chain including the hardware upload tail (``None`` when empty)."""
        parts = [f for f in filters if f]
        if self.hw == "vaapi":
            parts += ["format=nv12", "hwupload"]
        return ",".join(parts) if parts else None

    def vf(self, filters: Sequence[str]) -> list[str]:
        """``["-vf", chain]`` or ``[]``."""
        chain = self.chain(filters)
        return ["-vf", chain] if chain else []

    def args(self) -> list[str]:
        """Encoder options (after the inputs)."""
        if self.hw == "vaapi":
            qp = self.crf + 1 if self.codec == "h264" else self.crf
            rate = (["-b:v", f"{self.bitrate}k", "-maxrate", f"{int(self.bitrate * 1.5)}k"] if self.bitrate
                    else ["-qp", str(max(1, min(51, qp)))])
            tag = ["-tag:v", "hvc1"] if self.codec == "hevc" else []
            return ["-c:v", f"{self.codec}_vaapi", *rate, *tag]
        if self.hw == "nvenc":
            rate = (["-rc", "vbr", "-b:v", f"{self.bitrate}k"] if self.bitrate
                    else ["-rc", "vbr", "-cq", str(self.crf), "-b:v", "0"])
            tag = ["-tag:v", "hvc1"] if self.codec == "hevc" else []
            return ["-c:v", f"{self.codec}_nvenc", "-preset", "p5", "-tune", "hq", *rate, "-pix_fmt", "yuv420p", *tag]
        args = mu.video_codec_args(self.codec, self.crf, self.preset, ten_bit=self.ten_bit, vs=self.vs,
                                   encoder=self.encoder)
        if self.bitrate:
            out: list[str] = []
            skip = False
            for i, a in enumerate(args):
                if skip:
                    skip = False
                    continue
                if a in ("-crf", "-qp"):
                    skip = True
                    continue
                if a == "-b:v":
                    skip = True
                    continue
                out.append(a)
            return out + ["-b:v", f"{self.bitrate}k"]
        return args

    def software(self) -> "VEnc":
        """Same choice without hardware encoding."""
        return dataclasses.replace(self, hw=None)


def _make_venc(ctx: Context, codec: str, vs: mu.VStream | None, *, crf: int | None = None, ten_bit: bool = False,
               bitrate: int | None = None, allow_hw: bool = True) -> VEnc:
    """Pick encoder implementation (checks availability, AV1 fallbacks, hardware when allowed)."""
    preset = _cfg_preset(ctx)
    encoder: str | None = None
    if codec == "h264":
        encoder = _encoder(ctx, "libx264")
        crf = _cfg_crf(ctx) if crf is None else crf
    elif codec == "hevc":
        encoder = _encoder(ctx, "libx265")
        crf = (_cfg_crf(ctx) + 5) if crf is None else crf
    elif codec == "vp9":
        encoder = _encoder(ctx, "libvpx-vp9")
        crf = 32 if crf is None else crf
    elif codec == "av1":
        encoder = _encoder(ctx, "libsvtav1", "libaom-av1", "librav1e")
        crf = 35 if crf is None else crf
    else:
        raise ValueError(codec)
    hw = _hw_for(ctx, codec) if allow_hw and not ten_bit else None
    return VEnc(codec=codec, crf=int(crf), preset=preset, hw=hw, ten_bit=ten_bit, vs=vs, bitrate=bitrate,
                encoder=encoder)


# ======================================================================================== running

def _fp(path: str | os.PathLike) -> str:
    """ffmpeg file argument: ``file:`` + absolute path (no protocol/option ambiguity)."""
    return "file:" + os.path.abspath(os.fspath(path))


def run_ffmpeg(ctx: Context, args: Sequence[Any], *, duration: float | None = None, label: str | None = None,
               span: tuple[float, float] = (0.0, 1.0), cwd: str | os.PathLike | None = None,
               loglevel: str = "error", raw: bool = False) -> subprocess.CompletedProcess:
    """Run ffmpeg with progress.

    The plain case goes through :meth:`Context.ffmpeg`; a progress ``span`` (multi-pass jobs), a
    working directory (relative helper files), another ``loglevel`` (filters printing their results)
    or ``raw=True`` use the same prefix and parser here, mapping progress into ``span``.
    """
    str_args = [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in args]
    if span == (0.0, 1.0) and cwd is None and loglevel == "error" and not raw:
        return ctx.ffmpeg(str_args, duration=duration, label=label)
    lo, hi = span
    started = time.monotonic()
    if duration or span != (0.0, 1.0):
        ctx.progress(lo, label)
    else:
        ctx.progress(None, label)

    def on_line(line: str) -> None:
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in ("out_time_us", "out_time_ms") and duration:
            if not value or value == "N/A":
                return
            try:
                seconds = max(0, int(value)) / 1_000_000.0
            except ValueError:
                return
            frac = min(1.0, seconds / duration)
            message = label
            elapsed = time.monotonic() - started
            if frac > 0.02 and elapsed > 2:
                left = elapsed * (1 - frac) / frac
                eta = ctx.t("job.eta", time=util.human_duration(left))
                message = f"{label} · {eta}" if label else eta
            ctx.progress(lo + (hi - lo) * frac, message)
        elif key == "progress" and value == "end" and duration:
            ctx.progress(hi, label)

    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel, "-progress", "pipe:1", "-nostats",
            *str_args]
    return ctx.run(argv, on_line=on_line, cwd=cwd)


def _encode(ctx: Context, enc: VEnc, build: Callable[[VEnc], list[str]], *, duration: float | None,
            label: str | None = None, span: tuple[float, float] = (0.0, 1.0), cwd: str | os.PathLike | None = None,
            raw: bool = False) -> VEnc:
    """Run ``build(enc)``; when a hardware encoder fails, retry once in software (never fails because of it)."""
    if enc.hw:
        try:
            run_ffmpeg(ctx, build(enc), duration=duration, label=label, span=span, cwd=cwd, raw=raw)
            _hw_succeeded(enc.hw, enc.codec)
            return enc
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"hardware encoder {enc.hw} failed: {exc.details[-800:]}")
            _hw_failed(enc.hw, enc.codec)
            ctx.warn(ctx.t("media.warn.hw_fallback"))
            enc = enc.software()
    run_ffmpeg(ctx, build(enc), duration=duration, label=label, span=span, cwd=cwd, raw=raw)
    return enc


def media_info(ctx: Context, f: FileInfo | str | os.PathLike) -> mu.MediaInfo:
    """Probe a file (cached by the runner) and parse it; unreadable media → readable :class:`UserError`."""
    path = f.path if isinstance(f, FileInfo) else os.fspath(f)
    name = f.name if isinstance(f, FileInfo) else os.path.basename(path)
    try:
        data = ctx.probe(path)
    except MissingTool:
        raise
    except Cancelled:
        raise
    except UserError as exc:
        raise UserError("media.err.unreadable", details=exc.details or exc.text(ctx.lang), name=name) from None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    info = mu.parse_probe(data, size)
    if not info.videos and not info.audios:
        raise UserError("media.err.unreadable", details=ctx.t("media.err.no_streams"), name=name)
    return info


def _need_video(ctx: Context, f: FileInfo, info: mu.MediaInfo) -> mu.VStream:
    if info.video is None:
        raise UserError("media.err.no_video", name=f.name)
    return info.video


def _need_audio(ctx: Context, f: FileInfo, info: mu.MediaInfo) -> mu.AStream:
    if not info.audios:
        raise UserError("media.err.no_audio", name=f.name)
    return info.audios[0]


def _need_duration(ctx: Context, f: FileInfo, info: mu.MediaInfo) -> float:
    if not info.duration or info.duration <= 0:
        raise UserError("media.err.duration", name=f.name)
    return float(info.duration)


def _sdr_filters(ctx: Context, vs: mu.VStream | None) -> list[str]:
    """Tone-mapping filters for an HDR source going to an 8-bit SDR output (warns once)."""
    if vs is None or not vs.hdr:
        return []
    if _have_filter(ctx, "zscale") and _have_filter(ctx, "tonemap"):
        ctx.warn(ctx.t("media.warn.hdr_tonemap"))
        return [mu.TONEMAP]
    ctx.warn(ctx.t("media.warn.hdr_no_tonemap"))
    return ["format=yuv420p"]


def _fix_filters(vs: mu.VStream | None, *, deinterlace: bool = True) -> list[str]:
    """Deinterlace interlaced sources, square pixels and even dimensions."""
    if vs is None:
        return []
    out: list[str] = []
    if deinterlace and vs.interlaced:
        out.append("bwdif=mode=send_frame")
    w, h = vs.display_size
    if (vs.sar and abs(vs.sar - 1.0) > 0.01) or (w % 2) or (h % 2):
        out += ["scale=trunc(iw*sar/2)*2:trunc(ih/2)*2", "setsar=1"]
    return out


def _main_maps(info: mu.MediaInfo, *, audio: bool = True, first_audio_only: bool = False) -> list[str]:
    """``-map`` options for the main video stream and the audio streams (never cover art or data)."""
    args: list[str] = []
    if info.video is not None:
        args += ["-map", f"0:{info.video.index}"]
    if audio:
        audios = info.audios[:1] if first_audio_only else info.audios
        for a in audios:
            args += ["-map", f"0:{a.index}"]
    return args


def _sub_args(ctx: Context, info: mu.MediaInfo, container: str, *, first_out: int = 0, warn: bool = True) -> list[str]:
    """Map and convert subtitle streams for ``container``; unsupported ones are dropped with a warning."""
    args: list[str] = []
    n = first_out
    dropped = False
    for s in info.subs:
        codec = mu.subtitle_codec_for(container, s)
        if codec is None:
            dropped = True
            continue
        args += ["-map", f"0:{s.index}", f"-c:s:{n}", codec]
        n += 1
    if dropped and warn:
        ctx.warn(ctx.t("media.warn.subs_dropped", fmt=mu.container_of(container).upper()))
    return args


def _audio_codec_args(ctx: Context, info: mu.MediaInfo, container: str, *, reencode: bool = False,
                      kbps: int | None = None, first_only: bool = False, stereo: bool = False) -> list[str]:
    """Per-stream audio codec options: copy when the container allows it, else AAC (Opus for WebM).

    ``kbps`` is the rate of a stereo pair (a track above it by 15 % is re-encoded): surround AAC tracks get
    the same rate per channel (:func:`mu.aac_kbps`), WebM and ``stereo`` downmix them to two channels."""
    audios = info.audios[:1] if first_only else info.audios
    webm = mu.container_of(container) == "webm"
    out: list[str] = []
    for i, a in enumerate(audios):
        surround = a.channels > 2
        downmix = surround and (webm or stereo)
        base = kbps or (128 if webm else 160)
        rate = base if downmix or webm else mu.aac_kbps(base, a.channels)
        too_big = bool(kbps and a.bit_rate and a.bit_rate > rate * 1000 * 1.15)
        if not reencode and not too_big and not (stereo and surround) and mu.can_copy_audio(container, a.codec):
            out += [f"-c:a:{i}", "copy"]
            continue
        if webm:
            out += [f"-c:a:{i}", _encoder(ctx, "libopus", "libvorbis"), f"-b:a:{i}", f"{rate}k"]
        else:
            out += [f"-c:a:{i}", "aac", f"-b:a:{i}", f"{rate}k"]
        if downmix:
            out += [f"-ac:a:{i}", "2"]
    return out


def _mux_args(container: str) -> list[str]:
    """Container options (faststart for MP4/MOV)."""
    if mu.container_of(container) in ("mp4", "mov"):
        return ["-movflags", "+faststart"]
    return []


#: extensions ffmpeg maps to a muxer by itself (stream copies keep the source container)
_MUXABLE_EXTS = frozenset({"mp4", "m4v", "mov", "mkv", "webm", "avi", "ts", "m2ts", "mts", "m2t", "flv", "wmv",
                           "asf", "mpg", "mpeg", "vob", "3gp", "3g2", "ogv", "f4v", "mxf", "dv"})
#: extensions whose muxer has to be named explicitly
_MUXER_ALIASES = {"qt": "mov", "divx": "avi", "mk3d": "matroska"}


def _copy_container(f: FileInfo) -> tuple[str, list[str]]:
    """Output extension and ``-f`` options for a stream copy that keeps the source container.

    Containers ffmpeg cannot write (RealMedia, unknown extensions…) become Matroska, which
    accepts every codec.
    """
    ext = (f.ext or "").lower()
    if ext in _MUXABLE_EXTS:
        return ext, []
    if ext in _MUXER_ALIASES:
        return ext, ["-f", _MUXER_ALIASES[ext]]
    return "mkv", []


def _edit_target(ctx: Context, f: FileInfo, info: mu.MediaInfo, *, sdr: bool = False,
                 force_mp4: bool = False) -> tuple[str, VEnc, list[str]]:
    """Output extension, encoder and colour filters for a re-encoding edit (same container when possible).

    HDR sources stay 10-bit HEVC (VP9 in WebM) unless ``sdr`` asks for tone mapping to H.264.
    """
    vs = info.video
    ext = f.ext if f.ext in EDIT_CONTAINERS and not force_mp4 else "mp4"
    hdr = bool(vs and vs.hdr)
    if ext == "webm":
        ten = hdr and not sdr
        enc = _make_venc(ctx, "vp9", vs, ten_bit=ten)
        return ext, enc, ([] if ten else _sdr_filters(ctx, vs))
    if hdr and not sdr and _have_encoder(ctx, "libx265"):
        return ext, _make_venc(ctx, "hevc", vs, ten_bit=True), []
    enc = _make_venc(ctx, "h264", vs)
    return ext, enc, _sdr_filters(ctx, vs)


def _link_or_copy(src: str | os.PathLike, dst: Path) -> Path:
    """Expose ``src`` under a safe name in the temp dir (symlink, hard link or copy)."""
    for fn in (os.symlink, os.link):
        try:
            fn(os.path.abspath(os.fspath(src)), dst)
            return dst
        except (OSError, NotImplementedError, AttributeError):
            continue
    shutil.copy2(os.fspath(src), dst)
    return dst


def _parse_metadata_param(value: Any) -> tuple[dict[str, str], list[str], bool]:
    """``metadata: {set, remove, remove_all}`` (SPEC §5.6) → normalised pieces."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value) if value.strip() else {}
        except ValueError:
            value = {}
    if not isinstance(value, dict):
        value = {}
    sets_raw = value.get("set") or {}
    sets = {str(k): "" if v is None else str(v) for k, v in sets_raw.items()} if isinstance(sets_raw, dict) else {}
    removes = [str(k) for k in value.get("remove") or [] if k]
    remove_all = bool(value.get("remove_all"))
    return sets, removes, remove_all


def _time_param(ctx: Context, name: str) -> float | None:
    raw = ctx.params.get(name)
    if raw is None or raw == "":
        return None
    value = util.parse_time(raw)
    if value is None:
        raise UserError("err.invalid_param", param=name)
    return float(value)


def _range(ctx: Context, duration: float) -> tuple[float, float]:
    """Validated ``(start, end)`` from the trim dialog params."""
    start = _time_param(ctx, "start") or 0.0
    end = _time_param(ctx, "end")
    if end is None or end <= 0:
        end = duration
    start = max(0.0, start)
    end = min(duration, end)
    if end - start < 0.05:
        raise UserError("media.err.range")
    return start, end


def _ts(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


def _speed_suffix(ctx: Context, factor: float) -> str:
    return ctx.t("media.sfx.speed", factor=mu.fmt_num(factor).replace(".", "," if ctx.lang == "it" else "."))


def _remove_quiet(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except OSError:
        pass


def _passthrough(ctx: Context, out: Path, f: FileInfo, message_key: str = "warn.already_optimized") -> Result:
    """Drop an output that did not beat the original (Compress "pass through")."""
    _remove_quiet(out)
    return ctx.result([], message=ctx.t(message_key), kind="none", size_before=f.size)


# ======================================================================================== params

_P_CODEC_MP4 = P.select("codec", "media.prm.codec", [("h264", "media.opt.h264"), ("hevc", "media.opt.hevc")], "h264",
                        help="media.prm.codec.help")
_P_CODEC_WEBM = P.select("codec", "media.prm.codec", [("vp9", "media.opt.vp9"), ("av1", "media.opt.av1")], "vp9")
_P_CRF = P.int("crf", "media.prm.crf", "cfg:video.crf", min=0, max=51, help="media.prm.crf.help")
_P_START = P.time("start", "media.prm.start", 0.0)
_P_END = P.time("end", "media.prm.end", None)
_P_TRIM_MODE = P.select("mode", "media.prm.trim_mode", [("keep", "media.opt.keep"), ("cut", "media.opt.cut")], "keep")
_P_FAST = P.bool("fast", "media.prm.fast", False, help="media.prm.fast.help")
_MAX_SHORT_OPTIONS = [(0, "media.opt.original"), (2160, "2160p"), (1440, "1440p"), (1080, "1080p"), (720, "720p"),
                      (480, "480p")]


def _p_max_short(default: int) -> Any:
    return P.select("max_short", "media.prm.max_short", _MAX_SHORT_OPTIONS, default, help="media.prm.max_short.help")


def _p_compress_crf(default: int) -> Any:
    return P.int("crf", "media.prm.crf", default, min=0, max=51, help="media.prm.crf.help")


# ===================================================================================== video: convert

def _convert_video(ctx: Context, container: str, codec: str, *, remux_any: bool = False, crf: int | None = None) -> Result:
    """Convert to ``container``; the video stream is copied when it already fits (``remux_any``: any
    codec the container accepts), otherwise re-encoded with ``codec``."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = info.duration
    out = ctx.output_path(ext=container)
    pix_ok = vs.pix_fmt in ("yuv420p", "yuvj420p") or codec != "h264"
    copy_ok = (not vs.interlaced and mu.can_copy_video(container, vs.codec)
               and (remux_any or (vs.codec == codec and pix_ok and not (codec == "h264" and vs.hdr))))
    with atomic_target(out) as tmp:
        if copy_ok:
            tag = ["-tag:v", "hvc1"] if vs.codec == "hevc" and mu.container_of(container) in ("mp4", "mov") else []
            args = ["-i", _fp(f.path), *_main_maps(info), "-c:v", "copy", *tag,
                    *_audio_codec_args(ctx, info, container), *_sub_args(ctx, info, container),
                    "-map_metadata", "0", *_mux_args(container), "-max_muxing_queue_size", "4096", _fp(tmp)]
            try:
                run_ffmpeg(ctx, args, duration=d)
                return ctx.result([out], size_before=f.size)
            except Cancelled:
                raise
            except UserError as exc:
                ctx.log(f"remux failed, re-encoding: {exc.details[-500:]}")
                ctx.warn(ctx.t("media.warn.copy_fallback"))
        _reencode_into(ctx, f, info, tmp, container, codec, crf=crf)
    return ctx.result([out], size_before=f.size)


def _reencode_into(ctx: Context, f: FileInfo, info: mu.MediaInfo, tmp: Path, container: str, codec: str, *,
                   crf: int | None = None, extra_filters: Sequence[str] = ()) -> VEnc:
    """Full re-encode of the main video (+ audio as needed, subtitles converted) into ``tmp``."""
    vs = info.video
    assert vs is not None
    ten = vs.hdr and codec in ("hevc", "vp9", "av1")
    enc = _make_venc(ctx, codec, vs, crf=crf, ten_bit=ten)
    filters = _fix_filters(vs) + ([] if ten else _sdr_filters(ctx, vs)) + list(extra_filters)
    vfr = ["-fps_mode", "passthrough"] if vs.vfr else []

    def build(e: VEnc) -> list[str]:
        return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(), *vfr,
                *_audio_codec_args(ctx, info, container), *_sub_args(ctx, info, container),
                "-map_metadata", "0", *_mux_args(container), "-max_muxing_queue_size", "4096", _fp(tmp)]

    return _encode(ctx, enc, build, duration=info.duration)


def _crf_for(ctx: Context, codec: str) -> int | None:
    """CRF from the ``crf`` param (H.264 scale; HEVC gets +5, the x265 equivalent)."""
    if ctx.params.get("crf") in (None, ""):
        return None
    crf = max(0, min(51, ctx.pint("crf", _cfg_crf(ctx))))
    return min(51, crf + 5) if codec == "hevc" else crf


@action(id="video.convert.mp4", label="MP4", text="MP4", icon="movie", categories={"video"}, mode="quick",
        when=_not_ext("mp4", "m4v"), params=[_P_CODEC_MP4, _P_CRF], requires=FF, description="media.desc.to_mp4",
        mixed_ok=False, order=10)
def video_to_mp4(ctx: Context) -> Result:
    """MP4 (H.264/AAC by default, HEVC optional; remux when the codec already matches)."""
    codec = "hevc" if ctx.pstr("codec", "h264") == "hevc" else "h264"
    return _convert_video(ctx, "mp4", codec, crf=_crf_for(ctx, codec))


@action(id="video.convert.webm", label="WEBM", text="WEBM", icon="movie", categories={"video"}, mode="quick",
        when=_not_ext("webm"), params=[_P_CODEC_WEBM], requires=FF, description="media.desc.to_webm", order=11)
def video_to_webm(ctx: Context) -> Result:
    """WebM: VP9/Opus (default) or AV1/Opus."""
    codec = "av1" if ctx.pstr("codec", "vp9") == "av1" else "vp9"
    return _convert_video(ctx, "webm", codec)


@action(id="video.convert.mkv", label="MKV", text="MKV", icon="movie", categories={"video"},
        when=_not_ext("mkv"), requires=FF, description="media.desc.to_mkv", order=12)
def video_to_mkv(ctx: Context) -> Result:
    """Matroska remux (all streams copied, mov_text → SRT); re-encodes only if copying fails."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    out = ctx.output_path(ext="mkv")
    with atomic_target(out) as tmp:
        maps: list[str] = []
        for v in info.videos:
            maps += ["-map", f"0:{v.index}"]
        for a in info.audios:
            maps += ["-map", f"0:{a.index}"]
        args = ["-fflags", "+genpts", "-i", _fp(f.path), *maps, "-c", "copy", *_sub_args(ctx, info, "mkv"),
                "-map_metadata", "0", "-max_muxing_queue_size", "4096", _fp(tmp)]
        try:
            run_ffmpeg(ctx, args, duration=info.duration)
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"mkv remux failed: {exc.details[-500:]}")
            ctx.warn(ctx.t("media.warn.copy_fallback"))
            _reencode_into(ctx, f, info, tmp, "mkv", "h264")
    return ctx.result([out], size_before=f.size)


@action(id="video.convert.mov", label="MOV", text="MOV", icon="movie", categories={"video"},
        when=_not_ext("mov", "qt"), requires=FF, description="media.desc.to_mov", order=13)
def video_to_mov(ctx: Context) -> Result:
    """QuickTime MOV (stream copy when compatible, else H.264/AAC)."""
    return _convert_video(ctx, "mov", "h264", remux_any=True)


def _gif_width(ctx: Context, vs: mu.VStream) -> int | None:
    width = ctx.pint("width", 480)
    disp_w = vs.display_size[0] or 0
    limit = min(disp_w, 1920) if disp_w else 1920
    target = min(width, limit) if width > 0 else limit
    if disp_w and target >= disp_w:
        return None
    return max(16, target)


@action(id="video.convert.gif", label="GIF", text="GIF", icon="gif_box", categories={"video"}, mode="quick",
        params=[P.select("width", "media.prm.width", [(320, "320 px"), (480, "480 px"), (640, "640 px"),
                                                     (720, "720 px"), (1080, "1080 px"), (0, "media.opt.original")],
                         480),
                P.int("fps", "media.prm.fps", 15, min=5, max=30, unit="media.unit.fps")],
        requires=FF, description="media.desc.to_gif", order=14)
def video_to_gif(ctx: Context) -> Result:
    """Animated GIF: gifski when installed, otherwise palettegen/paletteuse in one ffmpeg pass."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = info.duration
    fps = max(5, min(30, ctx.pint("fps", 15)))
    width = _gif_width(ctx, vs)
    if d and d > 60:
        ctx.warn(ctx.t("media.warn.gif_long"))
    prefix = ["bwdif=mode=send_frame"] if vs.interlaced else []
    if vs.sar and abs(vs.sar - 1.0) > 0.01:
        prefix += ["scale=trunc(iw*sar/2)*2:ih", "setsar=1"]
    prefix += _sdr_filters(ctx, vs)
    out = ctx.output_path(ext="gif")
    with atomic_target(out) as tmp:
        if tools.have("gifski"):
            try:
                _gif_with_gifski(ctx, f, vs, d, fps, width, prefix, tmp)
                return ctx.result([out], size_before=f.size)
            except Cancelled:
                raise
            except UserError as exc:
                ctx.log(f"gifski failed, using ffmpeg: {exc} {exc.details[-300:]}")
        vf = mu.gif_filter(fps, width, prefix=",".join(prefix))
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{vs.index}", "-an", "-sn", "-vf", vf, "-loop", "0",
                         "-f", "gif", _fp(tmp)], duration=d)
    return ctx.result([out], size_before=f.size)


def _gif_with_gifski(ctx: Context, f: FileInfo, vs: mu.VStream, d: float | None, fps: int, width: int | None,
                     prefix: list[str], tmp: Path) -> None:
    work = ctx.tmpdir() / "gif"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    chain = prefix + [f"fps={fps}"] + ([f"scale={width}:-1:flags=lanczos"] if width else [])
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{vs.index}", "-an", "-sn", "-vf", ",".join(chain),
                     "-f", "image2", "f%06d.png"], duration=d, span=(0.0, 0.6), cwd=work)
    frames = sorted(work.glob("f*.png"))
    if not frames:
        raise UserError("media.err.no_frames")
    ctx.progress(0.6, ctx.t("media.msg.encoding_gif"))
    # gifski shrinks frames above about 800×600 by itself (1280×720 → 640×360) unless it is told the width;
    # the height stays free, giving both would stretch the picture
    size = mu.png_size(frames[0])
    frame_w = size[0] if size else (width or vs.display_size[0])
    keep_size = ["--width", str(frame_w)] if frame_w else []
    # frame names relative to the work dir keep the argument list short (thousands of frames)
    ctx.run(["gifski", "--fps", str(fps), "--quality", "90", *keep_size, "-o", str(tmp), *[p.name for p in frames]],
            cwd=work)
    if not tmp.is_file() or tmp.stat().st_size == 0:
        raise UserError("err.no_output", name=f.stem + ".gif")


def _audio_cover(info: mu.MediaInfo, ext: str) -> mu.Picture | None:
    """Cover art stream that can be stream-copied into an ``ext`` audio file."""
    if ext not in ("mp3", "m4a", "m4b", "flac"):
        return None
    for pic in info.pictures:
        if pic.codec in ("mjpeg", "png"):
            return pic
    return None


def _tag_args(info: mu.MediaInfo, stream: mu.AStream | mu.VStream | None, *, source: int = 0) -> list[str]:
    """``-map_metadata`` options keeping the tags of input ``source`` (described by ``info``).

    Ogg keeps its Vorbis comments on the stream while the MP3/MP4/FLAC/WAV muxers only write the file-level
    tags, so the comments of ``stream`` are mapped to the file level too.  Mapping from a stream also turns off
    ffmpeg's automatic copy of the stream tags: ``-metadata`` can then override a comment in an Ogg output
    (the Ogg muxer prefers the stream tags), and a cover art needs its own mapping (see :func:`_audio_out`)."""
    args = ["-map_metadata", str(source)]
    if info.stream_tags and stream is not None:
        args += ["-map_metadata", f"{source}:s:{stream.index}"]
    return args


def _audio_out(ctx: Context, f: FileInfo, info: mu.MediaInfo, target: mu.AudioTarget, tmp: Path, *,
               duration: float | None, af: str | None = None, start: float | None = None,
               limit: float | None = None, copy: bool = False, extra: Sequence[str] = (),
               span: tuple[float, float] = (0.0, 1.0), label: str | None = None, keep_cover: bool = True) -> None:
    """Encode (or copy) the first audio stream of ``f`` into ``tmp`` with ``target``; cover art is kept for
    MP3/M4A/FLAC (retried without it if the muxer refuses)."""
    a = info.audios[0]
    pic = _audio_cover(info, target.ext) if keep_cover else None

    def build(with_pic: bool) -> list[str]:
        args: list[str] = []
        if start:
            args += ["-ss", _ts(start)]
        args += ["-i", _fp(f.path), "-map", f"0:{a.index}"]
        if with_pic and pic is not None:
            args += ["-map", f"0:{pic.index}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        else:
            args += ["-vn"]
        args += ["-sn", "-dn"]
        if limit:
            args += ["-t", _ts(limit)]
        if af:
            args += ["-af", af]
        if copy:
            args += ["-c:a", "copy"]
            if target.muxer == "ipod":
                args += ["-movflags", "+faststart"]
        else:
            args += target.args
        args += [*extra, *_tag_args(info, a)]
        if with_pic and pic is not None and info.stream_tags:
            # its title and picture type ("Cover (front)") are stream tags as well
            args += ["-map_metadata:s:v", f"0:s:{pic.index}"]
        if target.muxer:
            args += ["-f", target.muxer]
        args.append(_fp(tmp))
        return args

    raw = span != (0.0, 1.0)
    try:
        run_ffmpeg(ctx, build(pic is not None), duration=duration, span=span, label=label, raw=raw)
    except Cancelled:
        raise
    except UserError:
        if pic is None:
            raise
        ctx.log("muxing the cover art failed, retrying without it")
        run_ffmpeg(ctx, build(False), duration=duration, span=span, label=label, raw=raw)


_COPY_CODECS = {"mp3": {"mp3"}, "m4a": {"aac", "alac"}, "m4b": {"aac", "alac"}, "opus": {"opus"},
                "flac": {"flac"}, "ogg": {"vorbis"}, "oga": {"vorbis"}}


def _audio_overrides(ctx: Context) -> dict:
    """Config overlay with per-job audio quality params."""
    cfg = dict(ctx.config)
    audio = dict(cfg.get("audio") or {}) if isinstance(cfg.get("audio"), dict) else {}
    if ctx.params.get("quality") not in (None, ""):
        audio["mp3_quality"] = ctx.pint("quality", 2)
    if ctx.params.get("bitrate") not in (None, ""):
        audio["opus_bitrate"] = ctx.pstr("bitrate")
    cfg["audio"] = audio
    return cfg


def _convert_audio_file(ctx: Context, f: FileInfo, ext: str, *, span: tuple[float, float] = (0.0, 1.0),
                        label: str | None = None) -> Path:
    """Convert/extract the first audio stream of ``f`` (audio or video) to ``ext``."""
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    cfg = _audio_overrides(ctx)
    kbps = None
    if ext in ("m4a", "m4b") and ctx.params.get("bitrate") not in (None, ""):
        kbps = mu.parse_bitrate(ctx.pstr("bitrate"), 192)
    target = mu.audio_target(ext, a, cfg, bitrate=kbps)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    # same codec: stream copy (no generation loss), whatever quality was asked for
    copy = a.codec in _COPY_CODECS.get(target.ext, set())
    if target.lossless and not a.lossless:
        ctx.warn(ctx.t("warn.lossy_to_lossless"))
    out = ctx.output_path(f, ext=target.ext)
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=info.duration, copy=copy, span=span, label=label)
    return out


def _convert_audio(ctx: Context, ext: str) -> Result:
    f = ctx.file
    out = _convert_audio_file(ctx, f, ext)
    return ctx.result([out], size_before=f.size)


_P_MP3_Q = P.int("quality", "media.prm.mp3_quality", "cfg:audio.mp3_quality", min=0, max=9,
                 help="media.prm.mp3_quality.help")
_P_OPUS_BR = P.select("bitrate", "media.prm.bitrate", [("48k", "48 kbit/s"), ("64k", "64 kbit/s"),
                                                       ("96k", "96 kbit/s"), ("128k", "128 kbit/s"),
                                                       ("160k", "160 kbit/s"), ("192k", "192 kbit/s")],
                      "cfg:audio.opus_bitrate")
_P_AAC_BR = P.select("bitrate", "media.prm.bitrate", [("128k", "128 kbit/s"), ("160k", "160 kbit/s"),
                                                      ("192k", "192 kbit/s"), ("256k", "256 kbit/s")], "192k")


@action(id="video.convert.mp3", label="MP3", text="MP3", icon="music_note", categories={"video"}, mode="quick",
        params=[_P_MP3_Q], requires=FF, description="media.desc.extract_mp3", order=15)
def video_to_mp3(ctx: Context) -> Result:
    """Audio track as MP3 (LAME VBR)."""
    return _convert_audio(ctx, "mp3")


@action(id="video.convert.m4a", label="M4A", text="M4A", icon="music_note", categories={"video"}, mode="quick",
        params=[_P_AAC_BR], requires=FF, description="media.desc.extract_m4a", order=16)
def video_to_m4a(ctx: Context) -> Result:
    """Audio track as M4A (AAC copied when possible)."""
    return _convert_audio(ctx, "m4a")


@action(id="video.convert.opus", label="OPUS", text="OPUS", icon="music_note", categories={"video"}, mode="quick",
        params=[_P_OPUS_BR], requires=FF, description="media.desc.extract_opus", order=17)
def video_to_opus(ctx: Context) -> Result:
    """Audio track as Opus."""
    return _convert_audio(ctx, "opus")


@action(id="video.convert.flac", label="FLAC", text="FLAC", icon="music_note", categories={"video"},
        requires=FF, description="media.desc.extract_flac", order=18)
def video_to_flac(ctx: Context) -> Result:
    """Audio track as FLAC (lossless container of the decoded audio)."""
    return _convert_audio(ctx, "flac")


@action(id="video.convert.wav", label="WAV", text="WAV", icon="music_note", categories={"video"},
        requires=FF, description="media.desc.extract_wav", order=19)
def video_to_wav(ctx: Context) -> Result:
    """Audio track as WAV PCM."""
    return _convert_audio(ctx, "wav")


# ==================================================================================== video: compress

_VIDEO_PRESETS = {"high": (23, 0, 160), "balanced": (26, 1080, 128), "small": (30, 720, 96)}


def _scale_short(vs: mu.VStream, short: int | None) -> list[str]:
    """Scale filters bringing the short side down to ``short`` (nothing when already smaller)."""
    if not short:
        return []
    w, h = vs.display_size
    dims = mu.fit_short_side(w, h, short)
    if dims is None:
        return []
    return [f"scale={dims[0]}:{dims[1]}:flags=lanczos", "setsar=1"]


def _compress_video(ctx: Context, preset: str) -> Result:
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    crf_default, cap_default, akbps = _VIDEO_PRESETS[preset]
    codec = "hevc" if ctx.pstr("codec", "h264") == "hevc" else "h264"
    crf = max(0, min(51, ctx.pint("crf", crf_default)))
    if codec == "hevc":
        crf = min(51, crf + 5)
    cap = ctx.pint("max_short", cap_default)
    ten = vs.hdr and codec == "hevc"
    enc = _make_venc(ctx, codec, vs, crf=crf, ten_bit=ten)
    scale = _scale_short(vs, cap)
    filters = ([p for p in _fix_filters(vs) if not scale or not p.startswith(("scale=", "setsar"))] + scale
               + ([] if ten else _sdr_filters(ctx, vs)))
    vfr = ["-fps_mode", "passthrough"] if vs.vfr else []
    out = ctx.output_path(ext="mp4", suffix="sfx.compressed")
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(), *vfr,
                    *_audio_codec_args(ctx, info, "mp4", kbps=akbps), *_sub_args(ctx, info, "mp4"),
                    "-map_metadata", "0", "-movflags", "+faststart", "-max_muxing_queue_size", "4096", _fp(tmp)]

        _encode(ctx, enc, build, duration=info.duration)
    if f.size and out.stat().st_size >= f.size:
        return _passthrough(ctx, out, f)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _compress_params(preset: str) -> list:
    crf, cap, _a = _VIDEO_PRESETS[preset]
    return [_P_CODEC_MP4, _p_compress_crf(crf), _p_max_short(cap)]


@action(id="video.compress.balanced", label="media.act.balanced", icon="compress", categories={"video"},
        mode="quick", params=_compress_params("balanced"), requires=FF, replace_ok=True,
        description="media.desc.compress_balanced", order=20)
def video_compress_balanced(ctx: Context) -> Result:
    """CRF 26, ≤1080p, AAC 128k."""
    return _compress_video(ctx, "balanced")


@action(id="video.compress.high", label="media.act.high_quality", icon="high_quality", categories={"video"},
        mode="quick", params=_compress_params("high"), requires=FF, replace_ok=True,
        description="media.desc.compress_high", order=21)
def video_compress_high(ctx: Context) -> Result:
    """CRF 23, original resolution."""
    return _compress_video(ctx, "high")


@action(id="video.compress.small", label="media.act.small", icon="compress", categories={"video"},
        mode="quick", params=_compress_params("small"), requires=FF, replace_ok=True,
        description="media.desc.compress_small", order=22)
def video_compress_small(ctx: Context) -> Result:
    """CRF 30, ≤720p, AAC 96k."""
    return _compress_video(ctx, "small")


def _two_pass(ctx: Context, f: FileInfo, info: mu.MediaInfo, plan: mu.BitratePlan, tmp: Path) -> None:
    """Two-pass x264 at ``plan`` bitrates into ``tmp`` (log files in the job temp dir)."""
    vs = info.video
    assert vs is not None
    d = info.duration
    work = ctx.tmpdir()
    scale = _scale_short(vs, plan.max_short)
    filters = ([p for p in _fix_filters(vs) if not scale or not p.startswith(("scale=", "setsar"))] + scale
               + _sdr_filters(ctx, vs))
    enc = _make_venc(ctx, "h264", vs, bitrate=plan.video_kbps, allow_hw=False)
    common = ["-i", _fp(f.path), "-map", f"0:{vs.index}", *enc.vf(filters), *enc.args(), "-passlogfile", "dw2pass"]
    run_ffmpeg(ctx, [*common, "-pass", "1", "-an", "-sn", "-f", "null", "-"], duration=d, span=(0.0, 0.45),
               cwd=work, label=ctx.t("media.msg.pass", n=1, total=2))
    if info.audios and plan.audio_kbps:
        a = info.audios[0]
        audio = ["-map", f"0:{a.index}", "-c:a", "aac", "-b:a", f"{plan.audio_kbps}k"]
        if a.channels > 2:
            audio += ["-ac", "2"]
    else:
        audio = ["-an"]
    run_ffmpeg(ctx, [*common, "-pass", "2", *audio, "-sn", "-map_metadata", "0", "-movflags", "+faststart",
                     _fp(tmp)], duration=d, span=(0.45, 1.0), cwd=work, label=ctx.t("media.msg.pass", n=2, total=2))


def _target_plan(ctx: Context, f: FileInfo, info: mu.MediaInfo, target_bytes: int,
                 cap: int | None = None) -> mu.BitratePlan:
    d = _need_duration(ctx, f, info)
    vs = info.video
    short = min(vs.display_size) if vs and all(vs.display_size) else None
    try:
        plan = mu.plan_target_bitrate(d, target_bytes, short, info.has_audio)
    except ValueError:
        minimum = mu.min_target_bytes(d, info.has_audio)
        raise UserError("media.err.target_too_small", size=util.human_size(target_bytes, ctx.lang),
                        duration=util.human_duration(d), min=util.human_size(minimum, ctx.lang)) from None
    if cap and short and short > cap and (plan.max_short is None or plan.max_short > cap):
        plan = dataclasses.replace(plan, max_short=cap)
    return plan


@action(id="video.compress.target", label="media.act.target_size", icon="straighten", categories={"video"},
        mode="dialog", dialog="form",
        params=[P.float("size_mb", "media.prm.size_mb", 25.0, min=1, max=100000, step=1, unit="media.unit.mb")],
        requires=FF, replace_ok=True, description="media.desc.target_size", order=23)
def video_compress_target(ctx: Context) -> Result:
    """Two-pass H.264 reaching a file size (resolution lowered when the bitrate gets small)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    target_bytes = int(max(1.0, ctx.pfloat("size_mb", 25.0)) * _MB)
    if f.size and f.size <= target_bytes:
        return ctx.result([], kind="none", size_before=f.size,
                          message=ctx.t("media.msg.already_under", size=util.human_size(target_bytes, ctx.lang)))
    plan = _target_plan(ctx, f, info, target_bytes)
    out = ctx.output_path(ext="mp4", suffix="sfx.compressed")
    with atomic_target(out) as tmp:
        _two_pass(ctx, f, info, plan, tmp)
    size = out.stat().st_size
    if size > target_bytes * 1.03:
        ctx.warn(ctx.t("media.warn.target_missed", size=util.human_size(size, ctx.lang)))
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


_MESSAGING_LIMIT = 15_500_000


@action(id="video.compress.messaging", label="media.act.messaging", icon="chat", categories={"video"},
        requires=FF, description="media.desc.messaging", order=24)
def video_compress_messaging(ctx: Context) -> Result:
    """WhatsApp/Telegram: ≤ 720p H.264/AAC MP4 under ~16 MB."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    out = ctx.output_path(ext="mp4", suffix="sfx.compressed")
    with atomic_target(out) as tmp:
        single = False
        if f.size and f.size <= _MESSAGING_LIMIT:
            enc = _make_venc(ctx, "h264", vs, crf=26)
            scale = _scale_short(vs, 720)
            filters = ([p for p in _fix_filters(vs) if not scale or not p.startswith(("scale=", "setsar"))] + scale
                       + _sdr_filters(ctx, vs))

            def build(e: VEnc) -> list[str]:
                return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info, first_audio_only=True), *e.vf(filters),
                        *e.args(), *_audio_codec_args(ctx, info, "mp4", kbps=128, first_only=True, stereo=True), "-sn",
                        "-map_metadata", "0", "-movflags", "+faststart", _fp(tmp)]

            _encode(ctx, enc, build, duration=info.duration)
            single = tmp.stat().st_size <= _MESSAGING_LIMIT
        if not single:
            plan = _target_plan(ctx, f, info, _MESSAGING_LIMIT, cap=720)
            _two_pass(ctx, f, info, plan, tmp)
    return ctx.result([out], size_before=f.size)


# ===================================================================================== video: resize

def _resize_video(ctx: Context, short: int) -> Result:
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    w, h = vs.display_size
    dims = mu.fit_short_side(w, h, short)
    if dims is None:
        raise UserError("media.err.no_upscale", size=f"{short}p", width=w, height=h)
    ext, enc, colour = _edit_target(ctx, f, info)
    filters = (["bwdif=mode=send_frame"] if vs.interlaced else []) + colour + [
        f"scale={dims[0]}:{dims[1]}:flags=lanczos", "setsar=1"]
    vfr = ["-fps_mode", "passthrough"] if vs.vfr else []
    out = ctx.output_path(ext=ext, suffix=f"{short}p")
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(), *vfr,
                    *_audio_codec_args(ctx, info, ext), *_sub_args(ctx, info, ext), "-map_metadata", "0",
                    *_mux_args(ext), "-max_muxing_queue_size", "4096", _fp(tmp)]

        _encode(ctx, enc, build, duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _resize_action(short: int, order: int, text: str) -> None:
    @action(id=f"video.resize.{short}", label=f"{short}p", text=text, icon="photo_size_select_large",
            categories={"video"}, requires=FF, replace_ok=True, description="media.desc.resize", order=order)
    def _resize(ctx: Context) -> Result:
        return _resize_video(ctx, short)

    _resize.__name__ = f"video_resize_{short}"
    _resize.__doc__ = f"Shrink the short side to {short} px (never enlarges)."


for _short, _order, _text in ((1080, 30, "1080p"), (720, 31, "720p"), (480, 32, "480p"), (1440, 33, "1440p"),
                              (2160, 34, "4K")):
    _resize_action(_short, _order, _text)


@action(id="video.crop", label="media.act.crop", icon="crop", categories={"video"}, multi="single", mode="dialog",
        dialog="crop", params=[P.rect("rect", "media.prm.rect", required=True),
                               P.text("ratio", "media.prm.ratio", "free")],
        requires=FF, replace_ok=True, description="media.desc.crop", order=35)
def video_crop(ctx: Context) -> Result:
    """Crop to ``rect`` (source pixels, display orientation); even dimensions."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    rot_w, rot_h = (vs.height, vs.width) if vs.rotation % 180 == 90 else (vs.width, vs.height)
    try:
        x, y, w, h = mu.crop_rect(ctx.p("rect"), rot_w, rot_h)
    except ValueError:
        raise UserError("media.err.rect") from None
    if (x, y, w, h) == (0, 0, rot_w - rot_w % 2, rot_h - rot_h % 2) and rot_w % 2 == 0 and rot_h % 2 == 0:
        raise UserError("media.err.rect_full")
    ext, enc, colour = _edit_target(ctx, f, info)
    filters = (["bwdif=mode=send_frame"] if vs.interlaced else []) + [mu.crop_filter(x, y, w, h)]
    if vs.sar and abs(vs.sar - 1.0) > 0.01:
        filters += ["scale=trunc(iw*sar/2)*2:trunc(ih/2)*2", "setsar=1"]
    filters += colour
    vfr = ["-fps_mode", "passthrough"] if vs.vfr else []
    out = ctx.output_path(ext=ext, suffix="sfx.cropped")
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(), *vfr,
                    *_audio_codec_args(ctx, info, ext), *_sub_args(ctx, info, ext), "-map_metadata", "0",
                    *_mux_args(ext), "-max_muxing_queue_size", "4096", _fp(tmp)]

        _encode(ctx, enc, build, duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


# ======================================================================================= video: trim

def _copy_maps(info: mu.MediaInfo, container: str, ctx: Context, *, subs: bool = True) -> list[str]:
    """Maps for a stream copy into the same container (main video, audio, compatible subtitles)."""
    args = _main_maps(info)
    if subs:
        args += _sub_args(ctx, info, container, warn=False)
    return args


def _cut_ranges(start: float, end: float, d: float, mode: str) -> tuple[str, float, float]:
    """Normalise a cut that touches an edge into a keep; raises for a cut of everything."""
    if mode != "cut":
        return "keep", start, end
    edge = 0.05
    if start <= edge and end >= d - edge:
        raise UserError("media.err.range_all")
    if start <= edge:
        return "keep", end, d
    if end >= d - edge:
        return "keep", 0.0, start
    return "cut", start, end


@action(id="video.trim", label="media.act.trim", icon="content_cut", categories={"video"}, multi="single",
        mode="dialog", dialog="trim", params=[_P_START, _P_END, _P_TRIM_MODE, _P_FAST], requires=FF,
        replace_ok=True, description="media.desc.trim_video", order=25)
def video_trim(ctx: Context) -> Result:
    """Keep (or cut out) a time range: accurate re-encode, or fast keyframe stream copy."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    start, end = _range(ctx, d)
    mode, start, end = _cut_ranges(start, end, d, ctx.pstr("mode", "keep"))
    fast = ctx.pbool("fast", False)
    if fast:
        ext, fmt = _copy_container(f)
        ctx.warn(ctx.t("media.warn.fast_trim"))
        out = ctx.output_path(ext=ext, suffix="sfx.trimmed")
        with atomic_target(out) as tmp:
            if mode == "keep":
                run_ffmpeg(ctx, ["-ss", _ts(start), "-i", _fp(f.path), "-t", _ts(end - start), "-c", "copy",
                                 *_copy_maps(info, ext, ctx), "-avoid_negative_ts", "make_zero",
                                 "-map_metadata", "0", *_mux_args(ext), *fmt, _fp(tmp)], duration=end - start)
            else:
                _fast_cut(ctx, f, info, ext, start, end, d, tmp, fmt)
        final = ctx.replace_original(f, out)
        return ctx.result([final], size_before=f.size)
    ext, enc, colour = _edit_target(ctx, f, info)
    filters = _fix_filters(vs) + colour
    out = ctx.output_path(ext=ext, suffix="sfx.trimmed")
    with atomic_target(out) as tmp:
        if mode == "keep":
            def build(e: VEnc) -> list[str]:
                return [*e.input_args(), "-ss", _ts(start), "-i", _fp(f.path), "-t", _ts(end - start),
                        *_main_maps(info), *e.vf(filters), *e.args(),
                        *_audio_codec_args(ctx, info, ext, reencode=True), *_sub_args(ctx, info, ext),
                        "-map_metadata", "0", *_mux_args(ext), "-max_muxing_queue_size", "4096", _fp(tmp)]

            _encode(ctx, enc, build, duration=end - start)
        else:
            _accurate_cut(ctx, f, info, ext, enc, filters, start, end, d, tmp)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _fast_cut(ctx: Context, f: FileInfo, info: mu.MediaInfo, ext: str, start: float, end: float, d: float,
              tmp: Path, fmt: Sequence[str] = ()) -> None:
    """Remove [start, end] with stream copies: two parts joined by the concat demuxer.

    ``fmt`` holds the ``-f`` options of containers ffmpeg cannot guess from ``ext``."""
    work = ctx.tmpdir()
    maps = _main_maps(info)
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-t", _ts(start), *maps, "-c", "copy", "-avoid_negative_ts", "make_zero",
                     *fmt, _fp(work / f"part1.{ext}")], duration=start, span=(0.0, 0.4), raw=True)
    run_ffmpeg(ctx, ["-ss", _ts(end), "-i", _fp(f.path), *maps, "-c", "copy", "-avoid_negative_ts", "make_zero",
                     *fmt, _fp(work / f"part2.{ext}")], duration=max(0.1, d - end), span=(0.4, 0.8), raw=True)
    (work / "list.ffconcat").write_text(mu.concat_list([f"part1.{ext}", f"part2.{ext}"]), encoding="utf-8")
    run_ffmpeg(ctx, ["-f", "concat", "-safe", "0", "-i", "list.ffconcat", "-map", "0", "-c", "copy",
                     "-map_metadata", "-1", *_mux_args(ext), *fmt, _fp(tmp)], duration=d - (end - start),
               span=(0.8, 1.0), cwd=work)


def _accurate_cut(ctx: Context, f: FileInfo, info: mu.MediaInfo, ext: str, enc: VEnc, filters: list[str],
                  start: float, end: float, d: float, tmp: Path) -> None:
    """Remove [start, end] with trim/atrim + concat (frame accurate, re-encoded)."""
    vs = info.video
    assert vs is not None
    a = info.audios[0] if info.audios else None

    def build(e: VEnc) -> list[str]:
        graph = [f"[0:{vs.index}]split=2[vs0][vs1]",
                 f"[vs0]trim=end={_ts(start)},setpts=PTS-STARTPTS[v0]",
                 f"[vs1]trim=start={_ts(end)},setpts=PTS-STARTPTS[v1]"]
        if a is not None:
            graph += [f"[0:{a.index}]asplit=2[as0][as1]",
                      f"[as0]atrim=end={_ts(start)},asetpts=PTS-STARTPTS[a0]",
                      f"[as1]atrim=start={_ts(end)},asetpts=PTS-STARTPTS[a1]",
                      "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]"]
        else:
            graph.append("[v0][v1]concat=n=2:v=1:a=0[vc]")
        tail = e.chain(filters)
        graph.append(f"[vc]{tail}[vout]" if tail else "[vc]null[vout]")
        audio = ["-map", "[ac]", *_filtered_audio_codec(ctx, ext, channels=a.channels)] if a is not None else []
        return [*e.input_args(), "-i", _fp(f.path), "-filter_complex", ";".join(graph), "-map", "[vout]",
                *e.args(), *audio, "-sn", "-map_metadata", "0", *_mux_args(ext), _fp(tmp)]

    _encode(ctx, enc, build, duration=d - (end - start), raw=True)


def _filtered_audio_codec(ctx: Context, ext: str, kbps: int = 160, channels: int = 2) -> list[str]:
    """Audio encoder for a filtered audio stream inside a video container (``kbps`` per stereo pair)."""
    if mu.container_of(ext) == "webm":
        return ["-c:a", _encoder(ctx, "libopus", "libvorbis"), "-b:a", f"{min(kbps, 128)}k",
                *(["-ac", "2"] if channels > 2 else [])]
    return ["-c:a", "aac", "-b:a", f"{mu.aac_kbps(kbps, channels)}k"]


# ======================================================================================= video: edit

@action(id="video.edit.mute", label="media.act.mute", icon="volume_off", categories={"video"}, requires=FF,
        replace_ok=True, description="media.desc.mute", order=40)
def video_mute(ctx: Context) -> Result:
    """Remove every audio track (stream copy)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    if not info.audios:
        raise UserError("media.err.no_audio", name=f.name)
    ext, fmt = _copy_container(f)
    out = ctx.output_path(ext=ext, suffix="sfx.muted")
    maps: list[str] = []
    for v in info.videos:
        maps += ["-map", f"0:{v.index}"]
    for pic in info.pictures:
        maps += ["-map", f"0:{pic.index}"]
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), *maps, *_sub_args(ctx, info, ext, warn=False), "-c:v", "copy",
                         "-an", "-map_metadata", "0", *_mux_args(ext), *fmt, _fp(tmp)], duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _filter_edit(ctx: Context, vfilters: Sequence[str], suffix: str, *, af: str | None = None,
                 sdr: bool = False, keep_subs: bool = True) -> Result:
    """Re-encode the video through ``vfilters`` (and ``af`` on the audio) in its own container."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    ext, enc, colour = _edit_target(ctx, f, info, sdr=sdr)
    filters = _fix_filters(vs) + colour + list(vfilters)
    vfr = ["-fps_mode", "passthrough"] if vs.vfr else []
    out = ctx.output_path(ext=ext, suffix=suffix)
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            audio = (["-af", af, *_audio_codec_args(ctx, info, ext, reencode=True)] if af
                     else _audio_codec_args(ctx, info, ext))
            subs = _sub_args(ctx, info, ext) if keep_subs else ["-sn"]
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(), *vfr,
                    *audio, *subs, "-map_metadata", "0", *_mux_args(ext), "-max_muxing_queue_size", "4096",
                    _fp(tmp)]

        _encode(ctx, enc, build, duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


@action(id="video.rotate.cw", label="media.act.rotate_cw", icon="rotate_right", categories={"video"}, requires=FF,
        replace_ok=True, description="media.desc.rotate", order=41)
def video_rotate_cw(ctx: Context) -> Result:
    """Rotate 90° clockwise (re-encode)."""
    return _filter_edit(ctx, ["transpose=clock"], "sfx.rotated")


@action(id="video.rotate.ccw", label="media.act.rotate_ccw", icon="rotate_left", categories={"video"}, requires=FF,
        replace_ok=True, description="media.desc.rotate", order=42)
def video_rotate_ccw(ctx: Context) -> Result:
    """Rotate 90° counter-clockwise (= 270° clockwise, re-encode)."""
    return _filter_edit(ctx, ["transpose=cclock"], "sfx.rotated")


@action(id="video.rotate.180", label="media.act.rotate_180", icon="flip_camera_android", categories={"video"},
        requires=FF, replace_ok=True, description="media.desc.rotate", order=43)
def video_rotate_180(ctx: Context) -> Result:
    """Rotate 180° (re-encode)."""
    return _filter_edit(ctx, ["hflip", "vflip"], "sfx.rotated")


@action(id="video.rotate.meta", label="media.act.rotate_meta", icon="screen_rotation", categories={"video"},
        mode="quick", when=_mp4_family,
        params=[P.select("angle", "media.prm.angle", [("cw", "media.opt.cw"), ("ccw", "media.opt.ccw"),
                                                      ("180", "media.opt.r180")], "cw")],
        requires=FF, replace_ok=True, description="media.desc.rotate_meta", order=44)
def video_rotate_meta(ctx: Context) -> Result:
    """Lossless rotation: rewrites the display matrix (``-display_rotation``) with a stream copy."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    delta = {"cw": -90, "ccw": 90, "180": 180}.get(ctx.pstr("angle", "cw"), -90)
    new = (vs.rotation + delta) % 360
    if new > 180:
        new -= 360
    ext, fmt = _copy_container(f)
    out = ctx.output_path(ext=ext, suffix="sfx.rotated")
    maps: list[str] = []
    for v in info.videos:
        maps += ["-map", f"0:{v.index}"]
    for a in info.audios:
        maps += ["-map", f"0:{a.index}"]
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, [f"-display_rotation:{vs.index}", str(new), "-i", _fp(f.path), *maps,
                         *_sub_args(ctx, info, ext, warn=False), "-c:v", "copy", "-c:a", "copy",
                         "-map_metadata", "0", *_mux_args(ext), *fmt, _fp(tmp)], duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


@action(id="video.flip.h", label="media.act.flip_h", icon="flip", categories={"video"}, requires=FF,
        replace_ok=True, description="media.desc.flip", order=45)
def video_flip_h(ctx: Context) -> Result:
    """Mirror horizontally (re-encode)."""
    return _filter_edit(ctx, ["hflip"], "sfx.flipped")


@action(id="video.flip.v", label="media.act.flip_v", icon="flip", categories={"video"}, requires=FF,
        replace_ok=True, description="media.desc.flip", order=46)
def video_flip_v(ctx: Context) -> Result:
    """Mirror vertically (re-encode)."""
    return _filter_edit(ctx, ["vflip"], "sfx.flipped")


def _speed_video(ctx: Context, factor: float) -> Result:
    if not (0.1 <= factor <= 16):
        raise UserError("media.err.speed")
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = info.duration
    ext, enc, colour = _edit_target(ctx, f, info)
    vfilters = _fix_filters(vs) + colour + [f"setpts=PTS/{mu.fmt_num(factor)}"]
    if factor > 1 and vs.fps:
        vfilters.append(f"fps={mu.fmt_num(min(vs.fps, 60.0))}")
    a = info.audios[0] if info.audios else None
    out = ctx.output_path(ext=ext, suffix=_speed_suffix(ctx, factor))
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            chain = e.chain(vfilters) or "null"
            graph = f"[0:{vs.index}]{chain}[v]"
            maps = ["-map", "[v]"]
            audio: list[str] = []
            if a is not None:
                graph += f";[0:{a.index}]{mu.atempo_chain(factor)}[a]"
                maps += ["-map", "[a]"]
                audio = _filtered_audio_codec(ctx, ext, channels=a.channels)
            return [*e.input_args(), "-i", _fp(f.path), "-filter_complex", graph, *maps, *e.args(), *audio, "-sn",
                    "-map_metadata", "0", *_mux_args(ext), _fp(tmp)]

        _encode(ctx, enc, build, duration=(d / factor) if d else None, raw=True)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _speed_action(cat: str, factor: float, order: int) -> None:
    key = mu.fmt_num(factor).replace(".", "_")
    label = f"{mu.fmt_num(factor)}×"

    @action(id=f"{cat}.speed.{key}", label=label, text=label, icon="speed", categories={cat}, requires=FF,
            when=_audio_files_ok, replace_ok=True, description=f"media.desc.speed_{cat}", order=order)
    def _speed(ctx: Context) -> Result:
        return _speed_video(ctx, factor) if cat == "video" else _speed_audio(ctx, factor)

    _speed.__name__ = f"{cat}_speed_{key}"
    _speed.__doc__ = f"Play at {factor}× (audio pitch preserved with atempo)."


_P_FACTOR = P.float("factor", "media.prm.factor", 1.25, min=0.25, max=8, step=0.05, unit="media.unit.x")


@action(id="video.speed.custom", label="media.act.speed_custom", icon="tune", categories={"video"}, mode="dialog",
        dialog="form", params=[_P_FACTOR], requires=FF, replace_ok=True, description="media.desc.speed_video",
        order=59)
def video_speed_custom(ctx: Context) -> Result:
    """Any speed between 0.25× and 8×."""
    return _speed_video(ctx, ctx.pfloat("factor", 1.25))


def _segment_seconds(info: mu.MediaInfo) -> float:
    """Segment length keeping the reverse filter's frame buffer around 400 MB."""
    vs = info.video
    if vs is None:
        return 300.0
    w, h = vs.display_size
    frame = max(1, (w or 1920) * (h or 1080)) * (3.0 if vs.bit_depth > 8 else 1.5)
    fps = vs.fps or 30.0
    seconds = 400e6 / (frame * fps)
    return max(1.0, min(30.0, seconds))


def _reverse_media(ctx: Context, f: FileInfo, info: mu.MediaInfo, tmp: Path, *, video: bool,
                   target: mu.AudioTarget | None = None, venc: VEnc | None = None,
                   vfilters: Sequence[str] = (), ext: str = "mp4") -> None:
    """Reverse in bounded memory: reverse fixed segments, then concatenate them in reverse order."""
    d = _need_duration(ctx, f, info)
    seg = _segment_seconds(info) if video else 300.0
    count = max(1, math.ceil(d / seg - 1e-9))
    work = ctx.tmpdir() / "reverse"
    work.mkdir(parents=True, exist_ok=True)
    a = info.audios[0] if info.audios else None
    names: list[str] = []
    for i in range(count):
        ctx.check_cancel()
        start = i * seg
        length = min(seg, d - start)
        if length < 0.02:
            break
        if video:
            vs = info.video
            assert vs is not None and venc is not None
            name = f"seg{i:05d}.mkv"
            args = ["-ss", _ts(start), "-i", _fp(f.path), "-t", _ts(length), "-map", f"0:{vs.index}"]
            chain = venc.chain(list(vfilters) + ["reverse"]) or "reverse"
            args += ["-vf", chain, *venc.args()]
            if a is not None:
                args += ["-map", f"0:{a.index}", "-af", "areverse", "-c:a", "pcm_s16le"]
            args += ["-sn", "-dn", "-f", "matroska", name]
        else:
            assert a is not None
            name = f"seg{i:05d}.wav"
            pcm = "pcm_s24le" if a.bits > 16 else "pcm_s16le"
            args = ["-ss", _ts(start), "-i", _fp(f.path), "-t", _ts(length), "-map", f"0:{a.index}",
                    "-af", "areverse", "-c:a", pcm, "-f", "wav", name]
        run_ffmpeg(ctx, args, duration=length, span=(0.9 * i / count, 0.9 * (i + 1) / count), cwd=work,
                   label=ctx.t("media.msg.segment", n=i + 1, total=count))
        names.append(name)
    names.reverse()
    (work / "list.ffconcat").write_text(mu.concat_list(names), encoding="utf-8")
    if video:
        audio = ["-map", "0:a?", *_filtered_audio_codec(ctx, ext, channels=a.channels)] if a is not None else []
        args = ["-f", "concat", "-safe", "0", "-i", "list.ffconcat", "-map", "0:v", "-c:v", "copy", *audio,
                "-map_metadata", "-1", *_mux_args(ext), _fp(tmp)]
    else:
        assert target is not None
        # the temporary segments carry no tags: they come from the original
        args = ["-f", "concat", "-safe", "0", "-i", "list.ffconcat", "-i", _fp(f.path), "-map", "0:a", *target.args,
                *_tag_args(info, a, source=1)]
        if target.muxer:
            args += ["-f", target.muxer]
        args.append(_fp(tmp))
    run_ffmpeg(ctx, args, duration=d, span=(0.9, 1.0), cwd=work, label=ctx.t("media.msg.joining"))


@action(id="video.edit.reverse", label="media.act.reverse", icon="fast_rewind", categories={"video"},
        requires=FF, replace_ok=True, description="media.desc.reverse", order=50)
def video_reverse(ctx: Context) -> Result:
    """Play backwards (video and audio), processed in segments to bound memory use."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    if d > 300:
        ctx.warn(ctx.t("media.warn.long_reverse"))
    ext, enc, colour = _edit_target(ctx, f, info)
    enc = enc.software()
    filters = _fix_filters(vs) + colour
    out = ctx.output_path(ext=ext, suffix="sfx.reversed")
    with atomic_target(out) as tmp:
        _reverse_media(ctx, f, info, tmp, video=True, venc=enc, vfilters=filters, ext=ext)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


@action(id="video.edit.loop", label="media.act.loop", icon="repeat", categories={"video"}, mode="quick",
        params=[P.int("times", "media.prm.times", 3, min=2, max=50, unit="media.unit.x")], requires=FF,
        description="media.desc.loop", order=51)
def video_loop(ctx: Context) -> Result:
    """Repeat the clip N times (stream copy, re-encode if the copy fails)."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    times = max(2, min(50, ctx.pint("times", 3)))
    d = info.duration
    total = d * times if d else None
    ext = f.ext if f.ext in EDIT_CONTAINERS else "mkv"
    out = ctx.output_path(ext=ext, suffix=ctx.t("media.sfx.loop", n=times))
    with atomic_target(out) as tmp:
        try:
            run_ffmpeg(ctx, ["-stream_loop", str(times - 1), "-i", _fp(f.path), *_main_maps(info), "-c", "copy",
                             "-map_metadata", "0", *_mux_args(ext), _fp(tmp)], duration=total)
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"loop copy failed: {exc.details[-400:]}")
            ctx.warn(ctx.t("media.warn.copy_fallback"))
            ext2, enc, colour = _edit_target(ctx, f, info)
            filters = _fix_filters(vs) + colour

            def build(e: VEnc) -> list[str]:
                return [*e.input_args(), "-stream_loop", str(times - 1), "-i", _fp(f.path), *_main_maps(info),
                        *e.vf(filters), *e.args(), *_audio_codec_args(ctx, info, ext), "-sn", "-map_metadata", "0",
                        *_mux_args(ext), _fp(tmp)]

            _encode(ctx, enc, build, duration=total)
    return ctx.result([out], size_before=f.size)


@action(id="video.edit.stabilize", label="media.act.stabilize", icon="center_focus_strong", categories={"video"},
        requires=FF, replace_ok=True, description="media.desc.stabilize", order=52)
def video_stabilize(ctx: Context) -> Result:
    """Two-pass vid.stab stabilisation (``deshake`` when ffmpeg lacks vid.stab)."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    if not (_have_filter(ctx, "vidstabdetect") and _have_filter(ctx, "vidstabtransform")):
        ctx.warn(ctx.t("media.warn.no_vidstab"))
        return _filter_edit(ctx, ["deshake"], "sfx.stabilized", sdr=True)
    d = info.duration
    ext, enc, colour = _edit_target(ctx, f, info, sdr=True)
    base = _fix_filters(vs) + colour
    work = ctx.tmpdir()
    detect = ",".join(base + ["vidstabdetect=shakiness=6:accuracy=15:result=transforms.trf"])
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{vs.index}", "-vf", detect, "-an", "-sn", "-f", "null", "-"],
               duration=d, span=(0.0, 0.4), cwd=work, label=ctx.t("media.msg.analyzing"))
    transform = base + ["vidstabtransform=input=transforms.trf:zoom=0:optzoom=1:smoothing=12",
                        "unsharp=5:5:0.8:3:3:0.4"]
    out = ctx.output_path(ext=ext, suffix="sfx.stabilized")
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(transform), *e.args(),
                    *_audio_codec_args(ctx, info, ext), *_sub_args(ctx, info, ext), "-map_metadata", "0",
                    *_mux_args(ext), _fp(tmp)]

        _encode(ctx, enc, build, duration=d, span=(0.4, 1.0), cwd=work)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _fade_seconds(ctx: Context, d: float, default: float) -> float:
    seconds = max(0.1, ctx.pfloat("seconds", default))
    return min(seconds, d / 3.0)


@action(id="video.edit.fade", label="media.act.fade", icon="gradient", categories={"video"}, mode="quick",
        params=[P.float("seconds", "media.prm.fade_seconds", 1.0, min=0.1, max=10, step=0.1, unit="media.unit.s")],
        requires=FF, replace_ok=True, description="media.desc.fade_video", order=53)
def video_fade(ctx: Context) -> Result:
    """Fade in from black and out to black (audio faded too)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    sec = _fade_seconds(ctx, d, 1.0)
    out_at = max(0.0, d - sec)
    vf = [f"fade=t=in:st=0:d={_ts(sec)}", f"fade=t=out:st={_ts(out_at)}:d={_ts(sec)}"]
    af = f"afade=t=in:st=0:d={_ts(sec)},afade=t=out:st={_ts(out_at)}:d={_ts(sec)}" if info.audios else None
    return _filter_edit(ctx, vf, "sfx.faded", af=af)


# ==================================================================================== video: extract

@action(id="video.extract.audio", label="media.act.extract_audio", icon="music_note", categories={"video"},
        requires=FF, description="media.desc.extract_audio", order=60)
def video_extract_audio(ctx: Context) -> Result:
    """Every audio track, stream-copied into the matching container (AAC → .m4a, Opus → .opus…); PCM the WAV
    muxer refuses (big-endian, Blu-ray, DVD) is repacked without loss.  A track that cannot be stored that way
    is re-encoded to FLAC/M4A under the matching extension."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_audio(ctx, f, info)
    outputs: list[Path] = []
    many = len(info.audios) > 1
    total = len(info.audios)
    for i, a in enumerate(info.audios):
        ctx.check_cancel()
        ext, muxer = mu.audio_ext_for_codec(a.codec)
        tag = (a.language or a.title or str(i + 1)) if many else None
        span = (i / total, (i + 1) / total)
        head = ["-i", _fp(f.path), "-map", f"0:{a.index}", "-vn", "-sn", "-dn"]
        out = ctx.output_path(f, ext=ext, suffix=tag)
        codec = mu.wav_pcm_codec(a) if muxer == "wav" else a.codec
        args = [*head, "-c:a", "copy" if codec == a.codec else codec, *_tag_args(info, a)]
        if muxer == "wav":
            args += ["-rf64", "auto"]
        if muxer == "ipod":
            args += ["-movflags", "+faststart"]
        if muxer:
            args += ["-f", muxer]
        try:
            with atomic_target(out) as tmp:
                run_ffmpeg(ctx, [*args, _fp(tmp)], duration=info.duration, span=span, raw=True)
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"audio copy failed ({a.codec}), re-encoding: {exc.details[-300:]}")
            ctx.warn(ctx.t("media.warn.copy_fallback"))
            fallback = mu.audio_target("flac", a) if a.lossless else mu.audio_target("m4a", a)
            if fallback.ext != ext:  # nothing was written under the first name
                out = ctx.output_path(f, ext=fallback.ext, suffix=tag)
            with atomic_target(out) as tmp:
                run_ffmpeg(ctx, [*head, *fallback.args, *_tag_args(info, a), "-f", fallback.muxer, _fp(tmp)],
                           duration=info.duration, span=span, raw=True)
        outputs.append(out)
    return ctx.result(outputs, size_before=f.size)


@action(id="video.extract.snapshot", label="media.act.snapshot", icon="photo_camera", categories={"video"},
        mode="quick", params=[P.slider("position", "media.prm.position", 10, min=0, max=100, step=1,
                                       unit="media.unit.pct")],
        requires=FF, description="media.desc.snapshot", order=61)
def video_snapshot(ctx: Context) -> Result:
    """Full-resolution PNG frame (default at 10 % of the duration)."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    pos = max(0.0, min(100.0, ctx.pfloat("position", 10.0)))
    d = info.duration or 0.0
    t = d * pos / 100.0
    if d and t >= d - 0.05:
        t = max(0.0, d - 0.5)
    filters = (["scale=trunc(iw*sar/2)*2:ih", "setsar=1"] if abs(vs.sar - 1.0) > 0.01 else [])
    filters += _sdr_filters(ctx, vs)
    out = ctx.output_path(ext="png", suffix="sfx.snapshot")
    with atomic_target(out) as tmp:
        for when in (t, 0.0):
            run_ffmpeg(ctx, ["-ss", _ts(when), "-i", _fp(f.path), "-map", f"0:{vs.index}",
                             *(["-vf", ",".join(filters)] if filters else []), "-frames:v", "1", "-update", "1",
                             "-f", "image2", _fp(tmp)], raw=True)
            if tmp.is_file() and tmp.stat().st_size > 0:
                break
        else:
            raise UserError("media.err.no_frames")
    return ctx.result([out])


@action(id="video.extract.frames", label="media.act.frames", icon="burst_mode", categories={"video"},
        mode="dialog", dialog="form",
        params=[P.float("every", "media.prm.every", 1.0, min=0.04, max=3600, step=0.5, unit="media.unit.s"),
                P.select("format", "media.prm.image_format", [("png", "PNG"), ("jpg", "JPG")], "png")],
        requires=FF, description="media.desc.frames", order=62)
def video_frames(ctx: Context) -> Result:
    """One image every N seconds into ``"<stem> (fotogrammi)/fotogramma 00-01-23.png"``."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    every = max(0.04, ctx.pfloat("every", 1.0))
    fmt = "jpg" if ctx.pstr("format", "png") == "jpg" else "png"
    expected = int(d / every) + 1
    if expected > 10000:
        raise UserError("media.err.too_many_frames", n=expected)
    if expected > 500:
        ctx.warn(ctx.t("media.warn.many_frames", n=expected))
    work = ctx.tmpdir() / "frames"
    work.mkdir(parents=True, exist_ok=True)
    filters = (["scale=trunc(iw*sar/2)*2:ih", "setsar=1"] if abs(vs.sar - 1.0) > 0.01 else [])
    filters += _sdr_filters(ctx, vs)
    filters.append(f"fps=1/{mu.fmt_num(every)}")
    quality = ["-q:v", "2"] if fmt == "jpg" else []
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{vs.index}", "-an", "-sn", "-vf", ",".join(filters), *quality,
                     "-f", "image2", f"f%06d.{fmt}"], duration=d, span=(0.0, 0.95), cwd=work)
    produced = sorted(work.glob(f"f*.{fmt}"))
    if not produced:
        raise UserError("media.err.no_frames")
    outdir = ctx.output_dir(f, suffix="sfx.frames")
    word = ctx.t("media.frame")
    with_ms = every < 1 or abs(every - round(every)) > 1e-6
    for k, src in enumerate(produced):
        ctx.check_cancel()
        name = util.safe_filename(f"{word} {mu.timestamp_name(k * every, with_ms)}.{fmt}")
        shutil.move(str(src), str(outdir / name))
    ctx.progress(1.0)
    return ctx.result([outdir], message=i18n.tn("media.msg.frames", len(produced), ctx.lang))


@action(id="video.extract.contact_sheet", label="media.act.contact_sheet", icon="grid_view", categories={"video"},
        mode="quick",
        params=[P.select("grid", "media.prm.grid", [("3x3", "3×3"), ("4x4", "4×4"), ("5x5", "5×5"), ("6x6", "6×6")],
                         "4x4"),
                P.select("format", "media.prm.image_format", [("jpg", "JPG"), ("png", "PNG")], "jpg"),
                P.bool("timestamps", "media.prm.timestamps", True)],
        requires=FF, description="media.desc.contact_sheet", order=63)
def video_contact_sheet(ctx: Context) -> Result:
    """Grid of evenly spaced thumbnails with their timecodes."""
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    m = re.match(r"^(\d)x(\d)$", ctx.pstr("grid", "4x4"))
    cols, rows = (int(m.group(1)), int(m.group(2))) if m else (4, 4)
    n = cols * rows
    fmt = "png" if ctx.pstr("format", "jpg") == "png" else "jpg"
    w, h = vs.display_size
    tile_w = mu.even(min(480, w or 480))
    tile_h = mu.even(tile_w * (h or 270) / (w or 480))
    base = _sdr_filters(ctx, vs) + [f"scale={tile_w}:{tile_h}:flags=lanczos", "setsar=1"]
    use_text = ctx.pbool("timestamps", True) and _have_filter(ctx, "drawtext")
    work = ctx.tmpdir() / "sheet"
    work.mkdir(parents=True, exist_ok=True)
    font = max(12, tile_h // 12)
    for i in range(n):
        ctx.check_cancel()
        t = d * (i + 0.5) / n
        name = f"f{i:03d}.png"
        for attempt in range(2):
            chain = list(base)
            if use_text:
                chain.append(f"drawtext=text='{mu.drawtext_time(t)}':x=8:y=h-th-8:fontsize={font}"
                             ":fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=4")
            try:
                run_ffmpeg(ctx, ["-ss", _ts(t), "-i", _fp(f.path), "-map", f"0:{vs.index}", "-vf", ",".join(chain),
                                 "-frames:v", "1", "-update", "1", "-f", "image2", name],
                           span=(0.9 * i / n, 0.9 * (i + 1) / n), cwd=work)
                break
            except Cancelled:
                raise
            except UserError:
                if use_text and attempt == 0:
                    use_text = False
                    continue
                raise
        if not (work / name).is_file():
            if i == 0:
                raise UserError("media.err.no_frames")
            shutil.copyfile(work / f"f{i - 1:03d}.png", work / name)
    out = ctx.output_path(ext=fmt, suffix="sfx.contact_sheet")
    with atomic_target(out) as tmp:
        quality = ["-q:v", "3"] if fmt == "jpg" else []
        run_ffmpeg(ctx, ["-framerate", "1", "-i", "f%03d.png", "-vf",
                         f"tile={cols}x{rows}:padding=6:margin=6:color=0x101010", "-frames:v", "1", "-update", "1",
                         *quality, "-f", "image2", _fp(tmp)], span=(0.9, 1.0), cwd=work)
    return ctx.result([out])


@action(id="video.extract.subtitles", label="media.act.extract_subtitles", icon="subtitles", categories={"video"},
        requires=FF, description="media.desc.extract_subtitles", order=64)
def video_extract_subtitles(ctx: Context) -> Result:
    """Each embedded subtitle track to ``<stem>.<lang>.srt`` (PGS bitmaps copied as ``.sup``)."""
    f = ctx.file
    info = media_info(ctx, f)
    if not info.subs:
        raise UserError("media.err.no_subtitles", name=f.name)
    usable = [s for s in info.subs if s.text or s.codec == "hdmv_pgs_subtitle"]
    if not usable:
        raise UserError("media.err.image_subs")
    if len(usable) < len(info.subs):
        ctx.warn(ctx.t("media.warn.image_subs_skipped"))
    langs = [s.language for s in usable]
    outputs: list[Path] = []
    total = len(usable)
    for i, s in enumerate(usable):
        ctx.check_cancel()
        ext, codec, muxer = ("srt", "srt", "srt") if s.text else ("sup", "copy", "sup")
        parts = [f.stem]
        if s.language:
            parts.append(s.language)
        if total > 1 and (not s.language or langs.count(s.language) > 1):
            parts.append(str(i + 1))
        if s.forced:
            parts.append("forced")
        out = ctx.output_path(f, name=".".join(parts) + f".{ext}")
        with atomic_target(out) as tmp:
            run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{s.index}", "-c:s", codec, "-f", muxer, _fp(tmp)],
                       duration=info.duration, span=(i / total, (i + 1) / total), raw=True)
        outputs.append(out)
    return ctx.result(outputs, message=i18n.tn("media.msg.subtitle_tracks", len(outputs), ctx.lang))


# ================================================================================== video: subtitles

def _prepare_subtitle(ctx: Context, path: Path) -> Path:
    """Copy a subtitle file into the temp dir as UTF-8 with a safe name."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserError("media.err.subtitle_unreadable", details=str(exc), name=path.name) from None
    if not data.strip():
        raise UserError("media.err.subtitle_unreadable", name=path.name)
    _stem, ext = util.split_name(path.name)
    ext = ext if ext in ("srt", "ass", "ssa", "vtt") else "srt"
    dst = ctx.tmpdir() / f"subs.{ext}"
    dst.write_text(mu.decode_subtitle(data), encoding="utf-8")
    return dst


def _subtitle_source(ctx: Context, f: FileInfo, param: str | None = "subtitle") -> Path:
    """The chosen subtitle file (``param``), else the first sidecar ``<stem>[.<lang>].srt/.ass/.vtt``."""
    raw = ctx.pstr(param, "").strip() if param else ""
    if raw:
        p = Path(util.uri_to_path(raw) if raw.startswith("file:") else os.path.expanduser(raw))
        if not p.is_absolute():
            p = Path(f.path).parent / p
        if not p.is_file():
            raise UserError("err.file_not_found", name=p.name)
        return p
    found = mu.sidecar_subtitles(f.path, prefer_lang=ctx.lang)
    if not found:
        raise UserError("media.err.no_subtitle_file", name=f.stem)
    return found[0]


def _burn(ctx: Context, sub_path: Path) -> Result:
    f = ctx.file
    info = media_info(ctx, f)
    vs = _need_video(ctx, f, info)
    _need_filter(ctx, "subtitles")
    prepared = _prepare_subtitle(ctx, sub_path)
    ext, enc, colour = _edit_target(ctx, f, info)
    filters = _fix_filters(vs) + colour + [f"subtitles=filename={prepared.name}"]
    out = ctx.output_path(ext=ext, suffix="sfx.subtitled")
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            return [*e.input_args(), "-i", _fp(f.path), *_main_maps(info), *e.vf(filters), *e.args(),
                    *_audio_codec_args(ctx, info, ext), "-sn", "-map_metadata", "0", *_mux_args(ext),
                    "-max_muxing_queue_size", "4096", _fp(tmp)]

        _encode(ctx, enc, build, duration=info.duration, cwd=prepared.parent)
    return ctx.result([out], size_before=f.size)


@action(id="video.subtitles.burn", label="media.act.burn_sidecar", icon="subtitles", categories={"video"},
        when=_has_sidecar, requires=FF, description="media.desc.burn_sidecar", order=70)
def video_burn_sidecar(ctx: Context) -> Result:
    """Burn ``<stem>.srt/.ass/.vtt`` (next to the video) into the picture."""
    return _burn(ctx, _subtitle_source(ctx, ctx.file, param=None))


@action(id="video.subtitles.burn_file", label="media.act.burn_file", icon="closed_caption", categories={"video"},
        multi="single", mode="dialog", dialog="form",
        params=[P.path("subtitle", "media.prm.subtitle", "", required=True, help="media.prm.subtitle.help")],
        requires=FF, description="media.desc.burn_file", order=71)
def video_burn_file(ctx: Context) -> Result:
    """Burn a chosen subtitle file into the picture."""
    return _burn(ctx, _subtitle_source(ctx, ctx.file))


@action(id="video.subtitles.embed", label="media.act.embed_subs", icon="closed_caption_add", categories={"video"},
        mode="quick", params=[P.path("subtitle", "media.prm.subtitle", "", help="media.prm.subtitle_embed.help")],
        requires=FF, description="media.desc.embed_subs", order=72)
def video_embed_subtitles(ctx: Context) -> Result:
    """Add a subtitle file as a selectable track (no re-encoding)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    src = _subtitle_source(ctx, f)
    prepared = _prepare_subtitle(ctx, src)
    ext = f.ext if f.ext in EDIT_CONTAINERS else "mkv"
    cont = mu.container_of(ext)
    new_codec = {"mp4": "mov_text", "mov": "mov_text", "webm": "webvtt"}.get(cont, "copy")
    if cont == "mkv" and prepared.suffix == ".vtt":
        new_codec = "webvtt"
    existing = _sub_args(ctx, info, ext)
    n_existing = existing.count("-map")
    lang = mu.lang3(mu.lang_from_name(src.name, f.stem))
    meta = [f"-metadata:s:s:{n_existing}", f"language={lang}"] if lang else []
    maps: list[str] = []
    for v in info.videos:
        maps += ["-map", f"0:{v.index}"]
    for a in info.audios:
        maps += ["-map", f"0:{a.index}"]
    out = ctx.output_path(ext=ext, suffix="sfx.subtitled")
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-i", _fp(prepared), "-c", "copy", *maps, *existing, "-map", "1:0",
                         f"-c:s:{n_existing}", new_codec, *meta, "-map_metadata", "0", *_mux_args(ext), _fp(tmp)],
                   duration=info.duration)
    return ctx.result([out], size_before=f.size)


_P_LANG = P.select("language", "media.prm.language", [("auto", "media.opt.auto"), ("it", "media.opt.lang_it"),
                                                       ("en", "media.opt.lang_en")], "auto")
_P_MODEL = P.path("model", "media.prm.model", "", help="media.prm.model.help")


@action(id="video.subtitles.generate", label="media.act.generate_subs", icon="auto_awesome", categories={"video"},
        mode="quick", params=[_P_LANG, _P_MODEL], requires=["whisper-cli", *FF], description="media.desc.generate_subs",
        order=73)
def video_generate_subtitles(ctx: Context) -> Result:
    """Speech-to-text subtitles (``<stem>.srt`` next to the video) with whisper.cpp."""
    return _transcribe(ctx, "srt")


# ======================================================================================= video: split

@action(id="video.split", label="media.act.split", icon="call_split", categories={"video"}, multi="single",
        mode="dialog", dialog="form",
        params=[P.select("split_mode", "media.prm.split_mode", [("minutes", "media.opt.every_minutes"),
                                                                ("parts", "media.opt.parts")], "minutes"),
                P.float("minutes", "media.prm.minutes", 5.0, min=0.1, max=600, step=0.5, unit="media.unit.min",
                        visible_if={"split_mode": "minutes"}),
                P.int("parts", "media.prm.parts", 2, min=2, max=100, visible_if={"split_mode": "parts"})],
        requires=FF, description="media.desc.split", order=80)
def video_split(ctx: Context) -> Result:
    """Split by time or into equal parts with stream copy (cuts land on keyframes)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_video(ctx, f, info)
    d = _need_duration(ctx, f, info)
    try:
        if ctx.pstr("split_mode", "minutes") == "parts":
            points = mu.split_points(d, parts=max(2, ctx.pint("parts", 2)))
        else:
            points = mu.split_points(d, minutes=max(0.1, ctx.pfloat("minutes", 5.0)))
    except ValueError:
        raise UserError("media.err.split") from None
    if not points:
        raise UserError("media.err.split_short")
    ext, fmt = _copy_container(f)
    outdir = ctx.output_dir(f, suffix="sfx.clips")
    pattern = "./" + mu.segment_pattern(f.stem, ext)
    fmt_opts = ["-segment_format_options", "movflags=+faststart"] if mu.container_of(ext) in ("mp4", "mov") else []
    if fmt:
        fmt_opts += ["-segment_format", fmt[-1]]
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-c", "copy", *_copy_maps(info, ext, ctx), "-f", "segment",
                     "-segment_times", ",".join(f"{p:.3f}" for p in points), "-reset_timestamps", "1",
                     "-segment_start_number", "1", "-map_metadata", "0", *fmt_opts, pattern],
               duration=d, cwd=outdir)
    clips = [p for p in outdir.iterdir() if p.is_file()]
    if not clips:
        raise UserError("err.no_output", name=outdir.name)
    return ctx.result([outdir], message=i18n.tn("media.msg.clips", len(clips), ctx.lang))


# ==================================================================================== video: metadata

def _all_stream_maps(info: mu.MediaInfo, *, pictures: bool = True) -> list[str]:
    maps: list[str] = []
    for v in info.videos:
        maps += ["-map", f"0:{v.index}"]
    if pictures:
        for pic in info.pictures:
            maps += ["-map", f"0:{pic.index}"]
    for a in info.audios:
        maps += ["-map", f"0:{a.index}"]
    return maps


@action(id="video.metadata.edit", label="media.act.metadata", icon="sell", categories={"video"}, multi="single",
        mode="dialog", dialog="metadata", params=[P.metadata("metadata", "media.prm.metadata")], requires=FF,
        replace_ok=True, description="media.desc.metadata_edit", order=90)
def video_metadata_edit(ctx: Context) -> Result:
    """Apply the metadata dialog (set / remove / remove all) with a stream copy."""
    f = ctx.file
    info = media_info(ctx, f)
    sets, removes, remove_all = _parse_metadata_param(ctx.p("metadata"))
    if not (sets or removes or remove_all):
        return ctx.result([], kind="none", message=ctx.t("media.msg.no_changes"))
    meta = ["-map_metadata", "-1"] if remove_all else _tag_args(info, info.audio or info.video)
    for key in removes:
        name = mu.normalize_tag_key(key, ffmpeg=True)
        if name and name != "cover":
            meta += ["-metadata", f"{name}="]
    for key, value in sets.items():
        name = mu.normalize_tag_key(key, ffmpeg=True)
        if name and name != "cover":
            meta += ["-metadata", f"{name}={value}"]
    ext, fmt = _copy_container(f)
    out = ctx.output_path(ext=ext, suffix="sfx.edited")
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-c", "copy", *_all_stream_maps(info),
                         *_sub_args(ctx, info, ext, warn=False), *meta, *_mux_args(ext), *fmt, _fp(tmp)],
                   duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


@action(id="video.metadata.strip", label="media.act.strip_metadata", icon="privacy_tip", categories={"video"},
        requires=FF, replace_ok=True, description="media.desc.strip_metadata", order=91)
def video_metadata_strip(ctx: Context) -> Result:
    """Remove every tag, chapter, cover and data track (GPS telemetry included); streams are copied."""
    f = ctx.file
    info = media_info(ctx, f)
    ext, fmt = _copy_container(f)
    out = ctx.output_path(ext=ext, suffix="sfx.no_metadata")
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-c", "copy", *_all_stream_maps(info, pictures=False),
                         *_sub_args(ctx, info, ext, warn=False), "-map_metadata", "-1", "-map_metadata:s", "-1",
                         "-map_chapters", "-1", "-fflags", "+bitexact", *_mux_args(ext), *fmt, _fp(tmp)],
                   duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


_LOCATION_RE = re.compile(r"location|gps|xyz|geo", re.I)


@action(id="video.metadata.gps", label="media.act.remove_gps", icon="location_off", categories={"video"},
        requires=FF, replace_ok=True, description="media.desc.remove_gps", order=92)
def video_metadata_gps(ctx: Context) -> Result:
    """Remove location tags and telemetry tracks, keep everything else."""
    f = ctx.file
    info = media_info(ctx, f)
    keys = [k for k in info.tags if _LOCATION_RE.search(k)]
    if not keys and not info.data_streams:
        return ctx.result([], kind="none", message=ctx.t("media.msg.no_gps"))
    ext, fmt = _copy_container(f)
    meta: list[str] = []
    for key in keys:
        meta += ["-metadata", f"{key}="]
    out = ctx.output_path(ext=ext, suffix="sfx.no_gps")
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-c", "copy", *_all_stream_maps(info),
                         *_sub_args(ctx, info, ext, warn=False), "-map_metadata", "0", *meta, *_mux_args(ext),
                         *fmt, _fp(tmp)], duration=info.duration)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


# ===================================================================================== audio: convert

def _audio_convert_action(ext: str, order: int, *, params: Sequence = (), banned: Sequence[str] = (),
                          mode: str = "instant") -> None:
    text = ext.upper()

    @action(id=f"audio.convert.{ext}", label=text, text=text, icon="music_note", categories={"audio"}, mode=mode,
            params=list(params), when=_not_ext(ext, *banned), requires=FF, description=f"media.desc.to_{ext}",
            order=order)
    def _convert(ctx: Context) -> Result:
        return _convert_audio(ctx, ext)

    _convert.__name__ = f"audio_to_{ext}"
    _convert.__doc__ = f"Convert to {text}."


_audio_convert_action("mp3", 10, params=[_P_MP3_Q], mode="quick")
_audio_convert_action("m4a", 11, params=[_P_AAC_BR], banned=("m4b",), mode="quick")
_audio_convert_action("opus", 12, params=[_P_OPUS_BR], mode="quick")
_audio_convert_action("ogg", 13, banned=("oga",))
_audio_convert_action("flac", 14)
_audio_convert_action("wav", 15, banned=("wave",))
_audio_convert_action("aiff", 16, banned=("aif", "aifc"))


@action(id="audio.convert.m4r", label="M4R", text="M4R", icon="ring_volume", categories={"audio"}, mode="quick",
        params=[P.time("start", "media.prm.ringtone_start", 0.0)], when=_not_ext("m4r"), requires=FF,
        description="media.desc.to_m4r", order=17)
def audio_to_m4r(ctx: Context) -> Result:
    """iPhone ringtone: AAC in an ``.m4r`` (ipod) container, at most 40 s (faded out when cut)."""
    f = ctx.file
    info = media_info(ctx, f)
    _need_audio(ctx, f, info)
    d = _need_duration(ctx, f, info)
    start = max(0.0, min(_time_param(ctx, "start") or 0.0, max(0.0, d - 1.0)))
    length = min(RINGTONE_MAX, d - start)
    af = None
    if d - start > RINGTONE_MAX:
        ctx.warn(ctx.t("media.warn.ringtone_cut", seconds=int(RINGTONE_MAX)))
        af = f"afade=t=out:st={_ts(length - 1.5)}:d=1.5"
    target = mu.audio_target("m4r", info.audios[0], ctx.config)
    out = ctx.output_path(ext="m4r")
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=length, af=af, start=start or None, limit=length,
                   keep_cover=False)
    return ctx.result([out], size_before=f.size)


# ==================================================================================== audio: compress

_AUDIO_KBPS = {"high": 192, "balanced": 128, "small": 96}
_OPUS_KBPS = {"high": 128, "balanced": 96, "small": 64}
_LOSSY_SAME = frozenset({"mp3", "m4a", "m4b", "aac", "opus", "ogg", "oga", "wma", "weba"})
_PCM_EXTS = frozenset({"wav", "wave", "aif", "aiff", "aifc"})


def _compress_audio(ctx: Context, preset: str) -> Result:
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    if preset == "voice":
        target = mu.AudioTarget("opus", ["-c:a", _encoder(ctx, "libopus"), "-b:a", "32k", "-ac", "1",
                                         "-application", "voip"], muxer="opus", codec="libopus")
    elif a.lossless or f.ext not in _LOSSY_SAME:
        if preset == "high" and f.ext in _PCM_EXTS:
            target = mu.audio_target("flac", a)
        else:
            target = mu.audio_target("mp3", a, ctx.config, bitrate=_AUDIO_KBPS[preset])
    else:
        kbps = (_OPUS_KBPS if a.codec == "opus" else _AUDIO_KBPS)[preset]
        target = mu.same_format_audio(f.ext, a, ctx.config, bitrate=kbps)
        wanted = mu.aac_kbps(kbps, a.channels) if target.codec == "aac" else kbps
        if a.bit_rate and a.bit_rate <= wanted * 1000 * 1.05:
            return ctx.result([], kind="none", size_before=f.size, message=ctx.t("warn.already_optimized"))
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    out = ctx.output_path(ext=target.ext, suffix="sfx.compressed")
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=info.duration)
    if f.size and out.stat().st_size >= f.size:
        return _passthrough(ctx, out, f)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _audio_compress_action(preset: str, label: str, icon: str, order: int) -> None:
    @action(id=f"audio.compress.{preset}", label=label, icon=icon, categories={"audio"}, when=_audio_files_ok,
            requires=FF, replace_ok=True, description=f"media.desc.audio_compress_{preset}", order=order)
    def _compress(ctx: Context) -> Result:
        return _compress_audio(ctx, preset)

    _compress.__name__ = f"audio_compress_{preset}"
    _compress.__doc__ = f"Compress audio ({preset})."


_audio_compress_action("balanced", "media.act.balanced", "compress", 20)
_audio_compress_action("high", "media.act.high_quality", "high_quality", 21)
_audio_compress_action("small", "media.act.small", "compress", 22)
_audio_compress_action("voice", "media.act.voice", "record_voice_over", 23)


# =================================================================================== audio: normalize

_TP = -1.0
_LRA = 11.0


def _sample_rate_for(target: mu.AudioTarget, a: mu.AStream) -> int:
    rate = a.sample_rate or 48000
    if target.codec == "libopus":
        return 48000
    if target.codec in ("libmp3lame", "libvorbis", "aac", "wmav2", "ac3"):
        return min(rate, 48000)
    return rate


def _normalize(ctx: Context, lufs: float) -> Result:
    """EBU R128 two-pass loudness normalisation (measured values fed back, linear mode)."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    d = info.duration
    target = mu.same_format_audio(f.ext, a, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    res = run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{a.index}", "-vn", "-sn", "-dn",
                           "-af", mu.loudnorm_filter(lufs, _TP, _LRA), "-f", "null", "-"],
                     duration=d, span=(0.0, 0.45), loglevel="info", label=ctx.t("media.msg.analyzing"))
    stderr = res.stderr or ""
    try:
        measured = mu.parse_loudnorm(stderr)
    except ValueError:
        if re.search(r'"input_i"\s*:\s*"-inf"', stderr):
            raise UserError("media.err.silent", name=f.name) from None
        raise UserError("media.err.loudnorm", details="\n".join(stderr.splitlines()[-15:])) from None
    if abs(measured["input_i"] - lufs) <= 0.5 and measured["input_tp"] <= _TP + 0.2:
        return ctx.result([], kind="none", message=ctx.t("media.msg.already_normalized", lufs=mu.fmt_num(lufs)))
    rate = _sample_rate_for(target, a)
    out = ctx.output_path(ext=target.ext, suffix="sfx.normalized")
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=d, af=mu.loudnorm_filter(lufs, _TP, _LRA, measured),
                   extra=["-ar", str(rate)], span=(0.45, 1.0))
    final = ctx.replace_original(f, out)
    message = ctx.t("media.msg.loudness", before=f"{measured['input_i']:.1f}", after=mu.fmt_num(lufs))
    return ctx.result([final], size_before=f.size, message=message)


def _normalize_action(key: str, lufs: float, order: int) -> None:
    text = f"−{mu.fmt_num(abs(lufs))}"

    @action(id=f"audio.normalize.{key}", label=f"media.act.norm_{key}", text=text, icon="equalizer",
            categories={"audio"}, when=_audio_files_ok, requires=FF, replace_ok=True,
            description=f"media.desc.norm_{key}", order=order)
    def _norm(ctx: Context) -> Result:
        return _normalize(ctx, lufs)

    _norm.__name__ = f"audio_normalize_{key}"
    _norm.__doc__ = f"Normalise to {lufs} LUFS (true peak {_TP} dBTP)."


_normalize_action("streaming", -14.0, 30)
_normalize_action("podcast", -16.0, 31)
_normalize_action("broadcast", -23.0, 32)


# ======================================================================================= audio: trim

@action(id="audio.trim", label="media.act.trim", icon="content_cut", categories={"audio"}, multi="single",
        mode="dialog", dialog="trim", when=_audio_files_ok, params=[_P_START, _P_END, _P_TRIM_MODE], requires=FF,
        replace_ok=True, description="media.desc.trim_audio", order=25)
def audio_trim(ctx: Context) -> Result:
    """Keep (or cut out) a range; always re-encoded for sample-accurate cuts."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    d = _need_duration(ctx, f, info)
    start, end = _range(ctx, d)
    mode, start, end = _cut_ranges(start, end, d, ctx.pstr("mode", "keep"))
    target = mu.same_format_audio(f.ext, a, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    out = ctx.output_path(ext=target.ext, suffix="sfx.trimmed")
    with atomic_target(out) as tmp:
        if mode == "keep":
            _audio_out(ctx, f, info, target, tmp, duration=end - start, start=start or None, limit=end - start)
        else:
            graph = (f"[0:{a.index}]asplit=2[s0][s1];[s0]atrim=end={_ts(start)},asetpts=PTS-STARTPTS[a0];"
                     f"[s1]atrim=start={_ts(end)},asetpts=PTS-STARTPTS[a1];[a0][a1]concat=n=2:v=0:a=1[a]")
            args = ["-i", _fp(f.path), "-filter_complex", graph, "-map", "[a]", *target.args, *_tag_args(info, a)]
            if target.muxer:
                args += ["-f", target.muxer]
            run_ffmpeg(ctx, [*args, _fp(tmp)], duration=d - (end - start), raw=True)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


# ======================================================================================= audio: edit

def _audio_filter(ctx: Context, af: str | None, suffix: str, *, extra: Sequence[str] = (),
                  duration: float | None = None) -> Result:
    """Re-encode the audio through ``af`` in its own format."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    target = mu.same_format_audio(f.ext, a, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    out = ctx.output_path(ext=target.ext, suffix=suffix)
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=duration or info.duration, af=af, extra=extra)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


def _speed_audio(ctx: Context, factor: float) -> Result:
    if not (0.1 <= factor <= 16):
        raise UserError("media.err.speed")
    f = ctx.file
    info = media_info(ctx, f)
    d = info.duration
    return _audio_filter(ctx, mu.atempo_chain(factor), _speed_suffix(ctx, factor),
                         duration=(d / factor) if d else None)


for _cat, _factors in (("video", ((2.0, 55), (1.5, 56), (4.0, 57), (0.5, 58))),
                       ("audio", ((1.25, 40), (1.5, 41), (2.0, 42), (0.75, 43), (0.5, 44)))):
    for _factor, _ord in _factors:
        _speed_action(_cat, _factor, _ord)


@action(id="audio.speed.custom", label="media.act.speed_custom", icon="tune", categories={"audio"}, mode="dialog",
        dialog="form", when=_audio_files_ok, params=[_P_FACTOR], requires=FF, replace_ok=True,
        description="media.desc.speed_audio", order=45)
def audio_speed_custom(ctx: Context) -> Result:
    """Any speed between 0.25× and 8× (pitch preserved)."""
    return _speed_audio(ctx, ctx.pfloat("factor", 1.25))


@action(id="audio.edit.fade", label="media.act.fade", icon="gradient", categories={"audio"}, mode="quick",
        when=_audio_files_ok,
        params=[P.float("seconds", "media.prm.fade_seconds", 2.0, min=0.1, max=30, step=0.5, unit="media.unit.s")],
        requires=FF, replace_ok=True, description="media.desc.fade_audio", order=46)
def audio_fade(ctx: Context) -> Result:
    """Fade in at the start and out at the end."""
    f = ctx.file
    info = media_info(ctx, f)
    d = _need_duration(ctx, f, info)
    sec = _fade_seconds(ctx, d, 2.0)
    af = f"afade=t=in:st=0:d={_ts(sec)},afade=t=out:st={_ts(max(0.0, d - sec))}:d={_ts(sec)}"
    return _audio_filter(ctx, af, "sfx.faded")


@action(id="audio.edit.silence", label="media.act.remove_silence", icon="graphic_eq", categories={"audio"},
        mode="quick", when=_audio_files_ok,
        params=[P.int("threshold", "media.prm.threshold", -50, min=-90, max=-20, unit="media.unit.db")],
        requires=FF, replace_ok=True, description="media.desc.remove_silence", order=47)
def audio_remove_silence(ctx: Context) -> Result:
    """Trim silence at the beginning and at the end (silencedetect, then an exact cut)."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    d = _need_duration(ctx, f, info)
    threshold = max(-90, min(-20, ctx.pint("threshold", -50)))
    res = run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{a.index}", "-vn", "-sn", "-dn",
                           "-af", f"silencedetect=noise={threshold}dB:d=0.3", "-f", "null", "-"],
                     duration=d, span=(0.0, 0.4), loglevel="info", label=ctx.t("media.msg.analyzing"))
    silences = mu.parse_silencedetect(res.stderr or "")
    start, end = mu.silence_trim_bounds(silences, d)
    if end - start < 0.1:
        raise UserError("media.err.all_silent", name=f.name)
    if start <= 0.05 and end >= d - 0.05:
        return ctx.result([], kind="none", message=ctx.t("media.msg.no_silence"))
    target = mu.same_format_audio(f.ext, a, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    out = ctx.output_path(ext=target.ext, suffix="sfx.no_silence")
    with atomic_target(out) as tmp:
        _audio_out(ctx, f, info, target, tmp, duration=end - start, start=start or None, limit=end - start,
                   span=(0.4, 1.0))
    final = ctx.replace_original(f, out)
    removed = d - (end - start)
    return ctx.result([final], size_before=f.size,
                      message=ctx.t("media.msg.silence_removed", seconds=f"{removed:.1f}".replace(
                          ".", "," if ctx.lang == "it" else ".")))


_DENOISE = {"light": 6, "medium": 12, "strong": 20}


@action(id="audio.edit.denoise", label="media.act.denoise", icon="noise_control_off", categories={"audio"},
        mode="quick", when=_audio_files_ok,
        params=[P.select("strength", "media.prm.strength", [("light", "media.opt.light"),
                                                            ("medium", "media.opt.medium"),
                                                            ("strong", "media.opt.strong")], "medium")],
        requires=FF, replace_ok=True, description="media.desc.denoise", order=48)
def audio_denoise(ctx: Context) -> Result:
    """FFT noise reduction (``afftdn`` with noise tracking)."""
    nr = _DENOISE.get(ctx.pstr("strength", "medium"), 12)
    return _audio_filter(ctx, f"afftdn=nr={nr}:nf=-50:tn=1", "sfx.denoised")


def _volume_af(db: float) -> str:
    chain = f"volume={mu.fmt_num(db)}dB"
    if db > 0:
        chain += ",alimiter=limit=0.966:level=0"
    return chain


def _volume_action(key: str, db: float, order: int) -> None:
    sign = "+" if db > 0 else "−"
    label = f"{sign}{mu.fmt_num(abs(db))} dB"

    @action(id=f"audio.volume.{key}", label=label, text=f"{sign}{mu.fmt_num(abs(db))}", icon="volume_up",
            categories={"audio"}, when=_audio_files_ok, requires=FF, replace_ok=True,
            description="media.desc.volume", order=order)
    def _volume(ctx: Context) -> Result:
        return _audio_filter(ctx, _volume_af(db), "sfx.louder" if db > 0 else "sfx.quieter")

    _volume.__name__ = f"audio_volume_{key}"
    _volume.__doc__ = f"Change the volume by {db:+g} dB (limiter on boosts)."


_volume_action("up3", 3.0, 50)
_volume_action("up6", 6.0, 51)
_volume_action("down3", -3.0, 52)
_volume_action("down6", -6.0, 53)


@action(id="audio.volume.custom", label="media.act.volume_custom", icon="tune", categories={"audio"},
        mode="dialog", dialog="form", when=_audio_files_ok,
        params=[P.float("db", "media.prm.db", 3.0, min=-30, max=30, step=0.5, unit="media.unit.db")],
        requires=FF, replace_ok=True, description="media.desc.volume", order=54)
def audio_volume_custom(ctx: Context) -> Result:
    """Any gain between −30 and +30 dB."""
    db = max(-30.0, min(30.0, ctx.pfloat("db", 3.0)))
    if abs(db) < 0.05:
        raise UserError("media.err.volume_zero")
    return _audio_filter(ctx, _volume_af(db), "sfx.louder" if db > 0 else "sfx.quieter")


@action(id="audio.channels.mono", label="media.act.mono", icon="speaker", categories={"audio"},
        when=_audio_files_ok, requires=FF, replace_ok=True, description="media.desc.mono", order=55)
def audio_mono(ctx: Context) -> Result:
    """Downmix to one channel."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    if a.channels == 1:
        raise UserError("media.err.already_mono", name=f.name)
    return _audio_filter(ctx, None, "sfx.mono", extra=["-ac", "1"])


@action(id="audio.channels.stereo", label="media.act.stereo", icon="surround_sound", categories={"audio"},
        when=_audio_files_ok, requires=FF, replace_ok=True, description="media.desc.stereo", order=56)
def audio_stereo(ctx: Context) -> Result:
    """Two channels (mono duplicated, surround downmixed)."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    if a.channels == 2:
        raise UserError("media.err.already_stereo", name=f.name)
    return _audio_filter(ctx, None, "sfx.stereo", extra=["-ac", "2"])


@action(id="audio.edit.reverse", label="media.act.reverse", icon="fast_rewind", categories={"audio"},
        when=_audio_files_ok, requires=FF, replace_ok=True, description="media.desc.reverse_audio", order=57)
def audio_reverse(ctx: Context) -> Result:
    """Play backwards (segmented to bound memory)."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    target = mu.same_format_audio(f.ext, a, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    out = ctx.output_path(ext=target.ext, suffix="sfx.reversed")
    with atomic_target(out) as tmp:
        _reverse_media(ctx, f, info, tmp, video=False, target=target)
    final = ctx.replace_original(f, out)
    return ctx.result([final], size_before=f.size)


# ================================================================================== audio: transcribe

_MODEL_PREFERENCE = ("large-v3-turbo-q5_0", "large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")


def _whisper_dir() -> Path:
    return util.xdg_data_home() / "dropwheel" / "whisper"


def _model_rank(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    for i, key in enumerate(_MODEL_PREFERENCE):
        if f"ggml-{key}." in name or f"ggml-{key}-" in name:
            return i, name
    return len(_MODEL_PREFERENCE), name


def _whisper_model(ctx: Context) -> Path:
    """Model file: the ``model`` param, ``whisper.model``/``audio.whisper_model`` from the config,
    ``~/.local/share/dropwheel/whisper/ggml-base.bin``, any ggml model there, then the AUR packages."""
    candidates: list[str] = []
    raw = ctx.pstr("model", "").strip()
    if raw:
        candidates.append(raw)
    for key in ("whisper.model", "audio.whisper_model"):
        value = cfgmod.get(ctx.config, key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for cand in candidates:
        p = Path(os.path.expanduser(cand))
        if p.is_file():
            return p
        named = _whisper_dir() / f"ggml-{cand}.bin"
        if named.is_file():
            return named
    if raw:
        raise UserError("err.file_not_found", name=raw)
    base = _whisper_dir()
    default = base / "ggml-base.bin"
    if default.is_file():
        return default
    found: list[Path] = []
    try:
        found = [p for p in base.glob("ggml-*.bin") if p.is_file()]
    except OSError:
        found = []
    for pattern_dir in (Path("/usr/share"),):
        try:
            found += [p for p in pattern_dir.glob("whisper.cpp-model-*/ggml-*.bin") if p.is_file()]
            found += [p for p in (pattern_dir / "whisper.cpp" / "models").glob("ggml-*.bin") if p.is_file()]
        except OSError:
            pass
    if found:
        return sorted(found, key=_model_rank)[0]
    command = (f"mkdir -p {shlex.quote(str(base))} && curl -L --fail -o {shlex.quote(str(default))} "
               f"{WHISPER_URL}")
    raise UserError("media.err.whisper_model", details=ctx.t("media.err.whisper_model_help", path=str(default),
                                                             url=WHISPER_URL),
                    install=command, path=str(default))


def _transcribe(ctx: Context, fmt: str) -> Result:
    """whisper.cpp transcription to ``<stem>.<fmt>`` (16 kHz mono WAV prepared first)."""
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    d = info.duration
    model = _whisper_model(ctx)
    lang = ctx.pstr("language", "auto")
    if lang not in ("auto", "it", "en") and not re.match(r"^[a-z]{2}$", lang):
        lang = "auto"
    work = ctx.tmpdir() / "whisper"
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "audio.wav"
    run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{a.index}", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
                     "-c:a", "pcm_s16le", "-f", "wav", _fp(wav)], duration=d, span=(0.0, 0.1),
               label=ctx.t("media.msg.preparing"))
    threads = max(1, min(8, os.cpu_count() or 4))
    label = ctx.t("media.msg.transcribing")
    ctx.progress(0.1, label)

    def on_line(line: str) -> None:
        pct = mu.whisper_progress(line)
        if pct is not None:
            ctx.progress(0.1 + 0.9 * pct / 100.0, label)

    ctx.run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", lang, "-t", str(threads), "-pp",
             "-of", str(work / "transcript"), f"-o{fmt}"], on_line=on_line, stderr_lines=True, cwd=work)
    produced = work / f"transcript.{fmt}"
    if not produced.is_file():
        raise UserError("media.err.whisper_failed")
    text = produced.read_text(encoding="utf-8", errors="replace")
    if fmt == "txt":
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        text = text + "\n" if text else ""
    if not text.strip():
        raise UserError("media.err.whisper_empty")
    out = ctx.output_path(f, ext=fmt)
    with atomic_target(out) as tmp:
        tmp.write_text(text, encoding="utf-8")
    preview = text if fmt == "txt" else None
    return ctx.result([out], text=preview)


def _transcribe_action(fmt: str, order: int) -> None:
    text = fmt.upper()

    @action(id=f"audio.transcribe.{fmt}", label=text, text=text, icon="record_voice_over", categories={"audio"},
            mode="quick", when=_audio_files_ok, params=[_P_LANG, _P_MODEL], requires=["whisper-cli", *FF],
            description=f"media.desc.transcribe_{fmt}", order=order)
    def _tr(ctx: Context) -> Result:
        return _transcribe(ctx, fmt)

    _tr.__name__ = f"audio_transcribe_{fmt}"
    _tr.__doc__ = f"Speech to text ({text})."


_transcribe_action("txt", 60)
_transcribe_action("srt", 61)
_transcribe_action("vtt", 62)


# =================================================================================== audio: visualize

_VIS_SIZES = {"square": (1080, 1080), "landscape": (1920, 1080), "portrait": (1080, 1920)}


def _visualize(ctx: Context, kind: str) -> Result:
    f = ctx.file
    info = media_info(ctx, f)
    a = _need_audio(ctx, f, info)
    d = _need_duration(ctx, f, info)
    width, height = _VIS_SIZES[kind]
    band = height // 3 if width >= height else height // 4
    colour = ctx.pstr("color", "#FF8C42").strip().lstrip("#")
    if not re.match(r"^[0-9A-Fa-f]{6}$", colour):
        colour = "FF8C42"
    enc = _make_venc(ctx, "h264", None, crf=23)
    out = ctx.output_path(ext="mp4", suffix="sfx.waveform")
    audio = ["-c:a", "copy"] if a.codec == "aac" else ["-c:a", "aac", "-b:a", f"{mu.aac_kbps(192, a.channels)}k"]
    with atomic_target(out) as tmp:
        def build(e: VEnc) -> list[str]:
            chain = e.chain([f"showwaves=s={width}x{band}:mode=cline:rate=30:colors=0x{colour}:scale=sqrt",
                             f"pad={width}:{height}:0:(oh-ih)/2:color=black", "format=yuv420p"])
            graph = f"[0:{a.index}]aformat=channel_layouts=mono,{chain}[v]"
            return [*e.input_args(), "-i", _fp(f.path), "-filter_complex", graph, "-map", "[v]",
                    "-map", f"0:{a.index}", *e.args(), *audio, "-shortest", *_tag_args(info, a),
                    "-movflags", "+faststart", _fp(tmp)]

        _encode(ctx, enc, build, duration=d)
    return ctx.result([out], size_before=f.size)


def _visualize_action(kind: str, text: str, order: int) -> None:
    @action(id=f"audio.visualize.{kind}", label=f"media.act.vis_{kind}", text=text, icon="graphic_eq",
            categories={"audio"}, mode="quick", when=_audio_files_ok,
            params=[P.color("color", "media.prm.color", "#FF8C42")], requires=FF,
            description="media.desc.visualize", order=order)
    def _vis(ctx: Context) -> Result:
        return _visualize(ctx, kind)

    _vis.__name__ = f"audio_visualize_{kind}"
    _vis.__doc__ = f"Waveform video ({kind})."


_visualize_action("square", "1:1", 70)
_visualize_action("landscape", "16:9", 71)
_visualize_action("portrait", "9:16", 72)


# ======================================================================================== audio: tags

_ASF_KEYS = {"title": "Title", "artist": "Author", "album": "WM/AlbumTitle", "albumartist": "WM/AlbumArtist",
             "date": "WM/Year", "tracknumber": "WM/TrackNumber", "discnumber": "WM/PartOfSet", "genre": "WM/Genre",
             "composer": "WM/Composer", "comment": "Description", "copyright": "Copyright"}


def _cover_bytes(ctx: Context, path: Path) -> tuple[bytes, str]:
    """JPEG/PNG bytes of an image file (other formats converted with Pillow, else ffmpeg)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserError("media.err.cover_image", details=str(exc), name=path.name) from None
    if data.startswith(b"\xff\xd8"):
        return data, "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None:
        import io

        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im = im.convert("RGB")
                im.thumbnail((1600, 1600))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=90)
                return buf.getvalue(), "image/jpeg"
        except Exception as exc:
            raise UserError("media.err.cover_image", details=str(exc), name=path.name) from None
    tmp = ctx.tmpdir() / "cover-converted.jpg"
    run_ffmpeg(ctx, ["-i", _fp(path), "-frames:v", "1", "-update", "1", "-q:v", "2", "-f", "image2", _fp(tmp)],
               raw=True)
    return tmp.read_bytes(), "image/jpeg"


def _mutagen_tags(path: Path, sets: dict[str, str], removes: list[str], remove_all: bool) -> list[str]:
    """Apply text tags with mutagen's "easy" interface; returns the keys this format cannot store."""
    import mutagen  # type: ignore

    unsupported: list[str] = []
    audio = mutagen.File(str(path), easy=True)
    if audio is None:
        raise ValueError("format not supported by mutagen")
    # These expose the raw ID3 object: "del audio[key]" finds nothing and a remove-only edit would do nothing.
    if type(audio).__name__ in ("WAVE", "AIFF", "DSF", "DSDIFF"):
        raise ValueError("no easy tag interface")
    if remove_all:
        audio.delete()
        audio = mutagen.File(str(path), easy=True)
        if audio is None:
            raise ValueError("format not supported by mutagen")
    if audio.tags is None:
        audio.add_tags()
    is_asf = type(audio).__name__ == "ASF"

    def key_for(canon: str) -> str:
        return _ASF_KEYS.get(canon, canon) if is_asf else canon

    for key in removes:
        canon = mu.normalize_tag_key(key)
        if canon == "cover":
            continue
        for cand in dict.fromkeys((key_for(canon), key)):
            try:
                del audio[cand]
            except (KeyError, ValueError, TypeError):
                pass
    for key, value in sets.items():
        canon = mu.normalize_tag_key(key)
        if canon == "cover":
            continue
        name = key_for(canon)
        try:
            if value.strip() == "":
                try:
                    del audio[name]
                except KeyError:
                    pass
            else:
                audio[name] = [value]
        except (KeyError, ValueError, TypeError):
            unsupported.append(key)
    if type(audio).__name__ == "EasyMP3":
        audio.save(v2_version=3)
    else:
        audio.save()
    return unsupported


def _mutagen_set_cover(path: Path, cover: tuple[bytes, str] | None, drop: bool) -> bool:
    """Embed (or remove) the front cover; ``False`` when mutagen cannot do it for this format."""
    import mutagen  # type: ignore
    from mutagen.flac import FLAC, Picture  # type: ignore
    from mutagen.id3 import APIC, ID3FileType  # type: ignore
    from mutagen.mp4 import MP4, MP4Cover  # type: ignore

    audio = mutagen.File(str(path))
    if audio is None:
        return False
    data, mime = cover if cover else (b"", "")
    if isinstance(audio, ID3FileType):
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        if cover:
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        audio.save(v2_version=3)
        return True
    if isinstance(audio, MP4):
        if audio.tags is None:
            audio.add_tags()
        if cover:
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        elif drop:
            audio.tags.pop("covr", None)
        audio.save()
        return True
    if isinstance(audio, FLAC):
        audio.clear_pictures()
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = data
            audio.add_picture(pic)
        audio.save()
        return True
    if type(audio).__name__ in ("OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
        if audio.tags is None:
            audio.add_tags()
        for key in ("metadata_block_picture", "coverart", "coverartmime"):
            if key in audio.tags:
                del audio.tags[key]
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = data
            audio.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        audio.save()
        return True
    return False


def _ffmpeg_tags(ctx: Context, f: FileInfo, info: mu.MediaInfo, tmp: Path, sets: dict[str, str], removes: list[str],
                 remove_all: bool, cover: tuple[bytes, str] | None, drop_cover: bool) -> None:
    """Tag writing with an ffmpeg stream copy (used without mutagen or for formats it does not handle)."""
    args = ["-i", _fp(f.path)]
    cover_file: Path | None = None
    if cover and f.ext not in ("mp3", "m4a", "m4b", "mp4", "flac"):
        ctx.warn(ctx.t("media.warn.cover_unsupported"))
        cover = None
    if cover:
        cover_file = ctx.tmpdir() / ("tag-cover.png" if cover[1] == "image/png" else "tag-cover.jpg")
        cover_file.write_bytes(cover[0])
        args += ["-i", _fp(cover_file)]
    args += ["-c", "copy"]
    for a in info.audios:
        args += ["-map", f"0:{a.index}"]
    if cover_file is not None:
        args += ["-map", "1:0", "-disposition:v:0", "attached_pic"]
    elif not drop_cover and not remove_all and info.pictures:
        if info.stream_tags:  # the Ogg muxer refuses picture streams ("Unsupported codec id")
            ctx.warn(ctx.t("media.warn.cover_unsupported"))
        else:
            for pic in info.pictures:
                args += ["-map", f"0:{pic.index}"]
    args += ["-map_metadata", "-1"] if remove_all else _tag_args(info, info.audios[0])
    for key in removes:
        name = mu.normalize_tag_key(key, ffmpeg=True)
        if name and name != "cover":
            args += ["-metadata", f"{name}="]
    for key, value in sets.items():
        name = mu.normalize_tag_key(key, ffmpeg=True)
        if name and name != "cover":
            args += ["-metadata", f"{name}={value}"]
    if f.ext == "mp3":
        args += ["-id3v2_version", "3"]
    if f.ext in ("aif", "aiff", "aifc"):
        args += ["-write_id3v2", "1"]  # the muxer only stores the title, comment and copyright otherwise
    if mu.container_of(f.ext) in ("mp4", "mov") or f.ext in ("m4a", "m4b"):
        args += ["-movflags", "+faststart"]
    run_ffmpeg(ctx, [*args, _fp(tmp)], duration=info.duration)


@action(id="audio.tags", label="media.act.tags", icon="sell", categories={"audio"}, multi="single", mode="dialog",
        dialog="metadata", when=_audio_files_ok,
        params=[P.metadata("metadata", "media.prm.metadata"),
                P.path("cover", "media.prm.cover", "", help="media.prm.cover.help")],
        requires=[("py:mutagen", "ffmpeg")], replace_ok=True, description="media.desc.tags", order=35)
def audio_tags(ctx: Context) -> Result:
    """Edit title/artist/album/year/track/genre/cover on a copy ("<stem> (modificato)"; the standard
    "Replace original" switch, off by default, swaps it in place with a backup for Undo).

    mutagen writes the tags when installed (any format it knows), otherwise ffmpeg stream-copies
    the file with ``-metadata`` options."""
    f = ctx.file
    sets, removes, remove_all = _parse_metadata_param(ctx.p("metadata"))
    cover_path = ctx.pstr("cover", "").strip()
    for key in list(sets):
        if mu.normalize_tag_key(key) == "cover":
            value = sets.pop(key).strip()
            if value:
                cover_path = value
    drop_cover = any(mu.normalize_tag_key(k) == "cover" for k in removes)
    if not (sets or removes or remove_all or cover_path):
        return ctx.result([], kind="none", message=ctx.t("media.msg.no_changes"))
    cover: tuple[bytes, str] | None = None
    if cover_path:
        p = Path(util.uri_to_path(cover_path) if cover_path.startswith("file:") else os.path.expanduser(cover_path))
        if not p.is_file():
            raise UserError("err.file_not_found", name=p.name)
        cover = _cover_bytes(ctx, p)
    unsupported: list[str] = []
    out = ctx.output_path(suffix="sfx.edited")
    with atomic_target(out) as tmp:
        done = False
        if tools.have("py:mutagen"):
            shutil.copy2(f.path, tmp)
            try:
                unsupported = _mutagen_tags(tmp, sets, removes, remove_all)
                if sets and len(unsupported) == len(sets):
                    raise ValueError("no tag supported by mutagen for this format")
                if (cover or drop_cover or remove_all) and not _mutagen_set_cover(tmp, cover, drop_cover or remove_all):
                    if cover:
                        raise ValueError("cover not supported by mutagen for this format")
                done = True
            except Cancelled:
                raise
            except Exception as exc:  # mutagen raises many exception types for odd files
                ctx.log(f"mutagen failed, using ffmpeg: {exc!r}")
                unsupported = []
        if not done:
            if not tools.have("ffmpeg") or not tools.have("ffprobe"):
                raise MissingTool("ffmpeg")
            info = media_info(ctx, f)
            _need_audio(ctx, f, info)
            _ffmpeg_tags(ctx, f, info, tmp, sets, removes, remove_all, cover, drop_cover)
    if unsupported:
        ctx.warn(ctx.t("media.warn.tags_unsupported", keys=", ".join(unsupported)))
    final = ctx.replace_original(f, out)
    return ctx.result([final], message=ctx.t("media.msg.tags_saved"))


def _mutagen_get_cover(path: str) -> tuple[bytes, str] | None:
    """Front cover embedded in an audio file (ID3 APIC, MP4 covr, FLAC/Ogg pictures)."""
    import mutagen  # type: ignore

    audio = mutagen.File(path)
    if audio is None:
        return None
    tags = audio.tags
    if tags is not None and hasattr(tags, "getall"):
        pics = tags.getall("APIC")
        if pics:
            pic = next((p for p in pics if getattr(p, "type", 0) == 3), pics[0])
            return bytes(pic.data), str(pic.mime or "")
    if tags is not None and "covr" in getattr(tags, "keys", lambda: [])():
        from mutagen.mp4 import MP4Cover  # type: ignore

        covers = tags["covr"]
        if covers:
            c = covers[0]
            mime = "image/png" if getattr(c, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(c), mime
    pictures = getattr(audio, "pictures", None)
    if pictures:
        pic = next((p for p in pictures if p.type == 3), pictures[0])
        return bytes(pic.data), str(pic.mime or "")
    if tags is not None:
        try:
            values = tags.get("metadata_block_picture") or []
        except (AttributeError, TypeError, ValueError):
            values = []
        if values:
            from mutagen.flac import Picture  # type: ignore

            pic = Picture(base64.b64decode(values[0]))
            return bytes(pic.data), str(pic.mime or "")
    return None


def _image_ext(data: bytes, mime: str) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return {"image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}.get(mime.lower(), "jpg")


@action(id="audio.extract.cover", label="media.act.extract_cover", icon="image", categories={"audio"},
        when=_audio_files_ok, requires=[("py:mutagen", "ffmpeg")], description="media.desc.extract_cover", order=75)
def audio_extract_cover(ctx: Context) -> Result:
    """Save the embedded cover art as an image next to the file."""
    f = ctx.file
    found: tuple[bytes, str] | None = None
    if tools.have("py:mutagen"):
        try:
            found = _mutagen_get_cover(f.path)
        except Exception as exc:  # unreadable or exotic tags: try ffmpeg
            ctx.log(f"mutagen cover read failed: {exc!r}")
    if found and found[0]:
        data, mime = found
        out = ctx.output_path(ext=_image_ext(data, mime), suffix="sfx.cover")
        with atomic_target(out) as tmp:
            tmp.write_bytes(data)
        return ctx.result([out])
    if not (tools.have("ffmpeg") and tools.have("ffprobe")):
        if tools.have("py:mutagen"):
            raise UserError("media.err.no_cover", name=f.name)
        raise MissingTool("ffmpeg")
    info = media_info(ctx, f)
    if not info.pictures:
        raise UserError("media.err.no_cover", name=f.name)
    pic = info.pictures[0]
    ext = {"mjpeg": "jpg", "png": "png", "bmp": "bmp", "webp": "webp", "gif": "gif"}.get(pic.codec, "png")
    copy = pic.codec in ("mjpeg", "png", "bmp", "webp", "gif")
    out = ctx.output_path(ext=ext, suffix="sfx.cover")
    with atomic_target(out) as tmp:
        run_ffmpeg(ctx, ["-i", _fp(f.path), "-map", f"0:{pic.index}", *(["-c:v", "copy"] if copy else []),
                         "-frames:v", "1", "-update", "1", "-f", "image2", _fp(tmp)], raw=True)
    return ctx.result([out])


# ========================================================================================= multi

_P_ORDER = P.select("order", "media.prm.order", [("name", "media.opt.by_name"), ("selection", "media.opt.as_selected")],
                    "name")


def _ordered(ctx: Context) -> list[FileInfo]:
    """Selection order or natural name order (``order`` param)."""
    files = list(ctx.files)
    if ctx.pstr("order", "name") == "name":
        files = util.natural_sorted(files, key=lambda f: ctx.original(f).name)
    return files


def _same_video_params(infos: Sequence[mu.MediaInfo]) -> bool:
    """Whether the concat demuxer can join these clips with a stream copy."""
    first = infos[0]
    v0 = first.video
    if v0 is None:
        return False
    for info in infos:
        v = info.video
        if v is None or v.codec != v0.codec or (v.width, v.height) != (v0.width, v0.height):
            return False
        if v.pix_fmt != v0.pix_fmt or v.rotation != v0.rotation or abs(v.sar - v0.sar) > 0.01:
            return False
        if v0.fps and v.fps and abs(v.fps - v0.fps) > 0.05:
            return False
        if len(info.audios) != len(first.audios):
            return False
        for a, a0 in zip(info.audios, first.audios):
            if (a.codec, a.sample_rate, a.channels) != (a0.codec, a0.sample_rate, a0.channels):
                return False
    return True


@action(id="video.join", label="media.act.join", icon="join", categories={"video"}, multi="combine", mode="quick",
        params=[_P_ORDER], requires=FF, description="media.desc.join_video", order=5)
def video_join(ctx: Context) -> Result:
    """Join clips: concat demuxer stream copy when they match, else re-encoded to the first clip's size."""
    files = _ordered(ctx)
    infos = []
    for f in files:
        info = media_info(ctx, f)
        _need_video(ctx, f, info)
        infos.append(info)
    total = sum(i.duration or 0.0 for i in infos) or None
    first, info0 = files[0], infos[0]
    same_ext, fmt = _copy_container(first)
    copy_ext = same_ext if all(f.ext == first.ext for f in files) and first.ext else None
    use_copy = bool(copy_ext) and _same_video_params(infos)
    ext = copy_ext if use_copy and copy_ext else "mp4"
    out = ctx.output_path(first, ext=ext, suffix="sfx.merged")
    if use_copy:
        work = ctx.tmpdir() / "join"
        work.mkdir(parents=True, exist_ok=True)
        names = []
        for i, f in enumerate(files):
            name = f"in{i:04d}.{ext}"
            _link_or_copy(f.path, work / name)
            names.append(name)
        (work / "list.ffconcat").write_text(mu.concat_list(names), encoding="utf-8")
        maps = ["-map", "0:v:0"] + (["-map", "0:a"] if info0.audios else [])
        try:
            with atomic_target(out) as tmp:
                run_ffmpeg(ctx, ["-f", "concat", "-safe", "0", "-i", "list.ffconcat", *maps, "-c", "copy",
                                 *_mux_args(ext), *fmt, _fp(tmp)], duration=total, cwd=work)
            return ctx.result([out], message=i18n.tn("media.msg.joined_copy", len(files), ctx.lang))
        except Cancelled:
            raise
        except UserError as exc:
            ctx.log(f"concat copy failed: {exc.details[-500:]}")
            ctx.warn(ctx.t("media.warn.copy_fallback"))
            if ext not in ("mp4", "m4v", "mov", "mkv"):
                ext = "mp4"
                out = ctx.output_path(first, ext=ext, suffix="sfx.merged")
    with atomic_target(out) as tmp:
        _join_reencode(ctx, files, infos, tmp, ext, total)
    return ctx.result([out], message=i18n.tn("media.msg.joined", len(files), ctx.lang))


def _join_reencode(ctx: Context, files: list[FileInfo], infos: list[mu.MediaInfo], tmp: Path, ext: str,
                   total: float | None) -> None:
    """concat filter: every clip scaled/padded to the first clip's display size, fps and 48 kHz stereo audio."""
    v0 = infos[0].video
    assert v0 is not None
    w, h = v0.display_size
    width, height = mu.even(w or 1920), mu.even(h or 1080)
    fps = min(60.0, v0.fps or 30.0)
    any_audio = any(i.audios for i in infos)
    enc = _make_venc(ctx, "hevc" if v0.hdr and _have_encoder(ctx, "libx265") else "h264", v0,
                     ten_bit=bool(v0.hdr and _have_encoder(ctx, "libx265")))

    def build(e: VEnc) -> list[str]:
        inputs: list[str] = []
        for f in files:
            inputs += ["-i", _fp(f.path)]
        silent_inputs: dict[int, int] = {}
        if any_audio:
            for i, info in enumerate(infos):
                if not info.audios:
                    silent_inputs[i] = len(files) + len(silent_inputs)
                    inputs += ["-f", "lavfi", "-t", _ts(info.duration or 1.0), "-i", "anullsrc=r=48000:cl=stereo"]
        graph: list[str] = []
        pads = ""
        for i, info in enumerate(infos):
            vs = info.video
            assert vs is not None
            colour = [] if e.ten_bit else (_sdr_filters(ctx, vs) if vs.hdr else [])
            chain = (["bwdif=mode=send_frame"] if vs.interlaced else []) + colour + [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1", f"fps={mu.fmt_num(fps)}",
                "format=yuv420p10le" if e.ten_bit else "format=yuv420p"]
            graph.append(f"[{i}:{vs.index}]{','.join(chain)}[v{i}]")
            pads += f"[v{i}]"
            if any_audio:
                if info.audios:
                    src = f"[{i}:{info.audios[0].index}]"
                else:
                    src = f"[{silent_inputs[i]}:a]"
                graph.append(f"{src}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
                pads += f"[a{i}]"
        n = len(files)
        graph.append(f"{pads}concat=n={n}:v=1:a={1 if any_audio else 0}[vc]" + ("[ac]" if any_audio else ""))
        tail = e.chain([])
        graph.append(f"[vc]{tail}[vout]" if tail else "[vc]null[vout]")
        audio = ["-map", "[ac]", "-c:a", "aac", "-b:a", "160k"] if any_audio else []
        return [*e.input_args(), *inputs, "-filter_complex", ";".join(graph), "-map", "[vout]", *e.args(),
                *audio, "-map_metadata", "0", *_mux_args(ext), _fp(tmp)]

    _encode(ctx, enc, build, duration=total, raw=True)


@action(id="audio.join", label="media.act.join", icon="join", categories={"audio"}, multi="combine", mode="quick",
        when=_audio_files_ok, params=[_P_ORDER], requires=FF, description="media.desc.join_audio", order=5)
def audio_join(ctx: Context) -> Result:
    """Join tracks: stream copy when format and parameters match, else concat filter re-encode."""
    files = _ordered(ctx)
    infos = []
    for f in files:
        info = media_info(ctx, f)
        _need_audio(ctx, f, info)
        infos.append(info)
    total = sum(i.duration or 0.0 for i in infos) or None
    a0 = infos[0].audios[0]
    target = mu.same_format_audio(files[0].ext, a0, ctx.config)
    if target.codec in ("libmp3lame", "libopus", "libvorbis"):
        _encoder(ctx, target.codec)
    same = all(f.ext == files[0].ext for f in files) and target.ext == files[0].ext and all(
        (i.audios[0].codec, i.audios[0].sample_rate, i.audios[0].channels) == (a0.codec, a0.sample_rate, a0.channels)
        for i in infos)
    out = ctx.output_path(files[0], ext=target.ext, suffix="sfx.merged")
    with atomic_target(out) as tmp:
        if same:
            work = ctx.tmpdir() / "join"
            work.mkdir(parents=True, exist_ok=True)
            names = []
            for i, f in enumerate(files):
                name = f"in{i:04d}.{target.ext}"
                _link_or_copy(f.path, work / name)
                names.append(name)
            (work / "list.ffconcat").write_text(mu.concat_list(names), encoding="utf-8")
            args = ["-f", "concat", "-safe", "0", "-i", "list.ffconcat", "-map", "0:a:0", "-c:a", "copy"]
            if target.muxer:
                args += ["-f", target.muxer]
            try:
                run_ffmpeg(ctx, [*args, _fp(tmp)], duration=total, cwd=work)
                return ctx.result([out], message=i18n.tn("media.msg.joined_copy", len(files), ctx.lang))
            except Cancelled:
                raise
            except UserError as exc:
                ctx.log(f"audio concat copy failed: {exc.details[-500:]}")
                ctx.warn(ctx.t("media.warn.copy_fallback"))
        rate = 48000 if target.codec == "libopus" else (a0.sample_rate or 44100)
        if target.codec in ("libmp3lame", "libvorbis", "aac"):
            rate = min(rate, 48000)
        layout_name = "stereo" if any(i.audios[0].channels >= 2 for i in infos) else "mono"
        inputs: list[str] = []
        graph: list[str] = []
        pads = ""
        for i, (f, info) in enumerate(zip(files, infos)):
            inputs += ["-i", _fp(f.path)]
            graph.append(f"[{i}:{info.audios[0].index}]aresample={rate},"
                         f"aformat=sample_fmts=fltp:channel_layouts={layout_name}[a{i}]")
            pads += f"[a{i}]"
        graph.append(f"{pads}concat=n={len(files)}:v=0:a=1[a]")
        args = [*inputs, "-filter_complex", ";".join(graph), "-map", "[a]", *target.args, *_tag_args(infos[0], a0)]
        if target.muxer:
            args += ["-f", target.muxer]
        run_ffmpeg(ctx, [*args, _fp(tmp)], duration=total, raw=True)
    return ctx.result([out], message=i18n.tn("media.msg.joined", len(files), ctx.lang))


def _track_entry(ctx: Context, f: FileInfo) -> tuple[str, float | None]:
    """Title ("Artist - Title" from tags, else the file name) and duration of a track."""
    orig = ctx.original(f)
    title = orig.stem
    seconds: float | None = None
    if tools.have("py:mutagen"):
        try:
            import mutagen  # type: ignore

            audio = mutagen.File(f.path, easy=True)
            if audio is not None:
                tags = audio.tags or {}
                t = (tags.get("title") or [""])[0] if hasattr(tags, "get") else ""
                artist = (tags.get("artist") or [""])[0] if hasattr(tags, "get") else ""
                if t:
                    title = f"{artist} - {t}" if artist else str(t)
                length = getattr(getattr(audio, "info", None), "length", None)
                if length:
                    seconds = float(length)
        except Exception as exc:  # tags are optional decoration
            ctx.log(f"mutagen read failed for {f.name}: {exc!r}")
    if seconds is None and tools.have("ffprobe"):
        seconds = ctx.duration(f.path)
    return title, seconds


@action(id="audio.playlist", label="media.act.playlist", text="M3U", icon="queue_music", categories={"audio"},
        multi="combine", when=_audio_files_ok, params=[_P_ORDER], requires=[], description="media.desc.playlist",
        order=6)
def audio_playlist(ctx: Context) -> Result:
    """Extended M3U (UTF-8 ``.m3u8``) with relative paths, titles and durations."""
    files = _ordered(ctx)
    first = ctx.original(files[0])
    folder = Path(first.path).parent.name or "playlist"
    out = ctx.output_path(files[0], name=f"{folder}.m3u8")
    base = out.parent
    entries = []
    for i, f in enumerate(files):
        ctx.check_cancel()
        ctx.progress(i / len(files))
        orig = ctx.original(f)
        try:
            location = os.path.relpath(orig.path, base)
        except ValueError:  # another drive (Windows) or mount
            location = orig.path
        if location.startswith(".." + os.sep + ".." + os.sep + ".."):
            location = orig.path
        title, seconds = _track_entry(ctx, f)
        entries.append((location.replace(os.sep, "/") if os.sep != "/" else location, seconds, title))
    with atomic_target(out) as tmp:
        tmp.write_text(mu.m3u8_text(entries), encoding="utf-8")
    return ctx.result([out], message=i18n.tn("media.msg.playlist", len(entries), ctx.lang))


# ========================================================================================= mixed

def _prepare_still(ctx: Context, image: FileInfo) -> tuple[Path, int, int]:
    """Still image for a video: EXIF-rotated, flattened, ≤ 1920 px, even dimensions."""
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None:
        try:
            with Image.open(image.path) as im:
                im.seek(0)
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P", "PA"):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (0, 0, 0))
                    bg.paste(rgba, mask=rgba.getchannel("A"))
                    im = bg
                else:
                    im = im.convert("RGB")
                im.thumbnail((1920, 1920))
                w, h = max(2, im.width // 2 * 2), max(2, im.height // 2 * 2)
                if (w, h) != im.size:
                    im = im.resize((w, h))
                path = ctx.tmpdir() / "still.png"
                im.save(path, "PNG")
                return path, w, h
        except Exception as exc:  # decoder missing for this format: let ffmpeg try
            ctx.log(f"Pillow could not prepare {image.name}: {exc!r}")
    info = media_info(ctx, image)
    vs = _need_video(ctx, image, info)
    w, h = vs.display_size
    factor = min(1.0, 1920.0 / max(w or 1, h or 1))
    return Path(image.path), mu.even((w or 2) * factor), mu.even((h or 2) * factor)


@action(id="media.cover_video", label="media.act.cover_video", icon="smart_display", categories={"image", "audio"},
        multi="combine", when=_image_plus_audio, mixed_ok=True, requires=FF, description="media.desc.cover_video",
        order=40)
def media_cover_video(ctx: Context) -> Result:
    """Audio + cover image → MP4 with a still picture (for uploading music to video sites)."""
    image = next(f for f in ctx.files if f.category == "image")
    audio_file = next(f for f in ctx.files if f.category == "audio")
    info = media_info(ctx, audio_file)
    a = _need_audio(ctx, audio_file, info)
    d = _need_duration(ctx, audio_file, info)
    still, width, height = _prepare_still(ctx, image)
    enc = _make_venc(ctx, "h264", None, crf=23)
    audio = ["-c:a", "copy"] if a.codec == "aac" else ["-c:a", "aac", "-b:a", f"{mu.aac_kbps(192, a.channels)}k"]
    out = ctx.output_path(audio_file, ext="mp4")
    with atomic_target(out) as tmp:
        prepared = still.parent == ctx.tmpdir()

        def build(e: VEnc) -> list[str]:
            tune = ["-tune", "stillimage"] if e.hw is None else []
            demux = ["-f", "image2"] if prepared else []
            return [*e.input_args(), *demux, "-loop", "1", "-framerate", "2", "-i", _fp(still),
                    "-i", _fp(audio_file.path),
                    "-map", "0:v:0", "-map", f"1:{a.index}",
                    *e.vf([f"scale={width}:{height}:flags=lanczos", "setsar=1", "format=yuv420p"]), *e.args(),
                    *tune, *audio, "-t", _ts(d), "-shortest", *_tag_args(info, a, source=1),
                    "-movflags", "+faststart", _fp(tmp)]

        _encode(ctx, enc, build, duration=d)
    return ctx.result([out], size_before=audio_file.size)


@action(id="media.to_mp3", label="media.act.all_to_mp3", text="MP3", icon="music_note", categories={"audio", "video"},
        multi="any", when=_mixed_audio_video, mixed_ok=True, mode="quick", params=[_P_MP3_Q], requires=FF,
        description="media.desc.all_to_mp3", order=41)
def media_to_mp3(ctx: Context) -> Result:
    """Every audio and video file to MP3 (one output per file; files already MP3 are skipped)."""
    files = [f for f in ctx.files if f.category in ("audio", "video")]
    todo = [f for f in files if not (f.category == "audio" and f.ext == "mp3")]
    skipped = len(files) - len(todo)
    if skipped:
        ctx.warn(i18n.tn("warn.skipped", skipped, ctx.lang))
    if not todo:
        return ctx.result([], kind="none", message=ctx.t("warn.already_optimized"))
    outputs: list[Path] = []
    errors: list[str] = []
    n = len(todo)
    for i, f in enumerate(todo):
        ctx.check_cancel()
        try:
            outputs.append(_convert_audio_file(ctx, f, "mp3", span=(i / n, (i + 1) / n),
                                               label=f"{i + 1}/{n} · {f.name}"))
        except Cancelled:
            raise
        except UserError as exc:
            errors.append(f"{f.name}: {exc.text(ctx.lang)}")
    if not outputs:
        raise UserError(errors[0] if len(errors) == 1 else ctx.t("job.batch_summary", done=0, total=n,
                                                                  failed=len(errors)),
                        details="\n".join(errors))
    for err in errors:
        ctx.warn(err)
    message = (ctx.t("job.batch_summary", done=len(outputs), total=n, failed=len(errors)) if errors
               else i18n.tn("job.batch_done", len(outputs), ctx.lang))
    return ctx.result(outputs, message=message)


# ======================================================================================== layouts

group("video.convert", "grp.convert", "swap_horiz",
      ["video.convert.mp4", "video.convert.webm", "video.convert.mkv", "video.convert.mov", "video.convert.gif",
       "video.convert.mp3", "video.convert.m4a",
       GroupSpec(id="video.convert.audio.more", label="grp.audio", icon="graphic_eq",
                 items=["video.convert.opus", "video.convert.flac", "video.convert.wav"])],
      default=["video.convert.mp4", "video.convert.mkv"])
group("video.compress", "grp.compress", "compress",
      ["video.compress.balanced", "video.compress.high", "video.compress.small", "video.compress.messaging",
       "video.compress.target"], default="video.compress.balanced")
group("video.resize", "grp.resize", "photo_size_select_large",
      ["video.resize.1080", "video.resize.720", "video.resize.480", "video.resize.1440", "video.resize.2160"],
      default=["video.resize.1080", "video.resize.720"])
group("video.rotate", "grp.rotate", "rotate_right",
      ["video.rotate.cw", "video.rotate.ccw", "video.rotate.180", "video.rotate.meta"], default="video.rotate.cw")
group("video.flip", "grp.flip", "flip", ["video.flip.h", "video.flip.v"], default="video.flip.h")
group("video.speed", "grp.speed", "speed",
      ["video.speed.2", "video.speed.1_5", "video.speed.4", "video.speed.0_5", "video.speed.custom"],
      default="video.speed.2")
group("video.edit", "grp.edit", "edit",
      ["video.edit.mute", "video.rotate", "video.flip", "video.speed", "video.edit.reverse", "video.edit.loop",
       "video.edit.stabilize", "video.edit.fade"], default="video.edit.mute")
group("video.extract", "grp.extract", "unarchive",
      ["video.extract.audio", "video.extract.snapshot", "video.extract.frames", "video.extract.contact_sheet",
       "video.extract.subtitles"], default="video.extract.audio")
group("video.subtitles", "grp.subtitles", "subtitles",
      ["video.subtitles.burn", "video.subtitles.burn_file", "video.subtitles.embed", "video.subtitles.generate",
       "video.extract.subtitles"], default=["video.subtitles.burn", "video.subtitles.generate"])
group("video.metadata", "grp.metadata", "sell",
      ["video.metadata.edit", "video.metadata.strip", "video.metadata.gps"], default="video.metadata.strip")
group("video.more", "grp.more", "more_horiz", ["video.subtitles", "video.split", "video.metadata"],
      default="video.split")

layout("video", ["video.convert", "video.compress", "video.trim", "video.crop", "video.resize", "video.edit",
                 "video.extract", "video.more"])

group("video.multi.more", "grp.more", "more_horiz",
      ["video.edit", "video.extract", "video.subtitles", "video.metadata"], default="video.metadata.gps")
layout("multi:video", ["video.join", "video.convert", "video.compress", "video.extract.audio", "video.edit.mute",
                       "video.metadata.strip", "video.resize", "video.multi.more"])

group("audio.convert", "grp.convert", "swap_horiz",
      ["audio.convert.mp3", "audio.convert.m4a", "audio.convert.opus", "audio.convert.ogg", "audio.convert.flac",
       "audio.convert.wav", "audio.convert.aiff", "audio.convert.m4r"],
      default=["audio.convert.mp3", "audio.convert.m4a"])
group("audio.compress", "grp.compress", "compress",
      ["audio.compress.balanced", "audio.compress.high", "audio.compress.small", "audio.compress.voice"],
      default="audio.compress.balanced")
group("audio.normalize", "grp.normalize", "equalizer",
      ["audio.normalize.streaming", "audio.normalize.podcast", "audio.normalize.broadcast"],
      default="audio.normalize.streaming")
group("audio.transcribe", "grp.transcribe", "record_voice_over",
      ["audio.transcribe.txt", "audio.transcribe.srt", "audio.transcribe.vtt"], default="audio.transcribe.txt")
group("audio.speed", "grp.speed", "speed",
      ["audio.speed.1_25", "audio.speed.1_5", "audio.speed.2", "audio.speed.0_75", "audio.speed.0_5",
       "audio.speed.custom"], default="audio.speed.1_5")
group("audio.volume", "media.grp.volume", "volume_up",
      ["audio.volume.up3", "audio.volume.up6", "audio.volume.down3", "audio.volume.down6", "audio.volume.custom"],
      default="audio.volume.up3")
group("audio.channels", "media.grp.channels", "surround_sound", ["audio.channels.mono", "audio.channels.stereo"],
      default=["audio.channels.mono", "audio.channels.stereo"])
group("audio.edit", "grp.edit", "edit",
      ["audio.speed", "audio.edit.fade", "audio.edit.silence", "audio.edit.denoise", "audio.volume",
       "audio.channels", "audio.edit.reverse"], default="audio.edit.silence")
group("audio.visualize", "media.grp.visualize", "graphic_eq",
      ["audio.visualize.square", "audio.visualize.landscape", "audio.visualize.portrait"],
      default="audio.visualize.square")
group("audio.more", "grp.more", "more_horiz", ["audio.visualize", "audio.extract.cover"],
      default="audio.visualize.square")

layout("audio", ["audio.convert", "audio.compress", "audio.trim", "audio.normalize", "audio.tags",
                 "audio.transcribe", "audio.edit", "audio.more"])

group("audio.multi.more", "grp.more", "more_horiz", ["audio.visualize", "audio.extract.cover"],
      default="audio.visualize.square")
layout("multi:audio", ["audio.join", "audio.convert", "audio.normalize", "audio.compress", "audio.playlist",
                       "audio.transcribe", "audio.edit", "audio.multi.more"])
