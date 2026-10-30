"""Pure helpers for :mod:`dropwheel.actions.media` (no job context, fully unit-testable).

* :func:`parse_probe` turns ``ffprobe -show_format -show_streams`` JSON into a :class:`MediaInfo`
  (display size after rotation, HDR/VFR/interlace flags, attached pictures, text/image subtitles);
* container/codec compatibility tables (:func:`can_copy_video`, :func:`can_copy_audio`,
  :func:`subtitle_codec_for`) and the codec → extension table used by "extract audio"
  (:func:`audio_ext_for_codec`, :func:`wav_pcm_codec`);
* encoder argument builders (:func:`video_codec_args`, :func:`audio_target`, :func:`same_format_audio`,
  :func:`aac_kbps`);
* filter builders (:func:`atempo_chain`, :func:`fit_short_side`, :func:`crop_filter`, :data:`TONEMAP`,
  :func:`gif_filter`, :func:`loudnorm_filter`) and parsers (:func:`parse_loudnorm`,
  :func:`parse_silencedetect`, :func:`whisper_progress`, :func:`png_size`);
* planning helpers (:func:`plan_target_bitrate`, :func:`split_points`, :func:`timestamp_name`,
  :func:`m3u8_text`, :func:`concat_list`).
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import util

__all__ = [
    "PLAYLIST_EXTS", "UNDECODABLE_EXTS", "LOSSLESS_AUDIO_CODECS", "TEXT_SUB_CODECS", "IMAGE_SUB_CODECS",
    "VStream", "AStream", "SStream", "Picture", "MediaInfo", "parse_probe", "parse_rate", "rotation_of",
    "even", "fit_short_side", "crop_rect", "crop_filter", "atempo_chain", "TONEMAP", "gif_filter", "png_size",
    "audio_ext_for_codec", "WAV_PCM_CODECS", "wav_pcm_codec", "can_copy_video", "can_copy_audio",
    "subtitle_codec_for", "container_of", "video_codec_args", "AudioTarget", "audio_target", "same_format_audio",
    "aac_kbps", "AAC_MAX_KBPS", "parse_bitrate",
    "BitratePlan", "plan_target_bitrate", "min_target_bytes", "loudnorm_filter", "parse_loudnorm",
    "parse_silencedetect", "silence_trim_bounds", "whisper_progress", "split_points", "timestamp_name",
    "drawtext_time", "concat_list", "m3u8_text", "lang3", "sidecar_subtitles", "lang_from_name",
    "ff_escape", "decode_subtitle", "TAG_ALIASES", "normalize_tag_key", "segment_pattern", "fmt_num",
]

#: playlist / cue sheets are classified "audio" by extension but are not media
PLAYLIST_EXTS = frozenset({"m3u", "m3u8", "pls", "cue", "xspf"})
#: audio files ffmpeg cannot decode (MIDI needs a synthesizer)
UNDECODABLE_EXTS = frozenset({"mid", "midi", "kar"})
LOSSLESS_AUDIO_CODECS = frozenset({"flac", "alac", "ape", "wavpack", "tta", "mlp", "truehd", "dsd_lsbf", "dsd_msbf",
                                   "dsd_lsbf_planar", "dsd_msbf_planar", "shorten", "tak"})
TEXT_SUB_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer",
                             "subviewer1", "jacosub", "sami", "realtext", "pjs", "mpl2", "vplayer", "stl", "eia_608"})
IMAGE_SUB_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"})
_HDR_TRC = frozenset({"smpte2084", "arib-std-b67"})


# =========================================================================================== probe

def _float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _int(value: Any) -> int | None:
    f = _float(value)
    return int(f) if f is not None else None


def parse_rate(text: Any) -> float | None:
    """``"30000/1001"`` → 29.97; ``"0/0"``/invalid → ``None``."""
    if text in (None, "", "N/A"):
        return None
    s = str(text)
    if "/" in s:
        num, _, den = s.partition("/")
        n, d = _float(num), _float(den)
        if not n or not d:
            return None
        return n / d
    return _float(s) or None


def rotation_of(stream: dict) -> int:
    """Counter-clockwise display rotation in degrees, normalised to 0/90/180/270."""
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            value = _float(side.get("rotation"))
            if value is not None:
                return int(round(value / 90.0)) * 90 % 360
    value = _float((stream.get("tags") or {}).get("rotate"))
    if value is not None:
        # legacy "rotate" tag is clockwise
        return int(round(-value / 90.0)) * 90 % 360
    return 0


@dataclass
class VStream:
    """A video stream (attached pictures excluded)."""

    index: int
    codec: str = ""
    width: int = 0
    height: int = 0
    rotation: int = 0              #: counter-clockwise degrees (0/90/180/270)
    sar: float = 1.0
    fps: float | None = None       #: average frame rate
    r_fps: float | None = None     #: real base frame rate
    pix_fmt: str = ""
    bit_depth: int = 8
    color_transfer: str = ""
    color_primaries: str = ""
    color_space: str = ""
    dolby_vision: bool = False
    field_order: str = ""
    bit_rate: int | None = None
    duration: float | None = None
    language: str = ""

    @property
    def hdr(self) -> bool:
        """PQ/HLG transfer or Dolby Vision."""
        return self.color_transfer in _HDR_TRC or self.dolby_vision

    @property
    def vfr(self) -> bool:
        """Variable frame rate (average differs from the base rate)."""
        if not self.fps or not self.r_fps:
            return False
        return abs(self.fps - self.r_fps) / max(self.r_fps, 1e-6) > 0.01

    @property
    def interlaced(self) -> bool:
        return self.field_order not in ("", "progressive", "unknown")

    @property
    def display_size(self) -> tuple[int, int]:
        """Width/height as displayed (rotation applied, square pixels)."""
        w, h = self.width, self.height
        if self.sar and abs(self.sar - 1.0) > 0.01 and w:
            w = int(round(w * self.sar))
        if self.rotation % 180 == 90:
            w, h = h, w
        return w, h


@dataclass
class AStream:
    """An audio stream."""

    index: int
    codec: str = ""
    channels: int = 2
    sample_rate: int = 0
    bit_rate: int | None = None
    sample_fmt: str = ""
    bits: int = 16                 #: sample depth worth keeping (16 for lossy codecs, see :func:`_audio_bits`)
    layout: str = ""
    language: str = ""
    title: str = ""

    @property
    def lossless(self) -> bool:
        return _is_lossless(self.codec)


@dataclass
class SStream:
    """A subtitle stream."""

    index: int
    codec: str = ""
    language: str = ""
    title: str = ""
    forced: bool = False
    default: bool = False

    @property
    def text(self) -> bool:
        """Text-based (convertible to SRT)."""
        return self.codec in TEXT_SUB_CODECS


@dataclass
class Picture:
    """An attached picture (cover art)."""

    index: int
    codec: str = ""


@dataclass
class MediaInfo:
    """Parsed ffprobe output."""

    duration: float | None = None
    size: int = 0
    bit_rate: int | None = None
    format_name: str = ""
    video: VStream | None = None
    videos: list[VStream] = field(default_factory=list)
    audios: list[AStream] = field(default_factory=list)
    subs: list[SStream] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    data_streams: int = 0
    attachments: int = 0
    chapters: int = 0
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def audio(self) -> AStream | None:
        """First audio stream."""
        return self.audios[0] if self.audios else None

    @property
    def has_audio(self) -> bool:
        return bool(self.audios)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def hdr(self) -> bool:
        return bool(self.video and self.video.hdr)

    @property
    def stream_tags(self) -> bool:
        """The tags live on the streams, not on the file: Ogg (Vorbis comments belong to each logical
        stream), so ``-map_metadata 0`` alone loses them in MP3/MP4/FLAC/WAV outputs."""
        return "ogg" in self.format_name.split(",")


def _is_lossless(codec: str) -> bool:
    return codec in LOSSLESS_AUDIO_CODECS or codec.startswith("pcm_")


def _bit_depth(stream: dict) -> int:
    raw = _int(stream.get("bits_per_raw_sample"))
    if raw and raw > 0:
        return raw
    pix = str(stream.get("pix_fmt") or "")
    m = re.search(r"p(\d{2})(?:le|be)?$", pix)
    if m:
        return int(m.group(1))
    if pix.startswith("p010"):
        return 10
    if pix.startswith("p016"):
        return 16
    return 8


def _audio_bits(stream: dict) -> int:
    """Sample depth worth keeping in a PCM/FLAC output: the depth the stream declares, else the decoder
    sample format of a lossless codec.  Lossy decoders (MP3/AAC/Opus/Vorbis…) output float samples
    whatever the source was, so they count as 16-bit (no 24-bit WAV/FLAC from an MP3).  Values below 8 are the
    size of a coded sample (ADPCM 4, DSD 1), not a depth."""
    raw = _int(stream.get("bits_per_raw_sample")) or _int(stream.get("bits_per_sample"))
    if raw and raw >= 8:
        return raw
    if not _is_lossless(str(stream.get("codec_name") or "")):
        return 16
    fmt = str(stream.get("sample_fmt") or "")
    if fmt.startswith(("s32", "flt")):
        return 24 if fmt.startswith("s32") else 32
    if fmt.startswith("dbl") or fmt.startswith("s64"):
        return 32
    if fmt.startswith("u8"):
        return 8
    return 16


def _sar(stream: dict) -> float:
    text = str(stream.get("sample_aspect_ratio") or "")
    if ":" in text:
        a, _, b = text.partition(":")
        x, y = _float(a), _float(b)
        if x and y:
            return x / y
    return 1.0


def parse_probe(data: dict | None, size: int = 0) -> MediaInfo:
    """Build a :class:`MediaInfo` from ffprobe JSON (missing fields are tolerated)."""
    data = data if isinstance(data, dict) else {}
    fmt = data.get("format") or {}
    info = MediaInfo(size=size or _int(fmt.get("size")) or 0, bit_rate=_int(fmt.get("bit_rate")),
                     format_name=str(fmt.get("format_name") or ""),
                     tags={str(k): str(v) for k, v in (fmt.get("tags") or {}).items()},
                     chapters=len(data.get("chapters") or []))
    duration = _float(fmt.get("duration"))
    longest = 0.0
    for raw in data.get("streams") or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("codec_type")
        index = _int(raw.get("index"))
        if index is None:
            continue
        tags = raw.get("tags") or {}
        disp = raw.get("disposition") or {}
        for cand in (raw.get("duration"), tags.get("DURATION"), tags.get("duration")):
            value = util.parse_time(cand) if isinstance(cand, str) and ":" in cand else _float(cand)
            if value and value > longest:
                longest = value
        codec = str(raw.get("codec_name") or "")
        if kind == "video":
            if disp.get("attached_pic"):
                info.pictures.append(Picture(index=index, codec=codec))
                continue
            side = [s for s in raw.get("side_data_list") or [] if isinstance(s, dict)]
            dv = any("dv_profile" in s or "DOVI" in str(s.get("side_data_type", "")) for s in side)
            vs = VStream(
                index=index, codec=codec, width=_int(raw.get("width")) or 0, height=_int(raw.get("height")) or 0,
                rotation=rotation_of(raw), sar=_sar(raw), fps=parse_rate(raw.get("avg_frame_rate")),
                r_fps=parse_rate(raw.get("r_frame_rate")), pix_fmt=str(raw.get("pix_fmt") or ""),
                bit_depth=_bit_depth(raw), color_transfer=str(raw.get("color_transfer") or ""),
                color_primaries=str(raw.get("color_primaries") or ""), color_space=str(raw.get("color_space") or ""),
                dolby_vision=dv, field_order=str(raw.get("field_order") or ""),
                bit_rate=_int(raw.get("bit_rate")), duration=_float(raw.get("duration")),
                language=str(tags.get("language") or ""))
            info.videos.append(vs)
        elif kind == "audio":
            info.audios.append(AStream(
                index=index, codec=codec, channels=_int(raw.get("channels")) or 2,
                sample_rate=_int(raw.get("sample_rate")) or 0, bit_rate=_int(raw.get("bit_rate")),
                sample_fmt=str(raw.get("sample_fmt") or ""), bits=_audio_bits(raw),
                layout=str(raw.get("channel_layout") or ""), language=str(tags.get("language") or ""),
                title=str(tags.get("title") or "")))
        elif kind == "subtitle":
            info.subs.append(SStream(index=index, codec=codec, language=str(tags.get("language") or ""),
                                     title=str(tags.get("title") or ""), forced=bool(disp.get("forced")),
                                     default=bool(disp.get("default"))))
        elif kind == "data":
            info.data_streams += 1
        elif kind == "attachment":
            info.attachments += 1
    info.video = info.videos[0] if info.videos else None
    info.duration = duration if duration and duration > 0 else (longest or None)
    return info


# ========================================================================================= geometry

def even(value: float, minimum: int = 2) -> int:
    """Round to the nearest even integer ≥ ``minimum``."""
    n = int(round(value / 2.0)) * 2
    return max(minimum, n)


def fit_short_side(width: int, height: int, short: int) -> tuple[int, int] | None:
    """Size with the short side reduced to ``short`` (even dims), or ``None`` when no shrink is needed."""
    if not width or not height:
        return None
    if min(width, height) <= short:
        return None
    if width >= height:
        return even(width * short / height), even(short)
    return even(short), even(height * short / width)


def crop_rect(rect: Any, width: int, height: int) -> tuple[int, int, int, int]:
    """Validate ``{x,y,w,h}`` (source pixels) → clamped even ``(x, y, w, h)``; raises ``ValueError``."""
    if isinstance(rect, str):
        try:
            rect = json.loads(rect)
        except ValueError:
            parts = re.split(r"[,:x ]+", rect.strip())
            if len(parts) != 4:
                raise ValueError("rect") from None
            rect = dict(zip(("x", "y", "w", "h"), parts))
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        rect = dict(zip(("x", "y", "w", "h"), rect))
    if not isinstance(rect, dict):
        raise ValueError("rect")
    try:
        x = float(rect.get("x", 0))
        y = float(rect.get("y", 0))
        w = float(rect.get("w", rect.get("width", 0)))
        h = float(rect.get("h", rect.get("height", 0)))
    except (TypeError, ValueError):
        raise ValueError("rect") from None
    if any(v != v for v in (x, y, w, h)):
        raise ValueError("rect")
    fractional = any(abs(v - round(v)) > 1e-9 for v in (x, y, w, h))
    if fractional and 0 < w <= 1.0 and 0 < h <= 1.0 and 0 <= x < 1.0 and 0 <= y < 1.0 and width > 4 and height > 4:
        # normalised rectangle (0..1, at least one fractional value)
        x, y, w, h = x * width, y * height, w * width, h * height
    x = max(0.0, min(x, width - 2.0))
    y = max(0.0, min(y, height - 2.0))
    w = min(w, width - x)
    h = min(h, height - y)
    if w < 2 or h < 2:
        raise ValueError("rect too small")
    xi, yi = int(x) // 2 * 2, int(y) // 2 * 2
    wi = max(2, int(w) // 2 * 2)
    hi = max(2, int(h) // 2 * 2)
    wi = min(wi, (width - xi) // 2 * 2)
    hi = min(hi, (height - yi) // 2 * 2)
    if wi < 2 or hi < 2:
        raise ValueError("rect too small")
    return xi, yi, wi, hi


def crop_filter(x: int, y: int, w: int, h: int) -> str:
    """``crop=w:h:x:y``."""
    return f"crop={w}:{h}:{x}:{y}"


def fmt_num(value: float) -> str:
    """Compact decimal for filter arguments (``2`` / ``1.5`` / ``0.333333``)."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def atempo_chain(factor: float) -> str:
    """``atempo`` filters reaching ``factor`` (each stage within [0.5, 2] to keep quality)."""
    if factor <= 0:
        raise ValueError("factor must be positive")
    stages: list[float] = []
    rest = float(factor)
    while rest > 2.0 + 1e-9:
        stages.append(2.0)
        rest /= 2.0
    while rest < 0.5 - 1e-9:
        stages.append(0.5)
        rest /= 0.5
    if abs(rest - 1.0) > 1e-9 or not stages:
        stages.append(rest)
    return ",".join(f"atempo={fmt_num(s)}" for s in stages)


#: HDR (PQ/HLG) → SDR BT.709 tone mapping with zimg (``zscale``), 8-bit 4:2:0 output
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def gif_filter(fps: int, width: int | None, prefix: str = "") -> str:
    """High quality palette GIF filtergraph (palettegen/paletteuse in one pass)."""
    parts = [p for p in (prefix, f"fps={fps}") if p]
    if width:
        parts.append(f"scale={width}:-1:flags=lanczos")
    head = ",".join(parts)
    return (f"{head},split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];"
            f"[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle")


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(path: str | os.PathLike) -> tuple[int, int] | None:
    """Width and height from the IHDR chunk of a PNG file (``None`` when unreadable or not a PNG)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != _PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return (width, height) if width and height else None


def ff_escape(value: str) -> str:
    """Escape ``value`` for a filter option inside a filtergraph (both escaping levels of the
    ffmpeg-filters "Notes on filtergraph escaping"): ``a 'b': c, d`` → ``a \\\\\\'b\\\\\\'\\\\: c\\, d``."""
    level1 = str(value).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    out = level1.replace("\\", "\\\\").replace("'", "\\'")
    for ch in "[],;":
        out = out.replace(ch, "\\" + ch)
    return out


# ===================================================================================== containers

_CONTAINER = {
    "mp4": "mp4", "m4v": "mp4", "f4v": "mp4", "3gp": "mp4", "3g2": "mp4",
    "mov": "mov", "qt": "mov",
    "mkv": "mkv", "mk3d": "mkv", "mka": "mkv",
    "webm": "webm", "weba": "webm",
    "avi": "avi", "divx": "avi",
    "ogv": "ogg", "ogg": "ogg", "oga": "ogg", "opus": "ogg",
    "ts": "ts", "m2ts": "ts", "mts": "ts",
    "flv": "flv", "wmv": "asf", "asf": "asf", "mpg": "mpeg", "mpeg": "mpeg", "vob": "mpeg",
}

_VIDEO_COPY = {
    "mp4": {"h264", "hevc", "av1", "mpeg4", "vp9", "mpeg2video", "mjpeg"},
    "mov": {"h264", "hevc", "prores", "mjpeg", "mpeg4", "dnxhd", "av1", "mpeg2video"},
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"mpeg4", "h264", "mjpeg", "msmpeg4v3", "msmpeg4v2", "mpeg2video", "huffyuv", "rawvideo"},
    "ts": {"h264", "hevc", "mpeg2video", "mpeg1video", "av1"},
    "ogg": {"theora", "vp8"},
    "flv": {"h264", "flv1"},
}
_AUDIO_COPY = {
    "mp4": {"aac", "mp3", "alac", "ac3", "eac3"},
    "mov": {"aac", "mp3", "alac", "ac3", "eac3", "pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be", "pcm_f32le"},
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "ac3", "pcm_s16le", "mp2", "aac"},
    "ts": {"aac", "mp3", "ac3", "eac3", "mp2", "opus"},
    "ogg": {"vorbis", "opus", "flac", "speex"},
    "flv": {"aac", "mp3"},
}


def container_of(ext: str) -> str:
    """Container family of an extension (``"m4v"`` → ``"mp4"``)."""
    return _CONTAINER.get(ext.lower().lstrip("."), ext.lower().lstrip("."))


def can_copy_video(container: str, codec: str) -> bool:
    """``True`` when ``codec`` can be stream-copied into ``container``."""
    cont = container_of(container)
    if cont == "mkv":
        return bool(codec)
    allowed = _VIDEO_COPY.get(cont)
    return bool(allowed and codec in allowed)


def can_copy_audio(container: str, codec: str) -> bool:
    """``True`` when audio ``codec`` can be stream-copied into ``container``."""
    cont = container_of(container)
    if cont == "mkv":
        return bool(codec)
    allowed = _AUDIO_COPY.get(cont)
    return bool(allowed and codec in allowed)


def subtitle_codec_for(container: str, sub: SStream) -> str | None:
    """Subtitle encoder for ``container`` (``"copy"``, ``"mov_text"``…) or ``None`` to drop it."""
    cont = container_of(container)
    if cont == "mkv":
        if sub.codec == "mov_text":
            return "srt"
        return "copy"
    if not sub.text:
        return None
    if cont in ("mp4", "mov"):
        return "copy" if sub.codec == "mov_text" else "mov_text"
    if cont == "webm":
        return "copy" if sub.codec == "webvtt" else "webvtt"
    return None


_AUDIO_EXT = {
    "aac": "m4a", "alac": "m4a", "mp3": "mp3", "mp2": "mp2", "opus": "opus", "vorbis": "ogg", "flac": "flac",
    "ac3": "ac3", "eac3": "eac3", "dts": "dts", "wmav1": "wma", "wmav2": "wma", "wmapro": "wma",
    "amr_nb": "amr", "amr_wb": "amr", "speex": "spx", "truehd": "mka", "mlp": "mka",
}
_AUDIO_FMT = {"m4a": "ipod", "mka": "matroska", "wma": "asf", "dts": "dts", "ac3": "ac3", "eac3": "eac3",
              "amr": "amr", "spx": "ogg", "opus": "opus", "ogg": "ogg", "mp2": "mp2"}


#: PCM codecs the WAV muxer stores as they are (it refuses big-endian, Blu-ray and DVD PCM)
WAV_PCM_CODECS = frozenset({"pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_alaw",
                            "pcm_mulaw"})


def audio_ext_for_codec(codec: str) -> tuple[str, str | None]:
    """Extension and muxer for a stream-copied audio track (``"aac"`` → ``("m4a", "ipod")``).

    Every PCM flavour maps to WAV; the ones outside :data:`WAV_PCM_CODECS` (``pcm_s16be`` of camera
    MOVs, ``pcm_bluray``, ``pcm_dvd``…) have to be repacked with :func:`wav_pcm_codec` instead of copied."""
    codec = (codec or "").lower()
    if codec.startswith("pcm_"):
        return "wav", "wav"
    ext = _AUDIO_EXT.get(codec, "mka")
    return ext, _AUDIO_FMT.get(ext)


def wav_pcm_codec(src: AStream) -> str:
    """Little-endian PCM encoder for a WAV holding ``src`` without loss: the codec itself when WAV accepts it,
    else the same depth (float stays float, 20-bit DVD PCM becomes 24-bit)."""
    codec = (src.codec or "").lower()
    if codec in WAV_PCM_CODECS:
        return codec
    if codec.startswith("pcm_f64"):
        return "pcm_f64le"
    if codec.startswith("pcm_f"):
        return "pcm_f32le"
    if src.bits > 24:
        return "pcm_s32le"
    if src.bits > 16:
        return "pcm_s24le"
    if src.bits <= 8:
        return "pcm_u8"
    return "pcm_s16le"


# ======================================================================================= encoders

def video_codec_args(codec: str, crf: int, preset: str = "medium", *, ten_bit: bool = False,
                     vs: VStream | None = None, encoder: str | None = None) -> list[str]:
    """Software encoder arguments for ``h264``/``hevc``/``vp9``/``av1``.

    ``ten_bit`` keeps 10-bit 4:2:0 (HDR) and copies the colour description of ``vs``.
    ``encoder`` overrides the default encoder for AV1 (``libsvtav1``/``libaom-av1``/``librav1e``).
    """
    pix = "yuv420p10le" if ten_bit else "yuv420p"
    colour: list[str] = []
    if ten_bit and vs is not None:
        if vs.color_primaries:
            colour += ["-color_primaries", vs.color_primaries]
        if vs.color_transfer:
            colour += ["-color_trc", vs.color_transfer]
        if vs.color_space:
            colour += ["-colorspace", vs.color_space]
    if codec == "h264":
        return ["-c:v", encoder or "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "hevc":
        return ["-c:v", encoder or "libx265", "-preset", preset, "-crf", str(crf), "-pix_fmt", pix,
                "-tag:v", "hvc1", *colour]
    if codec == "vp9":
        args = ["-c:v", encoder or "libvpx-vp9", "-crf", str(crf), "-b:v", "0", "-row-mt", "1", "-deadline", "good",
                "-cpu-used", "2", "-pix_fmt", pix]
        if ten_bit:
            args += ["-profile:v", "2"]
        return args + colour
    if codec == "av1":
        enc = encoder or "libsvtav1"
        if enc == "libsvtav1":
            return ["-c:v", enc, "-preset", "8", "-crf", str(crf), "-g", "240", "-pix_fmt", pix, *colour]
        if enc == "librav1e":
            return ["-c:v", enc, "-qp", str(min(255, crf * 4)), "-speed", "6", "-pix_fmt", pix, *colour]
        return ["-c:v", enc, "-crf", str(crf), "-b:v", "0", "-cpu-used", "6", "-row-mt", "1", "-pix_fmt", pix,
                *colour]
    raise ValueError(f"unknown video codec {codec!r}")


def parse_bitrate(value: Any, default: int = 128) -> int:
    """``"128k"`` / ``128`` / ``"128000"`` → kbit/s."""
    if value is None:
        return default
    text = str(value).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(k|m)?(?:b(?:it)?(?:/s|ps)?)?$", text)
    if not m:
        return default
    number = float(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return int(number * 1000)
    if unit == "k":
        return int(number)
    return int(number / 1000) if number > 10000 else int(number)


@dataclass
class AudioTarget:
    """How to encode audio into a given output format."""

    ext: str
    args: list[str]
    lossless: bool = False
    muxer: str | None = None
    codec: str = ""


#: ceiling for multichannel AAC-LC tracks (kbit/s)
AAC_MAX_KBPS = 512


def aac_kbps(stereo_kbps: int, channels: int) -> int:
    """AAC bitrate for a ``channels``-channel track, given the rate wanted for a stereo pair: surround keeps the
    same rate per channel (5.1 at 128k stereo → 384k), up to :data:`AAC_MAX_KBPS`."""
    if channels <= 2:
        return stereo_kbps
    return max(stereo_kbps, min(AAC_MAX_KBPS, stereo_kbps * channels // 2))


def _cfg_get(cfg: dict | None, section: str, key: str, default: Any) -> Any:
    sec = (cfg or {}).get(section)
    if isinstance(sec, dict) and sec.get(key) not in (None, ""):
        return sec[key]
    return default


def audio_target(ext: str, src: AStream | None = None, cfg: dict | None = None, *,
                 bitrate: int | None = None) -> AudioTarget:
    """Encoder arguments for an audio output with extension ``ext``.

    ``bitrate`` (kbit/s) forces CBR/ABR for lossy codecs; otherwise the configured quality is used
    (``audio.mp3_quality`` LAME VBR level, ``audio.opus_bitrate``).  For AAC it is the rate of a stereo
    pair: surround tracks get the same rate per channel (:func:`aac_kbps`); ringtones are stereo.
    PCM/FLAC keep 24 bits only when ``src`` really has them (:attr:`AStream.bits`).
    """
    ext = ext.lower().lstrip(".")
    channels = src.channels if src else 2
    bits = src.bits if src else 16
    stereo_only = ["-ac", "2"] if channels > 2 else []
    if ext == "mp3":
        q = _cfg_get(cfg, "audio", "mp3_quality", 2)
        try:
            q = max(0, min(9, int(q)))
        except (TypeError, ValueError):
            q = 2
        quality = ["-b:a", f"{bitrate}k"] if bitrate else ["-q:a", str(q)]
        return AudioTarget("mp3", ["-c:a", "libmp3lame", *quality, *stereo_only, "-id3v2_version", "3"],
                           muxer="mp3", codec="libmp3lame")
    if ext in ("m4a", "m4b", "m4r", "aac"):
        if ext == "m4r":
            args = ["-c:a", "aac", "-b:a", f"{bitrate or 192}k", *stereo_only]
        else:
            args = ["-c:a", "aac", "-b:a", f"{aac_kbps(bitrate or 192, channels)}k"]
        if ext == "aac":
            return AudioTarget("aac", args, muxer="adts", codec="aac")
        return AudioTarget(ext, args + ["-movflags", "+faststart"], muxer="ipod", codec="aac")
    if ext == "opus":
        rate = bitrate or parse_bitrate(_cfg_get(cfg, "audio", "opus_bitrate", "128k"), 128)
        return AudioTarget("opus", ["-c:a", "libopus", "-b:a", f"{rate}k", *stereo_only], muxer="opus",
                           codec="libopus")
    if ext in ("ogg", "oga"):
        quality = ["-b:a", f"{bitrate}k"] if bitrate else ["-q:a", "5"]
        return AudioTarget(ext, ["-c:a", "libvorbis", *quality], muxer="ogg", codec="libvorbis")
    if ext == "flac":
        fmt = ["-sample_fmt", "s32"] if bits > 16 else ["-sample_fmt", "s16"]
        return AudioTarget("flac", ["-c:a", "flac", "-compression_level", "8", *fmt], lossless=True, muxer="flac",
                           codec="flac")
    if ext in ("wav", "wave"):
        codec = "pcm_s24le" if bits > 16 else "pcm_s16le"
        return AudioTarget("wav", ["-c:a", codec, "-rf64", "auto"], lossless=True, muxer="wav", codec=codec)
    if ext in ("aif", "aiff", "aifc"):
        codec = "pcm_s24be" if bits > 16 else "pcm_s16be"
        # without the ID3 chunk the AIFF muxer only stores the title, comment and copyright
        return AudioTarget(ext, ["-c:a", codec, "-write_id3v2", "1"], lossless=True, muxer="aiff", codec=codec)
    if ext == "wma":
        return AudioTarget("wma", ["-c:a", "wmav2", "-b:a", f"{bitrate or 192}k", *stereo_only], muxer="asf",
                           codec="wmav2")
    if ext == "weba":
        rate = bitrate or parse_bitrate(_cfg_get(cfg, "audio", "opus_bitrate", "128k"), 128)
        return AudioTarget("weba", ["-c:a", "libopus", "-b:a", f"{rate}k", *stereo_only], muxer="webm",
                           codec="libopus")
    if ext == "ac3":
        return AudioTarget("ac3", ["-c:a", "ac3", "-b:a", f"{bitrate or 448}k"], muxer="ac3", codec="ac3")
    raise ValueError(f"unsupported audio target {ext!r}")


_SAME_LOSSLESS = {"ape", "wv", "tta", "dsf", "dff", "tak", "shn", "alac"}
_SAME_DIRECT = {"mp3", "m4a", "m4b", "aac", "opus", "flac", "wav", "wave", "aif", "aiff", "aifc", "wma", "ac3",
                "weba"}


def same_format_audio(ext: str, src: AStream | None, cfg: dict | None = None, *,
                      bitrate: int | None = None) -> AudioTarget:
    """Encoder for re-encoding an audio file "in its own format" (normalize, trim, effects).

    Formats ffmpeg cannot write well fall back to FLAC (lossless sources) or M4A (lossy ones).
    """
    ext = ext.lower()
    codec = src.codec if src else ""
    if ext in ("m4a", "m4b", "mp4") and codec == "alac":
        target = audio_target("m4a", src, cfg)
        return AudioTarget(target.ext, ["-c:a", "alac", "-movflags", "+faststart"], lossless=True, muxer="ipod",
                           codec="alac")
    if ext in ("ogg", "oga") and codec == "opus":
        target = audio_target("opus", src, cfg, bitrate=bitrate)
        return AudioTarget(ext, target.args, muxer="ogg", codec="libopus")
    if ext in ("ogg", "oga"):
        return audio_target(ext, src, cfg, bitrate=bitrate)
    if ext in _SAME_DIRECT:
        return audio_target(ext, src, cfg, bitrate=bitrate)
    if ext in ("mp2", "mpga"):
        return audio_target("mp3", src, cfg, bitrate=bitrate)
    if ext in _SAME_LOSSLESS or (src is not None and src.lossless):
        return audio_target("flac", src, cfg)
    return audio_target("m4a", src, cfg, bitrate=bitrate)


# ==================================================================================== target size

@dataclass
class BitratePlan:
    """Two-pass plan for a target file size."""

    video_kbps: int
    audio_kbps: int
    max_short: int | None   #: short side to scale down to (``None`` = keep)


_LADDER = ((8000, 2160), (4500, 1440), (2500, 1080), (1200, 720), (700, 540), (400, 480), (0, 360))
MIN_VIDEO_KBPS = 100
_OVERHEAD = 0.96


def _audio_budget(total_kbps: float, has_audio: bool) -> int:
    if not has_audio:
        return 0
    if total_kbps >= 1500:
        return 128
    if total_kbps >= 700:
        return 96
    if total_kbps >= 300:
        return 64
    return 32


def plan_target_bitrate(duration: float, target_bytes: int, src_short: int | None, has_audio: bool) -> BitratePlan:
    """Bitrates (kbit/s) and resolution cap reaching ``target_bytes`` for ``duration`` seconds.

    Raises ``ValueError`` when the video bitrate would fall below :data:`MIN_VIDEO_KBPS`.
    """
    if not duration or duration <= 0:
        raise ValueError("unknown duration")
    total = target_bytes * 8 * _OVERHEAD / duration / 1000.0
    audio = _audio_budget(total, has_audio)
    video = int(total - audio)
    if video < MIN_VIDEO_KBPS:
        raise ValueError("target too small")
    cap = next(h for threshold, h in _LADDER if video >= threshold)
    max_short = cap if src_short and src_short > cap else None
    return BitratePlan(video_kbps=video, audio_kbps=audio, max_short=max_short)


def min_target_bytes(duration: float, has_audio: bool) -> int:
    """Smallest size :func:`plan_target_bitrate` accepts (rounded up to 0.5 MB)."""
    kbps = MIN_VIDEO_KBPS + (32 if has_audio else 0)
    raw = kbps * 1000 * duration / 8 / _OVERHEAD
    step = 500_000
    return int(math.ceil(raw / step) * step)


# ====================================================================================== loudness

def loudnorm_filter(target_i: float, tp: float = -1.0, lra: float = 11.0, measured: dict | None = None) -> str:
    """``loudnorm`` filter: analysis pass (``measured=None``) or linear second pass."""
    base = f"loudnorm=I={fmt_num(target_i)}:TP={fmt_num(tp)}:LRA={fmt_num(lra)}"
    if not measured:
        return base + ":print_format=json"

    def clamp(key: str, lo: float, hi: float) -> str:
        return f"{max(lo, min(hi, float(measured[key]))):.2f}"

    return (base + f":measured_I={clamp('input_i', -99, 0)}:measured_TP={clamp('input_tp', -99, 99)}"
            f":measured_LRA={clamp('input_lra', 0, 99)}:measured_thresh={clamp('input_thresh', -99, 0)}"
            f":offset={clamp('target_offset', -99, 99)}:linear=true:print_format=summary")


_LOUD_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm(text: str) -> dict[str, float]:
    """Measured values from the loudnorm JSON block printed on stderr (last block wins).

    Raises ``ValueError`` when no block is found or the input is silent (``-inf``).
    """
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text or "", re.S)
    if not blocks:
        raise ValueError("no loudnorm data")
    data = json.loads(blocks[-1])
    out: dict[str, float] = {}
    for key in _LOUD_KEYS:
        raw = str(data.get(key, "")).strip()
        if raw in ("", "-inf", "inf", "nan", "-nan"):
            raise ValueError(f"loudnorm {key}={raw!r}")
        out[key] = float(raw)
    for key in ("output_i", "output_tp", "output_lra"):
        raw = str(data.get(key, "")).strip()
        try:
            out[key] = float(raw)
        except ValueError:
            pass
    return out


_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+(?:e[-+]?\d+)?)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+(?:e[-+]?\d+)?)")


def parse_silencedetect(text: str) -> list[tuple[float, float | None]]:
    """``[(start, end|None)]`` from ``silencedetect`` log lines (``None`` = silence until EOF)."""
    out: list[tuple[float, float | None]] = []
    current: float | None = None
    for line in (text or "").splitlines():
        m = _SIL_START.search(line)
        if m:
            current = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and current is not None:
            out.append((current, float(m.group(1))))
            current = None
    if current is not None:
        out.append((current, None))
    return out


def silence_trim_bounds(silences: Sequence[tuple[float, float | None]], duration: float,
                        edge: float = 0.05, pad: float = 0.1) -> tuple[float, float]:
    """Start/end keeping everything except silence touching the beginning/end of the file.

    ``edge`` is the tolerance to consider a silence as starting at 0 / reaching the end; ``pad``
    keeps a short breath around the sound.
    """
    start, end = 0.0, duration
    for s, e in silences:
        if s <= edge and e is not None:
            start = max(start, e - pad)
        if e is None or (duration and e >= duration - edge):
            end = min(end, s + pad)
    start = max(0.0, start)
    end = min(duration, end) if duration else end
    return start, end


_WHISPER_PROGRESS = re.compile(r"progress\s*=\s*(\d{1,3})\s*%")


def whisper_progress(line: str) -> int | None:
    """Percentage from a whisper-cli ``-pp`` progress line."""
    m = _WHISPER_PROGRESS.search(line or "")
    if not m:
        return None
    return max(0, min(100, int(m.group(1))))


# ========================================================================================= naming

def split_points(duration: float, *, minutes: float | None = None, parts: int | None = None) -> list[float]:
    """Cut times (seconds, exclusive of 0 and the end) for "every N minutes" or "N equal parts"."""
    if not duration or duration <= 0:
        raise ValueError("unknown duration")
    if parts is not None:
        if parts < 2:
            raise ValueError("parts")
        step = duration / parts
        return [round(step * i, 3) for i in range(1, parts)]
    if minutes is None or minutes <= 0:
        raise ValueError("minutes")
    step = minutes * 60.0
    points = []
    t = step
    while t < duration - 0.5:
        points.append(round(t, 3))
        t += step
    return points


def timestamp_name(seconds: float, with_ms: bool = False) -> str:
    """``83.5`` → ``"00-01-23"`` (``"00-01-23.500"`` with ``with_ms``), safe in file names."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h:02d}-{m:02d}-{s:02d}"
    if with_ms:
        ms = int(round((seconds - whole) * 1000))
        if ms >= 1000:
            ms = 999
        text += f".{ms:03d}"
    return text


def drawtext_time(seconds: float) -> str:
    """``H:MM:SS`` with the colons escaped for a drawtext ``text=`` value."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return text.replace(":", "\\:")


def segment_pattern(stem: str, ext: str) -> str:
    """Segment-muxer output pattern ``"<stem> %03d.<ext>"`` with ``%`` in the stem escaped."""
    return f"{stem.replace('%', '%%')} %03d.{ext}"


def concat_list(names: Iterable[str]) -> str:
    """ffconcat list for the concat demuxer (names are relative to the list file)."""
    lines = ["ffconcat version 1.0"]
    for name in names:
        if "\n" in name or "\r" in name:
            raise ValueError("newline in concat entry")
        lines.append("file '" + name.replace("'", "'\\''") + "'")
    return "\n".join(lines) + "\n"


def m3u8_text(entries: Iterable[tuple[str, float | None, str]]) -> str:
    """Extended M3U (UTF-8): ``entries`` are ``(location, seconds|None, title)``."""
    lines = ["#EXTM3U"]
    for location, seconds, title in entries:
        secs = int(round(seconds)) if seconds else -1
        clean = " ".join(str(title).split())
        lines.append(f"#EXTINF:{secs},{clean}")
        lines.append(location)
    return "\n".join(lines) + "\n"


_LANG3 = {
    "it": "ita", "en": "eng", "fr": "fre", "de": "ger", "es": "spa", "pt": "por", "nl": "dut", "ru": "rus",
    "ja": "jpn", "zh": "chi", "ko": "kor", "ar": "ara", "pl": "pol", "sv": "swe", "da": "dan", "no": "nor",
    "fi": "fin", "el": "gre", "tr": "tur", "cs": "cze", "hu": "hun", "ro": "rum", "uk": "ukr", "he": "heb",
    "hi": "hin", "ca": "cat", "hr": "hrv", "sl": "slv", "sk": "slo", "bg": "bul", "sr": "srp",
}
_LANG3_VALUES = set(_LANG3.values()) | {"fra", "deu", "nld", "zho", "ces", "ell", "ron", "slk", "und"}


def lang3(code: str) -> str:
    """ISO 639-2 code for a 2/3-letter language tag (``"it"`` → ``"ita"``); ``""`` when unknown."""
    code = (code or "").lower().strip()
    if code in _LANG3:
        return _LANG3[code]
    if code in _LANG3_VALUES:
        return code
    return ""


def lang_from_name(sub_name: str, video_stem: str) -> str:
    """Language tag in ``"<stem>.<lang>.srt"`` (``""`` when absent)."""
    stem, _ext = util.split_name(sub_name)
    if stem.lower().startswith(video_stem.lower() + "."):
        tag = stem[len(video_stem) + 1:].split(".")[0]
        return tag.lower() if lang3(tag) else ""
    return ""


_SUB_EXTS = ("srt", "ass", "ssa", "vtt")


def sidecar_subtitles(video_path: str | os.PathLike, prefer_lang: str = "") -> list[Path]:
    """Subtitle files next to a video: ``<stem>.srt`` first, then ``<stem>.<lang>.srt`` (preferred language first)."""
    p = Path(os.fspath(video_path))
    stem, _ext = util.split_name(p.name)
    try:
        siblings = [e for e in os.scandir(p.parent) if e.is_file()]
    except OSError:
        return []
    exact: list[Path] = []
    tagged: list[Path] = []
    for entry in siblings:
        sstem, sext = util.split_name(entry.name)
        if sext not in _SUB_EXTS:
            continue
        if sstem == stem:
            exact.append(Path(entry.path))
        elif sstem.startswith(stem + ".") and lang_from_name(entry.name, stem):
            tagged.append(Path(entry.path))
    order = {e: i for i, e in enumerate(_SUB_EXTS)}
    exact.sort(key=lambda q: order.get(util.split_name(q.name)[1], 9))
    pref = (prefer_lang or "").lower()
    tagged.sort(key=lambda q: (0 if pref and lang_from_name(q.name, stem) == pref else 1,
                               order.get(util.split_name(q.name)[1], 9), q.name))
    return exact + tagged


def decode_subtitle(data: bytes) -> str:
    """Decode a subtitle file (UTF-8/UTF-16 BOM, charset_normalizer when installed, else CP1252)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if len(data) >= 64:
        try:
            from charset_normalizer import from_bytes  # type: ignore

            best = from_bytes(data).best()
            # BOM-less UTF-16/32 guesses on 8-bit text are almost always wrong
            if best is not None and not str(best.encoding).startswith(("utf_16", "utf_32")):
                return str(best)
        except Exception:  # optional dependency
            pass
    return data.decode("cp1252", "replace")


# =========================================================================================== tags

#: tag aliases → canonical (mutagen "easy" / ffmpeg) names
TAG_ALIASES = {
    "title": "title", "tit2": "title", "name": "title", "©nam": "title",
    "artist": "artist", "tpe1": "artist", "author": "artist", "performer": "artist", "©art": "artist",
    "album": "album", "talb": "album", "©alb": "album",
    "albumartist": "albumartist", "album_artist": "albumartist", "band": "albumartist", "tpe2": "albumartist",
    "aart": "albumartist",
    "date": "date", "year": "date", "tdrc": "date", "tyer": "date", "creationdate": "date", "©day": "date",
    "contentcreatedate": "date",
    "track": "tracknumber", "tracknumber": "tracknumber", "trck": "tracknumber", "trkn": "tracknumber",
    "trackno": "tracknumber",
    "disc": "discnumber", "discnumber": "discnumber", "tpos": "discnumber", "partofset": "discnumber",
    "disk": "discnumber",
    "genre": "genre", "tcon": "genre", "©gen": "genre",
    "composer": "composer", "tcom": "composer", "©wrt": "composer",
    "comment": "comment", "comm": "comment", "description": "comment", "©cmt": "comment",
    "lyrics": "lyrics", "uslt": "lyrics", "©lyr": "lyrics", "unsyncedlyrics": "lyrics",
    "copyright": "copyright", "cprt": "copyright", "tcop": "copyright",
    "cover": "cover", "picture": "cover", "coverart": "cover", "apic": "cover", "covr": "cover",
    "metadata_block_picture": "cover",
    "language": "language", "encoder": "encoder", "encodedby": "encodedby", "encoded_by": "encodedby",
}
_FFMPEG_NAMES = {"albumartist": "album_artist", "tracknumber": "track", "discnumber": "disc", "encodedby": "encoded_by"}


def normalize_tag_key(key: str, ffmpeg: bool = False) -> str:
    """``"ItemList:Title"`` / ``"Tag:title"`` / ``"TPE1"`` → ``"title"``/``"artist"`` (ffmpeg names with ``ffmpeg=True``)."""
    raw = str(key or "").strip()
    if ":" in raw:
        raw = raw.rsplit(":", 1)[1]
    low = raw.lower().replace(" ", "").replace("-", "")
    canon = TAG_ALIASES.get(low, low)
    if ffmpeg:
        return _FFMPEG_NAMES.get(canon, canon)
    return canon
