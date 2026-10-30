"""Tests for dropwheel.actions.media and its pure helper module.

ffmpeg/ffprobe/whisper are not available on the development machine, so the action tests run
through the real JobRunner with ``Context.run`` replaced by :class:`FakeMedia`: it answers
ffprobe with synthetic JSON, creates the files ffmpeg would write (including image/segment
patterns), feeds progress lines and records every command line for assertions.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from dropwheel import config as cfgmod
from dropwheel import i18n, jobs, registry, tools
from dropwheel.detect import FileInfo
from dropwheel.errors import ToolError
from dropwheel.actions import _media_util as mu

LOCALES = Path(__file__).resolve().parent.parent / "src" / "dropwheel" / "locales"


# ======================================================================================= fixtures

@pytest.fixture
def media_mod():
    """The media module with its actions registered (re-imported if another test cleared the registry)."""
    import dropwheel.actions.media as media

    if registry.get("video.convert.mp4") is None:
        media = importlib.reload(media)
    return media


ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D libvpx-vp9           libvpx VP9 (codec vp9)
 V....D libsvtav1            SVT-AV1 (codec av1)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3 (codec mp3)
 A....D libopus              libopus Opus (codec opus)
 A....D libvorbis            libvorbis (codec vorbis)
 A....D flac                 FLAC
"""
FILTERS_ALL = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 ... zscale           V->V       Apply resizing, colorspace and bit depth conversion.
 ... tonemap           V->V       Conversion to/from different dynamic ranges.
 T.. drawtext          V->V       Draw text on top of video frames.
 ... subtitles         V->V       Render text subtitles onto input video using the libass library.
 ... vidstabdetect     V->V       Extract relative transformations.
 ... vidstabtransform  V->V       Transform the frames.
 ... showwaves         A->V       Convert input audio to a video output.
"""

LOUDNORM_STDERR = """[Parsed_loudnorm_0 @ 0x5581]
{
	"input_i" : "-27.61",
	"input_tp" : "-4.47",
	"input_lra" : "18.06",
	"input_thresh" : "-39.20",
	"output_i" : "-16.58",
	"output_tp" : "-1.50",
	"output_lra" : "14.78",
	"output_thresh" : "-27.71",
	"normalization_type" : "dynamic",
	"target_offset" : "0.58"
}
"""


class FakeMedia:
    """Stand-in for ``Context.run``: fake ffprobe/ffmpeg/whisper-cli/gifski."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.probes: dict[str, dict] = {}
        self.out_size: int | Callable[[list[str]], int] = 64
        self.stderr: Callable[[list[str]], str] | None = None
        self.fail: Callable[[list[str]], bool] | None = None
        self.filters = FILTERS_ALL
        self.whisper_text = "Ciao mondo.\n Seconda riga."
        #: text helper files (concat lists, subtitles) present in the cwd of each call, by name
        self.snapshots: dict[str, str] = {}

    # helpers -------------------------------------------------------------------------
    def ffmpeg_calls(self) -> list[list[str]]:
        return [args for args, _cwd in self.calls if os.path.basename(args[0]) == "ffmpeg"
                and "-encoders" not in args and "-filters" not in args]

    def last(self) -> list[str]:
        return self.ffmpeg_calls()[-1]

    def joined(self) -> str:
        return "\n".join(" ".join(a) for a in self.ffmpeg_calls())

    # Context.run replacement ------------------------------------------------------------
    def run(self, ctx: Any, argv: Any, *, input: Any = None, timeout: Any = None, env: Any = None, cwd: Any = None,
            ok_codes: Any = (0,), on_line: Any = None, stderr_lines: bool = False,
            text: bool = True) -> subprocess.CompletedProcess:
        ctx.check_cancel()
        args = [os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in argv]
        cwd_s = os.fspath(cwd) if cwd is not None else None
        self.calls.append((args, cwd_s))
        if cwd_s and os.path.isdir(cwd_s):
            for entry in os.scandir(cwd_s):
                if entry.name.endswith((".ffconcat", ".srt", ".ass", ".vtt")):
                    self.snapshots[entry.name] = Path(entry.path).read_text(encoding="utf-8")
        tool = os.path.basename(args[0])
        if tool == "ffprobe":
            path = os.path.abspath(args[-1])
            data = self.probes.get(path)
            if data is None:
                raise ToolError("err.tool_failed", details="Invalid data found", tool="ffprobe", code=1)
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        if tool == "ffmpeg":
            if "-encoders" in args:
                return subprocess.CompletedProcess(args, 0, ENCODERS, "")
            if "-filters" in args:
                return subprocess.CompletedProcess(args, 0, self.filters, "")
            if self.fail and self.fail(args):
                raise ToolError("err.tool_failed", details="simulated failure", tool="ffmpeg", code=1)
            if on_line is not None:
                for line in ("out_time_us=N/A", "out_time_us=500000", "progress=continue", "out_time_us=1000000",
                             "progress=end"):
                    on_line(line)
            stderr = self.stderr(args) if self.stderr else ""
            out = args[-1]
            if out != "-":
                self._write_output(out, cwd_s, args)
            return subprocess.CompletedProcess(args, 0, "", stderr)
        if tool == "whisper-cli":
            base = args[args.index("-of") + 1]
            fmt = next(a[2:] for a in args if a.startswith("-o") and a not in ("-of",))
            Path(f"{base}.{fmt}").write_text(self.whisper_text, encoding="utf-8")
            if on_line is not None:
                on_line("whisper_print_progress_callback: progress =  50%")
            return subprocess.CompletedProcess(args, 0, "", "")
        if tool == "gifski":
            Path(args[args.index("-o") + 1]).write_bytes(b"GIF89a" + b"\0" * 32)
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def _write_output(self, out: str, cwd: str | None, args: list[str]) -> None:
        path = out[5:] if out.startswith("file:") else out
        if not os.path.isabs(path):
            path = os.path.join(cwd or os.getcwd(), path)
        size = self.out_size(args) if callable(self.out_size) else self.out_size
        name = os.path.basename(path)
        if re.search(r"%0?\d*d", name.replace("%%", "")):
            folder = os.path.dirname(path)
            for i in range(1, 4):
                with open(os.path.join(folder, name % i), "wb") as fh:
                    fh.write(b"\0" * size)
            return
        with open(path, "wb") as fh:
            fh.write(b"\0" * size)


@pytest.fixture
def fake(monkeypatch, media_mod):
    """Fake media toolchain: tools exist, ``Context.run`` is :class:`FakeMedia`."""
    fm = FakeMedia()
    real_path = tools.path
    present = {"ffmpeg", "ffprobe", "whisper-cli"}

    def fake_path(name: str) -> str | None:
        if name in present:
            return f"/fake/bin/{name}"
        if name == "gifski" and "gifski" in present:
            return "/fake/bin/gifski"
        return real_path(name)

    monkeypatch.setattr(tools, "path", fake_path)
    monkeypatch.setattr(jobs.Context, "run", lambda self, argv, **kw: fm.run(self, argv, **kw))
    fm.present = present  # type: ignore[attr-defined]
    with media_mod._CAPS_LOCK:
        media_mod._CAPS.clear()
        media_mod._HW_STATE.clear()
        media_mod._HW_FAILURES.clear()
    jobs._PROBE_CACHE.clear()
    yield fm
    jobs._PROBE_CACHE.clear()
    with media_mod._CAPS_LOCK:
        media_mod._CAPS.clear()


@pytest.fixture
def runner(make_runner):
    cfg = cfgmod.load()
    cfg["video"]["hwaccel"] = "none"
    r, events = make_runner(config=cfg, lang="en")
    return r


# ----------------------------------------------------------------------------------- samples

MP4_HEAD = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + bytes(range(256)) * 8
MKV_HEAD = b"\x1a\x45\xdf\xa3" + bytes(range(256)) * 8
WAV_HEAD = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(range(256)) * 8
MP3_HEAD = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 600
FLAC_HEAD = b"fLaC" + bytes(range(256)) * 4


def make_file(directory: Path, name: str, head: bytes) -> Path:
    p = directory / name
    p.write_bytes(head)
    return p


def vprobe(*, codec: str = "h264", w: int = 1920, h: int = 1080, dur: float = 10.0, audio: str | None = "aac",
           rotation: float | None = None, trc: str = "", pix: str = "yuv420p", subs: tuple = (),
           tags: dict | None = None, data: int = 0, fps: str = "30/1", channels: int = 2, rate: int = 48000) -> dict:
    video: dict = {"index": 0, "codec_type": "video", "codec_name": codec, "width": w, "height": h, "pix_fmt": pix,
                   "avg_frame_rate": fps, "r_frame_rate": fps, "sample_aspect_ratio": "1:1",
                   "field_order": "progressive", "disposition": {"attached_pic": 0}}
    if trc:
        video.update(color_transfer=trc, color_primaries="bt2020", color_space="bt2020nc")
    if rotation is not None:
        video["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rotation}]
    streams = [video]
    if audio:
        streams.append({"index": 1, "codec_type": "audio", "codec_name": audio, "channels": channels,
                        "sample_rate": str(rate), "bit_rate": "128000", "sample_fmt": "fltp"})
    for i, (codec_name, lang) in enumerate(subs):
        streams.append({"index": len(streams), "codec_type": "subtitle", "codec_name": codec_name,
                        "tags": {"language": lang}})
    for _ in range(data):
        streams.append({"index": len(streams), "codec_type": "data", "codec_name": "none"})
    return {"format": {"duration": f"{dur:.6f}", "format_name": "mov,mp4", "bit_rate": "4000000",
                       "tags": tags or {}}, "streams": streams}


def aprobe(*, codec: str = "pcm_s16le", dur: float = 30.0, channels: int = 2, rate: int = 44100,
           bitrate: int | None = 1411000, cover: bool = False) -> dict:
    streams: list[dict] = [{"index": 0, "codec_type": "audio", "codec_name": codec, "channels": channels,
                            "sample_rate": str(rate), "sample_fmt": "s16",
                            "bit_rate": str(bitrate) if bitrate else "N/A"}]
    if cover:
        streams.append({"index": 1, "codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500,
                        "disposition": {"attached_pic": 1}})
    return {"format": {"duration": f"{dur:.6f}", "format_name": "wav"}, "streams": streams}


def oggprobe(*, codec: str = "opus", dur: float = 30.0, cover: bool = False) -> dict:
    """Ogg demuxer: the Vorbis comments are stream tags, the file level is empty."""
    streams: list[dict] = [{"index": 0, "codec_type": "audio", "codec_name": codec, "channels": 2,
                            "sample_rate": "48000", "sample_fmt": "fltp", "bits_per_sample": 0,
                            "tags": {"title": "Vecchio", "ARTIST": "Lei"}}]
    if cover:
        streams.append({"index": 1, "codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500,
                        "disposition": {"attached_pic": 1}, "tags": {"comment": "Cover (front)"}})
    return {"format": {"duration": f"{dur:.6f}", "format_name": "ogg"}, "streams": streams}


def metadata_maps(args: list[str]) -> list[str]:
    """Values of the plain ``-map_metadata`` options (file-level target) of an ffmpeg command line."""
    return [args[i + 1] for i, a in enumerate(args) if a == "-map_metadata"]


def run_job(runner, action_id: str, files: list[Path], params: dict | None = None):
    return runner.run_sync(action_id, [str(f) for f in files], params or {})


def outputs(job) -> list[Path]:
    return [Path(o["path"]) for o in (job.result or {}).get("outputs") or []]


def fi(name: str, category: str, path: str = "") -> FileInfo:
    stem, ext = os.path.splitext(name)
    return FileInfo(path=path or f"/tmp/{name}", uri=f"file:///tmp/{name}", name=name, stem=stem,
                    ext=ext.lstrip(".").lower(), category=category, exists=True, size=1000)


# =================================================================================== pure helpers

def test_parse_rate_and_rotation():
    assert mu.parse_rate("30000/1001") == pytest.approx(29.97, rel=1e-3)
    assert mu.parse_rate("0/0") is None
    assert mu.parse_rate("25") == 25.0
    assert mu.parse_rate(None) is None
    assert mu.rotation_of({"side_data_list": [{"rotation": -90}]}) == 270
    assert mu.rotation_of({"side_data_list": [{"rotation": 90}]}) == 90
    assert mu.rotation_of({"tags": {"rotate": "90"}}) == 270  # legacy tag is clockwise
    assert mu.rotation_of({}) == 0


def test_parse_probe_full():
    data = vprobe(codec="hevc", w=3840, h=2160, rotation=-90, trc="smpte2084", pix="yuv420p10le",
                  subs=(("subrip", "ita"), ("hdmv_pgs_subtitle", "eng")), tags={"location": "+45.4+009.1/"},
                  data=1, fps="30000/1001")
    data["streams"][0]["r_frame_rate"] = "60/1"
    data["streams"].append({"index": 9, "codec_type": "video", "codec_name": "mjpeg",
                            "disposition": {"attached_pic": 1}})
    data["streams"].append({"index": 10, "codec_type": "attachment", "codec_name": "ttf"})
    info = mu.parse_probe(data, 123)
    assert info.duration == pytest.approx(10.0)
    assert info.size == 123
    v = info.video
    assert v is not None and v.codec == "hevc"
    assert v.display_size == (2160, 3840)
    assert v.hdr and v.bit_depth == 10 and v.vfr
    assert [p.index for p in info.pictures] == [9]
    assert info.audio is not None and info.audio.codec == "aac"
    assert [s.text for s in info.subs] == [True, False]
    assert info.data_streams == 1 and info.attachments == 1
    assert info.tags["location"].startswith("+45")


def test_parse_probe_duration_fallbacks():
    data = {"format": {}, "streams": [{"index": 0, "codec_type": "audio", "codec_name": "opus",
                                       "tags": {"DURATION": "00:01:02.500000000"}}]}
    info = mu.parse_probe(data)
    assert info.duration == pytest.approx(62.5)
    assert mu.parse_probe({}).duration is None
    assert mu.parse_probe(None).video is None


def test_geometry_helpers():
    assert mu.even(7) == 8 and mu.even(1) == 2 and mu.even(1080) == 1080
    assert mu.fit_short_side(1920, 1080, 720) == (1280, 720)
    assert mu.fit_short_side(1080, 1920, 720) == (720, 1280)
    assert mu.fit_short_side(1280, 720, 1080) is None
    assert mu.fit_short_side(0, 0, 720) is None
    assert mu.crop_rect({"x": 11, "y": 21, "w": 501, "h": 301}, 1920, 1080) == (10, 20, 500, 300)
    assert mu.crop_rect({"x": 1800, "y": 0, "w": 400, "h": 100}, 1920, 1080) == (1800, 0, 120, 100)
    assert mu.crop_rect({"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}, 1920, 1080) == (480, 270, 960, 540)
    assert mu.crop_rect('{"x": 0, "y": 0, "w": 100, "h": 50}', 1920, 1080) == (0, 0, 100, 50)
    assert mu.crop_rect([0, 0, 64, 64], 1920, 1080) == (0, 0, 64, 64)
    for bad in ({"x": 0, "y": 0, "w": 1, "h": 1}, "nonsense", None, {"x": "a"}):
        with pytest.raises(ValueError):
            mu.crop_rect(bad, 1920, 1080)
    assert mu.crop_filter(10, 20, 500, 300) == "crop=500:300:10:20"


def test_atempo_chain():
    assert mu.atempo_chain(1.5) == "atempo=1.5"
    assert mu.atempo_chain(2) == "atempo=2"
    assert mu.atempo_chain(4) == "atempo=2,atempo=2"
    assert mu.atempo_chain(3) == "atempo=2,atempo=1.5"
    assert mu.atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert mu.atempo_chain(0.3) == "atempo=0.5,atempo=0.6"
    assert mu.atempo_chain(1) == "atempo=1"
    with pytest.raises(ValueError):
        mu.atempo_chain(0)


def test_ff_escape_matches_documentation_example():
    assert (mu.ff_escape("this is a 'string': may contain one, or more, special characters")
            == "this is a \\\\\\'string\\\\\\'\\\\: may contain one\\, or more\\, special characters")


def test_gif_filter_and_tonemap():
    graph = mu.gif_filter(15, 480, prefix="bwdif=mode=send_frame")
    assert graph.startswith("bwdif=mode=send_frame,fps=15,scale=480:-1:flags=lanczos,split[a][b]")
    assert "palettegen=max_colors=256:stats_mode=diff" in graph and "paletteuse=dither=sierra2_4a" in graph
    assert "scale" not in mu.gif_filter(10, None)
    assert "zscale" in mu.TONEMAP and mu.TONEMAP.endswith("format=yuv420p")


def test_container_tables():
    assert mu.container_of("m4v") == "mp4" and mu.container_of("MKV") == "mkv"
    assert mu.can_copy_video("mp4", "h264") and not mu.can_copy_video("webm", "h264")
    assert mu.can_copy_video("mkv", "anything")
    assert mu.can_copy_audio("mp4", "aac") and not mu.can_copy_audio("mp4", "vorbis")
    assert mu.can_copy_audio("webm", "opus") and not mu.can_copy_audio("webm", "aac")
    srt = mu.SStream(index=2, codec="subrip")
    pgs = mu.SStream(index=3, codec="hdmv_pgs_subtitle")
    movtext = mu.SStream(index=4, codec="mov_text")
    assert mu.subtitle_codec_for("mp4", srt) == "mov_text"
    assert mu.subtitle_codec_for("mp4", movtext) == "copy"
    assert mu.subtitle_codec_for("mp4", pgs) is None
    assert mu.subtitle_codec_for("mkv", pgs) == "copy"
    assert mu.subtitle_codec_for("mkv", movtext) == "srt"
    assert mu.subtitle_codec_for("webm", srt) == "webvtt"
    assert mu.audio_ext_for_codec("aac") == ("m4a", "ipod")
    assert mu.audio_ext_for_codec("pcm_s24le") == ("wav", "wav")
    assert mu.audio_ext_for_codec("vorbis") == ("ogg", "ogg")
    assert mu.audio_ext_for_codec("truehd")[0] == "mka"


def test_video_codec_args():
    h264 = mu.video_codec_args("h264", 23, "medium")
    assert h264[:2] == ["-c:v", "libx264"] and "yuv420p" in h264 and "-crf" in h264
    vs = mu.VStream(index=0, color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc")
    hevc = mu.video_codec_args("hevc", 28, ten_bit=True, vs=vs)
    assert "yuv420p10le" in hevc and "hvc1" in hevc and "smpte2084" in hevc
    vp9 = mu.video_codec_args("vp9", 32)
    assert vp9[vp9.index("-b:v") + 1] == "0" and "-row-mt" in vp9
    assert mu.video_codec_args("av1", 35)[1] == "libsvtav1"
    assert mu.video_codec_args("av1", 35, encoder="libaom-av1")[1] == "libaom-av1"
    with pytest.raises(ValueError):
        mu.video_codec_args("mpeg2", 5)


def test_audio_targets():
    cfg = {"audio": {"mp3_quality": 4, "opus_bitrate": "96k"}}
    mp3 = mu.audio_target("mp3", mu.AStream(index=0, channels=6), cfg)
    assert mp3.args[:4] == ["-c:a", "libmp3lame", "-q:a", "4"] and "-ac" in mp3.args
    assert mu.audio_target("mp3", None, cfg, bitrate=128).args[2:4] == ["-b:a", "128k"]
    assert mu.audio_target("opus", None, cfg).args[3] == "96k"
    m4a = mu.audio_target("m4a")
    assert m4a.muxer == "ipod" and "+faststart" in m4a.args
    assert mu.audio_target("m4r").muxer == "ipod"
    wav24 = mu.audio_target("wav", mu.AStream(index=0, bits=24))
    assert "pcm_s24le" in wav24.args and wav24.lossless
    assert "pcm_s16be" in mu.audio_target("aiff").args
    with pytest.raises(ValueError):
        mu.audio_target("xyz")
    alac = mu.same_format_audio("m4a", mu.AStream(index=0, codec="alac"))
    assert "alac" in alac.args and alac.lossless
    assert mu.same_format_audio("ogg", mu.AStream(index=0, codec="opus")).codec == "libopus"
    assert mu.same_format_audio("ape", mu.AStream(index=0, codec="ape")).ext == "flac"
    assert mu.same_format_audio("amr", mu.AStream(index=0, codec="amr_nb")).ext == "m4a"
    assert mu.same_format_audio("mp2", None).ext == "mp3"
    assert mu.parse_bitrate("128k") == 128 and mu.parse_bitrate("1.5M") == 1500
    assert mu.parse_bitrate("192000") == 192 and mu.parse_bitrate("junk", 64) == 64


def test_aac_rate_follows_the_channels():
    assert mu.aac_kbps(128, 1) == 128 and mu.aac_kbps(192, 2) == 192
    assert mu.aac_kbps(128, 6) == 384 and mu.aac_kbps(96, 6) == 288
    assert mu.aac_kbps(160, 8) == mu.AAC_MAX_KBPS == 512
    assert mu.aac_kbps(1000, 6) == 1000  # never below the rate asked for
    surround = mu.AStream(index=0, channels=6)
    args = mu.audio_target("m4a", surround).args
    assert args[args.index("-b:a") + 1] == "512k" and "-ac" not in args
    args = mu.audio_target("m4a", surround, bitrate=128).args
    assert args[args.index("-b:a") + 1] == "384k"
    args = mu.audio_target("m4r", surround).args  # ringtones are stereo
    assert args[args.index("-b:a") + 1] == "192k" and args[args.index("-ac") + 1] == "2"
    assert "192k" in mu.audio_target("m4a", mu.AStream(index=0, channels=2)).args


def test_lossy_sources_are_16_bit():
    def bits(**stream: Any) -> int:
        return mu._audio_bits(stream)

    # lossy decoders output float samples whatever the source was
    for codec in ("mp3", "aac", "opus", "vorbis", "ac3"):
        assert bits(codec_name=codec, sample_fmt="fltp", bits_per_sample=0) == 16
    # lossless: the declared depth, else the decoder sample format
    assert bits(codec_name="flac", sample_fmt="s32", bits_per_raw_sample="24") == 24
    assert bits(codec_name="flac", sample_fmt="s16", bits_per_raw_sample="16") == 16
    assert bits(codec_name="pcm_s24le", sample_fmt="s32", bits_per_sample=24) == 24
    assert bits(codec_name="pcm_s16le", sample_fmt="s16", bits_per_sample=16) == 16
    assert bits(codec_name="ape", sample_fmt="s32p") == 24
    # ADPCM/DSD declare the size of a coded sample (4 and 1 bits), which says nothing about the audio depth
    assert bits(codec_name="adpcm_ms", sample_fmt="s16", bits_per_sample=4) == 16
    assert bits(codec_name="dsd_lsbf_planar", sample_fmt="fltp", bits_per_sample=1) == 32
    mp3 = mu.AStream(index=0, codec="mp3", bits=16)
    assert "pcm_s16le" in mu.audio_target("wav", mp3).args
    assert "pcm_s16be" in mu.audio_target("aiff", mp3).args
    flac = mu.audio_target("flac", mp3).args
    assert flac[flac.index("-sample_fmt") + 1] == "s16"
    # through ffprobe JSON
    info = mu.parse_probe({"format": {"format_name": "mp3"},
                           "streams": [{"index": 0, "codec_type": "audio", "codec_name": "mp3", "sample_fmt": "fltp",
                                        "bits_per_sample": 0}]})
    assert info.audio.bits == 16


def test_wav_pcm_codec():
    def codec(name: str, bits: int = 16) -> str:
        return mu.wav_pcm_codec(mu.AStream(index=0, codec=name, bits=bits))

    for same in ("pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_alaw", "pcm_mulaw"):
        assert codec(same) == same
    assert codec("pcm_s16be") == "pcm_s16le" and codec("pcm_s24be", 24) == "pcm_s24le"
    assert codec("pcm_s32be", 32) == "pcm_s32le" and codec("pcm_f32be", 32) == "pcm_f32le"
    assert codec("pcm_f64be", 64) == "pcm_f64le" and codec("pcm_s8", 8) == "pcm_u8"
    assert codec("pcm_bluray", 24) == "pcm_s24le" and codec("pcm_dvd", 20) == "pcm_s24le"
    assert mu.audio_ext_for_codec("pcm_s16be") == ("wav", "wav")


def test_target_bitrate_plan():
    plan = mu.plan_target_bitrate(60, 16_000_000, 1080, True)
    assert plan.audio_kbps == 128 and 1900 <= plan.video_kbps <= 2000 and plan.max_short == 720
    plan = mu.plan_target_bitrate(60, 200_000_000, 1080, False)
    assert plan.audio_kbps == 0 and plan.max_short is None
    with pytest.raises(ValueError):
        mu.plan_target_bitrate(3600, 1_000_000, 1080, True)
    with pytest.raises(ValueError):
        mu.plan_target_bitrate(0, 1_000_000, 1080, True)
    minimum = mu.min_target_bytes(3600, True)
    assert minimum % 500_000 == 0
    mu.plan_target_bitrate(3600, minimum, 1080, True)


def test_loudnorm_filter_and_parse():
    first = mu.loudnorm_filter(-14, -1.0, 11)
    assert first == "loudnorm=I=-14:TP=-1:LRA=11:print_format=json"
    measured = mu.parse_loudnorm("noise\n" + LOUDNORM_STDERR)
    assert measured["input_i"] == pytest.approx(-27.61) and measured["target_offset"] == pytest.approx(0.58)
    second = mu.loudnorm_filter(-14, -1.0, 11, measured)
    assert "measured_I=-27.61" in second and "offset=0.58" in second and "linear=true" in second
    clamped = mu.loudnorm_filter(-23, -1, 11, {"input_i": -120, "input_tp": 3, "input_lra": 150,
                                               "input_thresh": 5, "target_offset": -200})
    assert "measured_I=-99.00" in clamped and "measured_LRA=99.00" in clamped and "measured_thresh=0.00" in clamped
    with pytest.raises(ValueError):
        mu.parse_loudnorm(LOUDNORM_STDERR.replace('"-27.61"', '"-inf"'))
    with pytest.raises(ValueError):
        mu.parse_loudnorm("no json here")


def test_silencedetect_parsing():
    log = """[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 1.5 | silence_duration: 1.5
[silencedetect @ 0x1] silence_start: 10.2
[silencedetect @ 0x1] silence_end: 11 | silence_duration: 0.8
[silencedetect @ 0x1] silence_start: 28.5
"""
    sil = mu.parse_silencedetect(log)
    assert sil == [(0.0, 1.5), (10.2, 11.0), (28.5, None)]
    start, end = mu.silence_trim_bounds(sil, 30.0)
    assert start == pytest.approx(1.4) and end == pytest.approx(28.6)
    assert mu.silence_trim_bounds([], 30.0) == (0.0, 30.0)
    assert mu.silence_trim_bounds([(5.0, 6.0)], 30.0) == (0.0, 30.0)


def test_misc_text_helpers():
    assert mu.whisper_progress("whisper_print_progress_callback: progress =  45%") == 45
    assert mu.whisper_progress("hello") is None
    assert mu.split_points(600, minutes=5) == [300.0]
    assert mu.split_points(601, minutes=5) == [300.0, 600.0]
    assert mu.split_points(90, parts=3) == [30.0, 60.0]
    with pytest.raises(ValueError):
        mu.split_points(90, parts=1)
    assert mu.timestamp_name(83.5) == "00-01-23"
    assert mu.timestamp_name(3723.25, with_ms=True) == "01-02-03.250"
    assert mu.drawtext_time(83) == "1\\:23"
    assert mu.drawtext_time(3723) == "1\\:02\\:03"
    assert mu.segment_pattern("50% off", "mp4") == "50%% off %03d.mp4"
    lst = mu.concat_list(["a.mp4", "it's.mp4"])
    assert lst.splitlines() == ["ffconcat version 1.0", "file 'a.mp4'", "file 'it'\\''s.mp4'"]
    with pytest.raises(ValueError):
        mu.concat_list(["bad\nname.mp4"])
    m3u = mu.m3u8_text([("a/b.mp3", 61.6, "Artist -  Title"), ("c.mp3", None, "c")])
    assert m3u.splitlines() == ["#EXTM3U", "#EXTINF:62,Artist - Title", "a/b.mp3", "#EXTINF:-1,c", "c.mp3"]
    assert mu.fmt_num(2.0) == "2" and mu.fmt_num(1.5) == "1.5" and mu.fmt_num(1 / 3) == "0.333333"


def test_language_and_sidecars(tmp_path):
    assert mu.lang3("it") == "ita" and mu.lang3("eng") == "eng" and mu.lang3("zz") == ""
    assert mu.lang_from_name("film.it.srt", "film") == "it"
    assert mu.lang_from_name("film.srt", "film") == ""
    video = tmp_path / "film.mp4"
    video.write_bytes(b"x")
    for name in ("film.en.srt", "film.it.srt", "film.vtt", "film.srt", "other.srt", "film.it.txt"):
        (tmp_path / name).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    found = [p.name for p in mu.sidecar_subtitles(video, prefer_lang="it")]
    assert found == ["film.srt", "film.vtt", "film.it.srt", "film.en.srt"]
    assert mu.sidecar_subtitles(tmp_path / "missing" / "x.mp4") == []


def test_decode_subtitle():
    assert mu.decode_subtitle("ciào".encode("utf-8")) == "ciào"
    assert mu.decode_subtitle(b"\xef\xbb\xbfhi") == "hi"
    assert mu.decode_subtitle("perché".encode("cp1252")).endswith("é")
    assert mu.decode_subtitle("hey".encode("utf-16")) == "hey"


def test_normalize_tag_key():
    assert mu.normalize_tag_key("ItemList:Title") == "title"
    assert mu.normalize_tag_key("Tag:tracknumber") == "tracknumber"
    assert mu.normalize_tag_key("ID3v2_3:Year") == "date"
    assert mu.normalize_tag_key("TPE1") == "artist"
    assert mu.normalize_tag_key("Album Artist") == "albumartist"
    assert mu.normalize_tag_key("album artist", ffmpeg=True) == "album_artist"
    assert mu.normalize_tag_key("track", ffmpeg=True) == "track"
    assert mu.normalize_tag_key("picture") == "cover"
    assert mu.normalize_tag_key("custom_key") == "custom_key"


# ====================================================================================== registry

def test_actions_registered_with_requirements(media_mod):
    ids = [a.id for a in registry.all_actions() if a.module.endswith(".media")]
    assert len(ids) == len(set(ids)) >= 90
    for act in registry.all_actions():
        if not act.module.endswith(".media"):
            continue
        flat = [r if isinstance(r, str) else r[0] for r in act.requires]
        if act.id not in ("audio.playlist", "audio.tags", "audio.extract.cover"):
            assert "ffmpeg" in flat, act.id
        if "transcribe" in act.id or act.id == "video.subtitles.generate":
            assert "whisper-cli" in flat
        if act.mode == "dialog":
            assert act.dialog in ("form", "crop", "trim", "metadata")
    assert registry.get("video.trim").dialog == "trim" and registry.get("video.trim").multi == "single"
    assert registry.get("video.crop").dialog == "crop"
    assert registry.get("audio.tags").dialog == "metadata"
    assert registry.get("media.cover_video").mixed_ok and registry.get("media.to_mp3").mixed_ok
    for key in ("video", "audio", "multi:video", "multi:audio"):
        assert ("main", key) in registry.layouts()


def _ids(nodes):
    return [n["id"] for n in nodes]


def test_video_menu_layout(media_mod):
    menu = registry.build_menu([fi("clip.mp4", "video")], {"wheel": {"show_unavailable": True}}, "it")
    main = menu["pages"][0]["items"]
    assert _ids(main) == ["video.convert", "video.compress", "video.trim", "video.crop", "video.resize",
                          "video.edit", "video.extract", "video.more"]
    convert = registry.find_node(menu, "video.convert")
    assert "video.convert.mp4" not in _ids(convert["items"])  # never offer the source format
    assert convert["label"] == "Converti" and len(convert["items"]) <= 8
    compress = registry.find_node(menu, "video.compress")
    assert compress["default"] == "video.compress.balanced" or not compress["items"][0]["available"]
    node = registry.find_node(menu, "video.compress.balanced")
    assert node["label"] == "Bilanciata" and node["mode"] == "quick"
    assert any(p["name"] == "replace" for p in node["params"])

    def check(nodes):
        assert len(nodes) <= 8
        for n in nodes:
            if n["type"] == "group":
                check(n["items"])

    check(main)
    mkv = registry.build_menu([fi("clip.mkv", "video")], {}, "en")
    assert "video.convert.mp4" in _ids(registry.find_node(mkv, "video.convert")["items"])
    assert registry.find_node(mkv, "video.rotate.meta") is None  # metadata rotation only for MP4/MOV


def test_audio_and_multi_menus(media_mod):
    menu = registry.build_menu([fi("song.mp3", "audio")], {}, "en")
    assert _ids(menu["pages"][0]["items"]) == ["audio.convert", "audio.compress", "audio.trim", "audio.normalize",
                                              "audio.tags", "audio.transcribe", "audio.edit", "audio.more"]
    assert "audio.convert.mp3" not in _ids(registry.find_node(menu, "audio.convert")["items"])
    playlist = registry.build_menu([fi("list.m3u", "audio")], {}, "en")
    assert all(not n["id"].startswith("audio.") for n in playlist["pages"][0]["items"])
    many = registry.build_menu([fi("a.mp3", "audio"), fi("b.wav", "audio")], {}, "en")
    top = _ids(many["pages"][0]["items"])
    assert top[0] == "audio.join" and "audio.playlist" in top and "audio.trim" not in top
    vids = registry.build_menu([fi("a.mp4", "video"), fi("b.mov", "video")], {}, "en")
    assert _ids(vids["pages"][0]["items"])[0] == "video.join"
    assert registry.find_node(vids, "video.crop") is None


def test_mixed_menus(media_mod):
    menu = registry.build_menu([fi("cover.jpg", "image"), fi("song.mp3", "audio")], {}, "en")
    assert registry.find_node(menu, "media.cover_video") is not None
    assert registry.find_node(menu, "media.to_mp3") is None
    menu = registry.build_menu([fi("a.mp4", "video"), fi("b.flac", "audio")], {}, "it")
    node = registry.find_node(menu, "media.to_mp3")
    assert node is not None and node["label"] == "Tutto in MP3"
    single = registry.build_menu([fi("a.mp4", "video")], {}, "en")
    assert registry.find_node(single, "media.to_mp3") is None


def test_locales_complete(media_mod):
    en = json.loads((LOCALES / "en" / "media.json").read_text(encoding="utf-8"))
    it = json.loads((LOCALES / "it" / "media.json").read_text(encoding="utf-8"))
    assert set(en) == set(it)
    for key in en:
        assert set(re.findall(r"\{(\w+)\}", en[key])) == set(re.findall(r"\{(\w+)\}", it[key])), key
    i18n.reload()
    for act in registry.all_actions():
        if not act.module.endswith(".media"):
            continue
        for key in (act.label, act.description):
            if key.startswith("media."):
                assert i18n.has(key, "it") and i18n.has(key, "en"), key
        for p in act.all_params():
            for key in (p.label, p.help, p.unit):
                if key and key.startswith("media."):
                    assert i18n.has(key, "it"), key
            for _v, lbl in p.option_pairs([]):
                if lbl.startswith("media."):
                    assert i18n.has(lbl, "it"), lbl
    assert i18n.t("media.act.mute", "it") == "Togli audio"
    assert i18n.tn("media.msg.frames", 3, "it") == "3 fotogrammi"


# ===================================================================================== video jobs

def test_convert_mp4_reencodes_hevc(fake, runner, tmp_path):
    src = make_file(tmp_path, "-my clip à.mkv", MKV_HEAD)
    fake.probes[str(src)] = vprobe(codec="hevc", subs=(("subrip", "ita"), ("hdmv_pgs_subtitle", "eng")))
    job = run_job(runner, "video.convert.mp4", [src])
    assert job.state == "done", job.error
    out = outputs(job)
    assert [p.name for p in out] == ["-my clip à.mp4"]
    args = fake.last()
    assert args[args.index("-i") + 1] == f"file:{src}"
    assert "libx264" in args and "yuv420p" in args and "+faststart" in args
    assert args[args.index("-c:a:0") + 1] == "copy"
    assert "mov_text" in args
    assert args[-1].startswith("file:") and ".part" in args[-1]
    assert any("Subtitles not supported" in w for w in job.warnings)


def test_convert_mp4_remux_when_h264(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mkv", MKV_HEAD)
    fake.probes[str(src)] = vprobe(codec="h264", audio="vorbis")
    job = run_job(runner, "video.convert.mp4", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a:0") + 1] == "aac"


def test_convert_hdr_to_h264_tonemaps(fake, runner, tmp_path):
    src = make_file(tmp_path, "hdr.mkv", MKV_HEAD)
    fake.probes[str(src)] = vprobe(codec="hevc", trc="smpte2084", pix="yuv420p10le")
    job = run_job(runner, "video.convert.mp4", [src])
    assert job.state == "done", job.error
    vf = fake.last()[fake.last().index("-vf") + 1]
    assert "zscale" in vf and "tonemap" in vf
    assert any("HDR" in w for w in job.warnings)
    job = run_job(runner, "video.convert.mp4", [src], {"codec": "hevc"})
    assert job.state == "done", job.error
    args = fake.last()
    # HEVC source to HEVC: stream copy, HDR untouched
    assert args[args.index("-c:v") + 1] == "copy" and "hvc1" in args


def test_convert_failure_falls_back_to_reencode(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(codec="h264")
    fake.fail = lambda args: "-c" in args and args[args.index("-c") + 1] == "copy" and "-fflags" in args
    job = run_job(runner, "video.convert.mkv", [src])
    assert job.state == "done", job.error
    assert "libx264" in fake.last()
    assert any("re-encoded" in w for w in job.warnings)


def test_gif_ffmpeg_and_gifski(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(dur=5)
    job = run_job(runner, "video.convert.gif", [src], {"width": 480, "fps": 12})
    assert job.state == "done", job.error
    vf = fake.last()[fake.last().index("-vf") + 1]
    assert vf.startswith("fps=12,scale=480:-1") and "paletteuse" in vf
    assert outputs(job)[0].name == "clip.gif"
    fake.present.add("gifski")
    job = run_job(runner, "video.convert.gif", [src])
    assert job.state == "done", job.error
    gifski = [a for a, _ in fake.calls if a[0].endswith("gifski")]
    assert gifski and "--fps" in gifski[-1] and any(p.endswith(".png") for p in gifski[-1])


def test_gifski_keeps_the_frame_width(fake, runner, tmp_path, monkeypatch):
    """Without --width gifski halves anything above about 800×600 (the "Original" size of a 720p clip)."""
    fake.present.add("gifski")
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(w=1280, h=720, dur=5)

    def last_gifski() -> list[str]:
        return [a for a, _ in fake.calls if a[0].endswith("gifski")][-1]

    def width_of(args: list[str]) -> str:
        return args[args.index("--width") + 1]

    job = run_job(runner, "video.convert.gif", [src], {"width": 0})
    assert job.state == "done", job.error
    assert width_of(last_gifski()) == "1280" and "--height" not in last_gifski()
    job = run_job(runner, "video.convert.gif", [src], {"width": 640})
    assert job.state == "done", job.error
    assert width_of(last_gifski()) == "640"
    # the size of the extracted frames wins over the probe (rotation, square pixels…)
    monkeypatch.setattr(mu, "png_size", lambda path: (720, 1280))
    job = run_job(runner, "video.convert.gif", [src], {"width": 0})
    assert job.state == "done", job.error
    assert width_of(last_gifski()) == "720"


def test_extract_audio_formats(fake, runner, tmp_path):
    src = make_file(tmp_path, "film.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(audio="aac")
    job = run_job(runner, "video.extract.audio", [src])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "film.m4a"
    args = fake.last()
    assert args[args.index("-c:a") + 1] == "copy" and args[args.index("-f") + 1] == "ipod"
    job = run_job(runner, "video.convert.mp3", [src], {"quality": 0})
    assert job.state == "done", job.error
    assert "libmp3lame" in fake.last() and fake.last()[fake.last().index("-q:a") + 1] == "0"
    job = run_job(runner, "video.convert.flac", [src])
    assert any("lossy" in w.lower() or "quality" in w.lower() for w in job.warnings)
    fake.probes[str(src)] = vprobe(audio=None)
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "video.extract.audio", [src])
    assert job.state == "failed" and "no audio" in job.error["message"]


def test_ogg_tags_reach_the_file_level(fake, runner, tmp_path):
    """MP3/MP4/FLAC/WAV muxers only write file-level tags, Ogg keeps them on the stream."""
    src = make_file(tmp_path, "voce.opus", b"OggS" + bytes(range(256)) * 4)
    fake.probes[str(src)] = oggprobe()
    assert mu.parse_probe(oggprobe()).stream_tags and not mu.parse_probe(aprobe()).stream_tags
    for action_id in ("audio.convert.mp3", "audio.convert.m4a", "audio.convert.flac", "audio.convert.wav",
                      "audio.convert.aiff", "audio.convert.m4r"):
        job = run_job(runner, action_id, [src])
        assert job.state == "done", job.error
        assert metadata_maps(fake.last()) == ["0", "0:s:0"], action_id
        assert "-map_metadata:s:v" not in fake.last()
    # trim with a cut (filter graph)
    job = run_job(runner, "audio.trim", [src], {"start": 5, "end": 10, "mode": "cut"})
    assert job.state == "done", job.error
    assert "-filter_complex" in fake.last() and metadata_maps(fake.last()) == ["0", "0:s:0"]
    # reverse: the tags come from the original, the second input
    job = run_job(runner, "audio.edit.reverse", [src])
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["1", "1:s:0"] and str(src) in " ".join(fake.last())
    # join: re-encode to the format of the first file
    mp3 = make_file(tmp_path, "zeta.mp3", MP3_HEAD)
    fake.probes[str(mp3)] = aprobe(codec="mp3")
    job = run_job(runner, "audio.join", [src, mp3])
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["0", "0:s:0"]
    # the title and picture type of a cover are stream tags too
    cov = make_file(tmp_path, "cov.ogg", b"OggS" + bytes(range(256)) * 4)
    fake.probes[str(cov)] = oggprobe(codec="vorbis", cover=True)
    job = run_job(runner, "audio.convert.mp3", [cov])
    assert job.state == "done", job.error
    args = fake.last()
    assert "0:1" in args and args[args.index("-map_metadata:s:v") + 1] == "0:s:1"
    # other containers: untouched
    job = run_job(runner, "audio.convert.flac", [mp3])
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["0"]


def test_cover_video_keeps_the_ogg_tags(fake, runner, tmp_path):
    src = make_file(tmp_path, "voce.opus", b"OggS" + bytes(range(256)) * 4)
    fake.probes[str(src)] = oggprobe()
    img = tmp_path / "cover.png"
    from PIL import Image
    Image.new("RGB", (64, 64), "red").save(img)
    job = run_job(runner, "media.cover_video", [img, src])
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["1", "1:s:0"]


def test_top_ring_labels_stand_alone(media_mod):
    """The multi-video ring shows these two outside their groups: the label must say what is removed/extracted."""
    top = registry.layouts()[("main", "multi:video")]
    assert "video.extract.audio" in top and "video.metadata.strip" in top
    for lang, extract, strip in (("it", "Estrai audio", "Togli metadati"), ("en", "Extract audio", "Strip metadata")):
        assert i18n.t(registry.get("video.extract.audio").label, lang) == extract
        assert i18n.t(registry.get("video.metadata.strip").label, lang) == strip


def test_extract_audio_pcm_goes_into_a_real_wav(fake, runner, tmp_path):
    """The WAV muxer refuses big-endian, Blu-ray and DVD PCM: they are repacked instead of copied."""
    src = make_file(tmp_path, "cam.mov", MP4_HEAD)
    for codec, bits, expected in (("pcm_s16be", 16, "pcm_s16le"), ("pcm_s24be", 24, "pcm_s24le"),
                                  ("pcm_bluray", 24, "pcm_s24le"), ("pcm_dvd", 20, "pcm_s24le"),
                                  ("pcm_s32be", 32, "pcm_s32le"), ("pcm_f32be", 32, "pcm_f32le"),
                                  ("pcm_s16le", 16, "copy"), ("pcm_s24le", 24, "copy"), ("pcm_mulaw", 8, "copy")):
        probe = vprobe(audio=codec)
        probe["streams"][1]["bits_per_sample"] = bits
        fake.probes[str(src)] = probe
        jobs._PROBE_CACHE.clear()
        job = run_job(runner, "video.extract.audio", [src])
        assert job.state == "done", job.error
        args = fake.last()
        assert args[args.index("-c:a") + 1] == expected, codec
        assert args[args.index("-f") + 1] == "wav" and "-rf64" in args
        assert "matroska" not in args and not job.warnings
        assert outputs(job)[0].suffix == ".wav"


def test_extract_audio_fallback_uses_the_matching_extension(fake, runner, tmp_path):
    src = make_file(tmp_path, "film.mkv", MKV_HEAD)
    fake.fail = lambda args: args[args.index("-c:a") + 1] == "copy"
    # lossy track (.dts copy refused) → M4A, under its own name
    fake.probes[str(src)] = vprobe(audio="dts")
    job = run_job(runner, "video.extract.audio", [src])
    assert job.state == "done", job.error
    assert [p.name for p in outputs(job)] == ["film.m4a"] and not (tmp_path / "film.dts").exists()
    assert fake.last()[fake.last().index("-f") + 1] == "ipod" and "matroska" not in fake.last()
    assert any("re-encoded" in w for w in job.warnings)
    # lossless track of an unknown family (.mka copy refused) → FLAC with its muxer
    fake.probes[str(src)] = vprobe(audio="truehd")
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "video.extract.audio", [src])
    assert job.state == "done", job.error
    assert [p.name for p in outputs(job)] == ["film.flac"]
    assert fake.last()[fake.last().index("-f") + 1] == "flac" and "matroska" not in fake.last()
    # same extension: the name reserved for the copy is reused
    fake.probes[str(src)] = vprobe(audio="flac")
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "video.extract.audio", [src])
    assert job.state == "done", job.error
    assert [p.name for p in outputs(job)] == ["film 2.flac"]  # film.flac comes from the previous job
    assert fake.last()[fake.last().index("-c:a") + 1] == "flac"


def test_surround_tracks_keep_their_rate_per_channel(fake, runner, tmp_path):
    src = make_file(tmp_path, "film.mkv", MKV_HEAD)
    probe = vprobe(audio="dts", channels=6)
    probe["streams"][1]["bit_rate"] = "1509000"
    fake.probes[str(src)] = probe
    job = run_job(runner, "video.convert.mp4", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-b:a:0") + 1] == "480k" and "-ac:a:0" not in args
    # compress presets: the stereo rate is scaled the same way
    for action_id, rate in (("video.compress.high", "480k"), ("video.compress.balanced", "384k"),
                            ("video.compress.small", "288k")):
        job = run_job(runner, action_id, [src])
        assert job.state == "done", job.error
        assert fake.last()[fake.last().index("-b:a:0") + 1] == rate, action_id
    # filter graphs re-encoding the audio
    job = run_job(runner, "video.speed.2", [src])
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-b:a") + 1] == "480k"
    job = run_job(runner, "video.trim", [src], {"start": 2, "end": 4, "mode": "cut"})
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-b:a") + 1] == "480k"
    job = run_job(runner, "video.edit.reverse", [src])
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-b:a") + 1] == "480k"
    # messaging downmixes to stereo, WebM too
    job = run_job(runner, "video.compress.messaging", [src])
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-b:a:0") + 1] == "128k" and "-ac:a:0" in fake.last()
    job = run_job(runner, "video.convert.webm", [src])
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-b:a:0") + 1] == "128k" and "-ac:a:0" in fake.last()


def test_compress_keeps_ac3_within_the_surround_rate(fake, runner, tmp_path):
    src = make_file(tmp_path, "film.mkv", MKV_HEAD)
    probe = vprobe(audio="ac3", channels=6)
    probe["streams"][1]["bit_rate"] = "448000"
    fake.probes[str(src)] = probe
    fake.out_size = 8
    job = run_job(runner, "video.compress.high", [src])  # 480k for 5.1: no need to touch a 448k track
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-c:a:0") + 1] == "copy"
    job = run_job(runner, "video.compress.small", [src])  # 288k: worth re-encoding
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-c:a:0") + 1] == "aac" and args[args.index("-b:a:0") + 1] == "288k"


def test_audio_compress_surround_rate(fake, runner, tmp_path):
    src = make_file(tmp_path, "film.m4a", MP4_HEAD)
    fake.probes[str(src)] = aprobe(codec="aac", channels=6, bitrate=384000)
    job = run_job(runner, "audio.compress.balanced", [src])  # 128k per stereo pair: 5.1 at 384k is already there
    assert job.state == "done", job.error
    assert job.result["kind"] == "none" and not fake.ffmpeg_calls()
    fake.probes[str(src)] = aprobe(codec="aac", channels=6, bitrate=640000)
    jobs._PROBE_CACHE.clear()
    fake.out_size = 8
    job = run_job(runner, "audio.compress.balanced", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-b:a") + 1] == "384k"


def test_compress_passthrough_and_success(fake, runner, tmp_path):
    src = make_file(tmp_path, "big.mov", MP4_HEAD)
    size = src.stat().st_size
    fake.probes[str(src)] = vprobe(w=3840, h=2160)
    fake.out_size = size + 10
    job = run_job(runner, "video.compress.balanced", [src])
    assert job.state == "done", job.error
    assert outputs(job) == [] and job.result["message"] == "Already optimized"
    assert not list(tmp_path.glob("*Compressed*"))
    fake.out_size = 10
    job = run_job(runner, "video.compress.balanced", [src])
    assert job.state == "done", job.error
    out = outputs(job)
    assert [p.name for p in out] == ["big (Compressed).mp4"]
    vf = fake.last()[fake.last().index("-vf") + 1]
    assert "scale=1920:1080" in vf
    assert fake.last()[fake.last().index("-crf") + 1] == "26"
    assert job.result["size_before"] == size


def test_compress_replace_original(fake, runner, tmp_path):
    src = make_file(tmp_path, "keep.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe()
    fake.out_size = 5
    job = run_job(runner, "video.compress.small", [src], {"replace": True})
    assert job.state == "done", job.error
    assert outputs(job) == [src] and src.stat().st_size == 5
    res = runner.undo(job.id)
    assert res["restored"] == 1 and src.stat().st_size == len(MP4_HEAD)


def test_target_size_two_pass(fake, runner, tmp_path):
    src = make_file(tmp_path, "long.mp4", MP4_HEAD + b"\0" * 2_000_000)
    fake.probes[str(src)] = vprobe(dur=60)
    job = run_job(runner, "video.compress.target", [src], {"size_mb": 2})
    assert job.state == "done", job.error
    passes = [(a, cwd) for a, cwd in fake.calls if "-pass" in a]
    assert [a[a.index("-pass") + 1] for a, _ in passes] == ["1", "2"]
    assert all(cwd and a[a.index("-passlogfile") + 1] == "dw2pass" for a, cwd in passes)
    assert passes[0][0][-1] == "-"
    job = run_job(runner, "video.compress.target", [src], {"size_mb": 0.05})
    assert job.state == "failed" and "too small" in job.error["message"]
    job = run_job(runner, "video.compress.target", [src], {"size_mb": 50})
    assert job.state == "done" and job.result["kind"] == "none"


def test_resize_and_no_upscale(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(w=1920, h=1080)
    job = run_job(runner, "video.resize.720", [src])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "clip (720p).mp4"
    assert "scale=1280:720:flags=lanczos" in fake.last()[fake.last().index("-vf") + 1]
    job = run_job(runner, "video.resize.2160", [src])
    assert job.state == "failed" and "never enlarged" in job.error["message"]


def test_crop_uses_rotated_dimensions(fake, runner, tmp_path):
    src = make_file(tmp_path, "phone.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(w=1920, h=1080, rotation=-90)
    job = run_job(runner, "video.crop", [src], {"rect": {"x": 100, "y": 1500, "w": 900, "h": 1000}})
    assert job.state == "done", job.error
    assert "crop=900:420:100:1500" in fake.last()[fake.last().index("-vf") + 1]
    assert outputs(job)[0].name == "phone (Cropped).mp4"
    job = run_job(runner, "video.crop", [src], {"rect": {"x": 0, "y": 0, "w": 1, "h": 1}})
    assert job.state == "failed" and "crop" in job.error["message"].lower()


def test_trim_modes(fake, runner, tmp_path):
    src = make_file(tmp_path, "talk.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(dur=60)
    job = run_job(runner, "video.trim", [src], {"start": 5, "end": "0:20"})
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-ss") + 1] == "5.000" and args[args.index("-t") + 1] == "15.000"
    assert "libx264" in args and outputs(job)[0].name == "talk (Trimmed).mp4"
    job = run_job(runner, "video.trim", [src], {"start": 5, "end": 20, "fast": True})
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-c") + 1] == "copy" and "make_zero" in fake.last()
    job = run_job(runner, "video.trim", [src], {"start": 10, "end": 20, "mode": "cut"})
    assert job.state == "done", job.error
    graph = fake.last()[fake.last().index("-filter_complex") + 1]
    assert "trim=end=10.000" in graph and "trim=start=20.000" in graph and "concat=n=2:v=1:a=1" in graph
    job = run_job(runner, "video.trim", [src], {"start": 10, "end": 20, "mode": "cut", "fast": True})
    assert job.state == "done", job.error
    concat = [(a, cwd) for a, cwd in fake.calls if "concat" in a and "-f" in a]
    assert concat and concat[-1][1] is not None
    assert fake.snapshots["list.ffconcat"].count("file '") == 2
    job = run_job(runner, "video.trim", [src], {"start": 30, "end": 10})
    assert job.state == "failed" and "start" in job.error["message"]
    job = run_job(runner, "video.trim", [src], {"start": 0, "end": 60, "mode": "cut"})
    assert job.state == "failed" and "whole" in job.error["message"]


def test_mute_speed_rotate_meta(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(rotation=-90)
    job = run_job(runner, "video.edit.mute", [src])
    assert job.state == "done", job.error
    assert "-an" in fake.last() and outputs(job)[0].name == "clip (Muted).mp4"
    job = run_job(runner, "video.speed.2", [src])
    assert job.state == "done", job.error
    graph = fake.last()[fake.last().index("-filter_complex") + 1]
    assert "setpts=PTS/2" in graph and "atempo=2" in graph and "fps=30" in graph
    assert outputs(job)[0].name == "clip (Speed 2x).mp4"
    job = run_job(runner, "video.rotate.meta", [src], {"angle": "cw"})
    assert job.state == "done", job.error
    args = fake.last()
    assert args[0:0] == [] and "-display_rotation:0" in args
    assert args[args.index("-display_rotation:0") + 1] == "180"
    fake.probes[str(src)] = vprobe(audio=None)
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "video.edit.mute", [src])
    assert job.state == "failed"


def test_stream_copy_container_fallbacks(fake, runner, tmp_path, media_mod):
    assert media_mod._copy_container(fi("a.mp4", "video")) == ("mp4", [])
    assert media_mod._copy_container(fi("a.qt", "video")) == ("qt", ["-f", "mov"])
    assert media_mod._copy_container(fi("a.rmvb", "video")) == ("mkv", [])
    # RealMedia cannot be written by ffmpeg: stream copies land in Matroska
    rm = make_file(tmp_path, "old clip.rmvb", b".RMF\x00\x00\x00\x12" + bytes(range(256)) * 4)
    fake.probes[str(rm)] = vprobe(codec="rv40", audio="cook")
    job = run_job(runner, "video.edit.mute", [rm])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "old clip (Muted).mkv"
    job = run_job(runner, "video.metadata.strip", [rm])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "old clip (No Metadata).mkv"
    # .qt is QuickTime: same extension, muxer named explicitly
    qt = make_file(tmp_path, "home.qt", MP4_HEAD)
    fake.probes[str(qt)] = vprobe()
    job = run_job(runner, "video.metadata.gps", [qt])
    assert job.state == "done" and job.result["kind"] == "none"
    job = run_job(runner, "video.rotate.meta", [qt], {"angle": "180"})
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-f") + 1] == "mov" and outputs(job)[0].suffix == ".qt"
    job = run_job(runner, "video.split", [qt], {"split_mode": "parts", "parts": 2})
    assert job.state == "done", job.error
    args = next(a for a, _c in reversed(fake.calls) if "segment" in a)
    assert args[args.index("-segment_format") + 1] == "mov"


def test_reverse_segments(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(w=3840, h=2160, dur=12, fps="60/1")
    job = run_job(runner, "video.edit.reverse", [src])
    assert job.state == "done", job.error
    segs = [a for a, _ in fake.calls if a and a[-1].startswith("seg")]
    assert len(segs) >= 2 and all("reverse" in a[a.index("-vf") + 1] for a in segs)
    listing = fake.snapshots["list.ffconcat"].splitlines()[1:]
    assert len(listing) == len(segs) and listing == sorted(listing, reverse=True)


def test_frames_snapshot_contact_sheet(fake, runner, tmp_path):
    src = make_file(tmp_path, "trip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(dur=100)
    job = run_job(runner, "video.extract.snapshot", [src])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "trip (Snapshot).png"
    assert fake.last()[fake.last().index("-ss") + 1] == "10.000"
    job = run_job(runner, "video.extract.frames", [src], {"every": 2})
    assert job.state == "done", job.error
    folder = outputs(job)[0]
    assert folder.is_dir() and sorted(p.name for p in folder.iterdir()) == [
        "frame 00-00-00.png", "frame 00-00-02.png", "frame 00-00-04.png"]
    job = run_job(runner, "video.extract.frames", [src], {"every": 0.04})
    assert job.state == "done"
    job = run_job(runner, "video.extract.contact_sheet", [src], {"grid": "3x3"})
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "trip (Contact Sheet).jpg"
    assert "tile=3x3" in fake.last()[fake.last().index("-vf") + 1]
    draw = [a for a, _ in fake.calls if "-vf" in a and "drawtext" in a[a.index("-vf") + 1]]
    assert len(draw) >= 9


def test_subtitles_extract_burn_embed(fake, runner, tmp_path):
    src = make_file(tmp_path, "movie.mkv", MKV_HEAD)
    fake.probes[str(src)] = vprobe(subs=(("subrip", "ita"), ("ass", "eng"), ("dvd_subtitle", "fre")))
    menu = registry.build_menu([str(src)], {}, "en")
    assert registry.find_node(menu, "video.subtitles.burn") is None
    job = run_job(runner, "video.extract.subtitles", [src])
    assert job.state == "done", job.error
    assert sorted(p.name for p in outputs(job)) == ["movie.eng.srt", "movie.ita.srt"]
    assert any("Image subtitles" in w for w in job.warnings)
    menu = registry.build_menu([str(src)], {}, "en")
    assert registry.find_node(menu, "video.subtitles.burn") is not None  # the extracted .ita.srt is a sidecar
    for extracted in outputs(job):
        extracted.unlink()
    (tmp_path / "movie.srt").write_bytes("1\n00:00:01,000 --> 00:00:02,000\nperché\n".encode("cp1252"))
    job = run_job(runner, "video.subtitles.burn", [src])
    assert job.state == "done", job.error
    args, cwd = next((a, c) for a, c in reversed(fake.calls) if "-vf" in a)
    assert "subtitles=filename=subs.srt" in args[args.index("-vf") + 1] and cwd
    assert fake.snapshots["subs.srt"].strip().endswith("perché")
    job = run_job(runner, "video.subtitles.embed", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert "1:0" in args and args[args.index("-c:s:2") + 1] == "copy"
    assert outputs(job)[0].name == "movie (Subtitled) 2.mkv"  # the burned copy took the first name


def test_split_and_metadata(fake, runner, tmp_path):
    src = make_file(tmp_path, "-dash.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe(dur=650, tags={"location": "+45.4+009.1/", "title": "x"}, data=1)
    job = run_job(runner, "video.split", [src], {"split_mode": "minutes", "minutes": 5})
    assert job.state == "done", job.error
    args, cwd = next((a, c) for a, c in reversed(fake.calls) if "segment" in a)
    assert args[args.index("-segment_times") + 1] == "300.000,600.000"
    assert args[-1] == "./-dash %03d.mp4"
    folder = outputs(job)[0]
    assert folder.name == "-dash (Clips)" and len(list(folder.iterdir())) == 3
    job = run_job(runner, "video.metadata.strip", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert "-map_metadata:s" in args and "-map_chapters" in args and "0:2" not in args
    job = run_job(runner, "video.metadata.gps", [src])
    assert job.state == "done", job.error
    assert "location=" in fake.last() and outputs(job)[0].name == "-dash (No GPS).mp4"
    clean = make_file(tmp_path, "clean.mp4", MP4_HEAD)
    fake.probes[str(clean)] = vprobe()
    job = run_job(runner, "video.metadata.gps", [clean])
    assert job.state == "done" and job.result["kind"] == "none"
    job = run_job(runner, "video.metadata.edit", [src],
                  {"metadata": {"set": {"ItemList:Title": "Vacanze", "Tag:artist": "Me"},
                                "remove": ["UserData:Comment"], "remove_all": False}})
    assert job.state == "done", job.error
    args = fake.last()
    assert "title=Vacanze" in args and "artist=Me" in args and "comment=" in args


def test_join_video_copy_and_reencode(fake, runner, tmp_path):
    a = make_file(tmp_path, "b part.mp4", MP4_HEAD)
    b = make_file(tmp_path, "a part.mp4", MP4_HEAD)
    fake.probes[str(a)] = vprobe(dur=10)
    fake.probes[str(b)] = vprobe(dur=5)
    job = run_job(runner, "video.join", [a, b])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "a part (Merged).mp4"
    assert "-c" in fake.last() and fake.last()[fake.last().index("-c") + 1] == "copy"
    assert fake.snapshots["list.ffconcat"].splitlines()[1:] == ["file 'in0000.mp4'", "file 'in0001.mp4'"]
    fake.probes[str(b)] = vprobe(dur=5, w=1280, h=720, audio=None)
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "video.join", [a, b])
    assert job.state == "done", job.error
    graph = fake.last()[fake.last().index("-filter_complex") + 1]
    # name order puts "a part" (1280×720) first: it is the canvas
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in graph and "concat=n=2:v=1:a=1" in graph
    assert "anullsrc=r=48000:cl=stereo" in fake.last()


# ===================================================================================== audio jobs

def test_audio_convert_and_when(fake, runner, tmp_path):
    src = make_file(tmp_path, "song.wav", WAV_HEAD)
    fake.probes[str(src)] = aprobe()
    job = run_job(runner, "audio.convert.mp3", [src])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "song.mp3" and "libmp3lame" in fake.last()
    job = run_job(runner, "audio.convert.wav", [src])
    assert job.state == "failed"  # never the source format
    job = run_job(runner, "audio.convert.m4r", [src], {"start": 5})
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-t") + 1] == "25.000" and args[args.index("-f") + 1] == "ipod"
    long_src = make_file(tmp_path, "long.flac", FLAC_HEAD)
    fake.probes[str(long_src)] = aprobe(codec="flac", dur=200, bitrate=None)
    job = run_job(runner, "audio.convert.m4r", [long_src])
    assert job.state == "done", job.error
    assert "afade=t=out" in fake.last()[fake.last().index("-af") + 1]
    assert any("40" in w for w in job.warnings)


def test_audio_compress_rules(fake, runner, tmp_path):
    wav = make_file(tmp_path, "rec.wav", WAV_HEAD)
    fake.probes[str(wav)] = aprobe()
    fake.out_size = 4
    job = run_job(runner, "audio.compress.high", [wav])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "rec (Compressed).flac"
    job = run_job(runner, "audio.compress.voice", [wav])
    assert job.state == "done", job.error
    assert "voip" in fake.last() and outputs(job)[0].suffix == ".opus"
    mp3 = make_file(tmp_path, "low.mp3", MP3_HEAD)
    fake.probes[str(mp3)] = aprobe(codec="mp3", bitrate=96000)
    job = run_job(runner, "audio.compress.balanced", [mp3])
    assert job.state == "done" and job.result["kind"] == "none"


def test_normalize_two_pass(fake, runner, tmp_path):
    src = make_file(tmp_path, "pod.mp3", MP3_HEAD)
    fake.probes[str(src)] = aprobe(codec="mp3", rate=44100, cover=True)
    fake.stderr = lambda args: LOUDNORM_STDERR if args[-1] == "-" else ""
    job = run_job(runner, "audio.normalize.podcast", [src])
    assert job.state == "done", job.error
    first = next(a for a in fake.ffmpeg_calls() if a[-1] == "-")
    assert first[first.index("-loglevel") + 1] == "info" and "print_format=json" in " ".join(first)
    second = fake.last()
    assert "measured_I=-27.61" in second[second.index("-af") + 1]
    assert second[second.index("-ar") + 1] == "44100" and "attached_pic" in second
    assert job.result["message"] == "-27.6 → -16 LUFS"
    assert outputs(job)[0].name == "pod (Normalized).mp3"
    fake.stderr = lambda args: LOUDNORM_STDERR.replace('"-27.61"', '"-inf"') if args[-1] == "-" else ""
    job = run_job(runner, "audio.normalize.streaming", [src])
    assert job.state == "failed" and "silent" in job.error["message"]
    fake.stderr = lambda args: LOUDNORM_STDERR.replace('"-27.61"', '"-14.2"').replace('"-4.47"', '"-2.0"')
    job = run_job(runner, "audio.normalize.streaming", [src])
    assert job.state == "done" and job.result["kind"] == "none"


def test_remove_silence(fake, runner, tmp_path):
    src = make_file(tmp_path, "memo.wav", WAV_HEAD)
    fake.probes[str(src)] = aprobe(dur=30)
    fake.stderr = lambda args: ("[silencedetect @ 0x1] silence_start: 0\n"
                                "[silencedetect @ 0x1] silence_end: 2 | silence_duration: 2\n"
                                "[silencedetect @ 0x1] silence_start: 27\n") if args[-1] == "-" else ""
    job = run_job(runner, "audio.edit.silence", [src])
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-ss") + 1] == "1.900" and args[args.index("-t") + 1] == "25.200"
    assert job.result["message"] == "Removed 4.8 s of silence"
    fake.stderr = lambda args: ""
    job = run_job(runner, "audio.edit.silence", [src])
    assert job.state == "done" and job.result["kind"] == "none"


def test_audio_edits(fake, runner, tmp_path):
    src = make_file(tmp_path, "voice.flac", FLAC_HEAD)
    fake.probes[str(src)] = aprobe(codec="flac", dur=60, channels=1, bitrate=None)
    job = run_job(runner, "audio.speed.1_5", [src])
    assert job.state == "done", job.error
    assert fake.last()[fake.last().index("-af") + 1] == "atempo=1.5"
    assert outputs(job)[0].name == "voice (Speed 1.5x).flac"
    job = run_job(runner, "audio.volume.up6", [src])
    assert "alimiter" in fake.last()[fake.last().index("-af") + 1]
    job = run_job(runner, "audio.channels.mono", [src])
    assert job.state == "failed" and "mono" in job.error["message"]
    job = run_job(runner, "audio.channels.stereo", [src])
    assert job.state == "done" and fake.last()[fake.last().index("-ac") + 1] == "2"
    job = run_job(runner, "audio.edit.denoise", [src], {"strength": "strong"})
    assert "afftdn=nr=20" in fake.last()[fake.last().index("-af") + 1]
    job = run_job(runner, "audio.edit.fade", [src])
    assert "afade=t=out:st=58.000" in fake.last()[fake.last().index("-af") + 1]
    job = run_job(runner, "audio.trim", [src], {"start": 10, "end": 20, "mode": "cut"})
    assert job.state == "done", job.error
    assert "atrim=start=20.000" in fake.last()[fake.last().index("-filter_complex") + 1]
    job = run_job(runner, "audio.edit.reverse", [src])
    assert job.state == "done", job.error
    assert "areverse" in " ".join(next(a for a, _ in fake.calls if a[-1].startswith("seg")))


def test_transcribe_and_missing_model(fake, runner, tmp_path, dropwheel_env):
    src = make_file(tmp_path, "intervista.mp3", MP3_HEAD)
    fake.probes[str(src)] = aprobe(codec="mp3")
    job = run_job(runner, "audio.transcribe.txt", [src])
    assert job.state == "failed"
    assert "ggml-base.bin" in job.error["message"] and "curl" in job.error["install"]
    model = dropwheel_env.data / "dropwheel" / "whisper" / "ggml-base.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"ggml")
    job = run_job(runner, "audio.transcribe.txt", [src], {"language": "it"})
    assert job.state == "done", job.error
    out = outputs(job)[0]
    assert out.name == "intervista.txt" and out.read_text(encoding="utf-8") == "Ciao mondo.\nSeconda riga.\n"
    whisper = next(a for a, _ in fake.calls if a[0].endswith("whisper-cli"))
    assert whisper[whisper.index("-l") + 1] == "it" and "-otxt" in whisper
    prep = next(a for a in fake.ffmpeg_calls() if "16000" in a)
    assert prep[prep.index("-ac") + 1] == "1"
    video = make_file(tmp_path, "lesson.mp4", MP4_HEAD)
    fake.probes[str(video)] = vprobe()
    job = run_job(runner, "video.subtitles.generate", [video])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "lesson.srt"


def test_join_audio_and_playlist(fake, runner, tmp_path):
    one = make_file(tmp_path, "02 two.mp3", MP3_HEAD)
    two = make_file(tmp_path, "10 ten.wav", WAV_HEAD)
    fake.probes[str(one)] = aprobe(codec="mp3", dur=100)
    fake.probes[str(two)] = aprobe(codec="pcm_s16le", dur=50)
    job = run_job(runner, "audio.join", [two, one])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "02 two (Merged).mp3"
    graph = fake.last()[fake.last().index("-filter_complex") + 1]
    assert graph.endswith("concat=n=2:v=0:a=1[a]")
    inputs = [fake.last()[i + 1] for i, a in enumerate(fake.last()) if a == "-i"]
    assert inputs == [f"file:{one}", f"file:{two}"]
    job = run_job(runner, "audio.playlist", [two, one])
    assert job.state == "done", job.error
    out = outputs(job)[0]
    assert out.suffix == ".m3u8"
    assert out.read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U", "#EXTINF:100,02 two", "02 two.mp3", "#EXTINF:50,10 ten", "10 ten.wav"]


def test_cover_video_and_mixed_mp3(fake, runner, tmp_path):
    pil = pytest.importorskip("PIL.Image")
    img = tmp_path / "cover.png"
    pil.new("RGBA", (1001, 701), (255, 0, 0, 128)).save(img)
    song = make_file(tmp_path, "song.mp3", MP3_HEAD)
    fake.probes[str(song)] = aprobe(codec="mp3", dur=180)
    job = run_job(runner, "media.cover_video", [img, song])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "song.mp4"
    args = fake.last()
    assert "-loop" in args and "stillimage" in args and args[args.index("-t") + 1] == "180.000"
    assert "scale=1000:700" in args[args.index("-vf") + 1]
    clip = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(clip)] = vprobe()
    flac = make_file(tmp_path, "track.flac", FLAC_HEAD)
    fake.probes[str(flac)] = aprobe(codec="flac", bitrate=None)
    job = run_job(runner, "media.to_mp3", [clip, flac, song])
    assert job.state == "done", job.error
    assert sorted(p.name for p in outputs(job)) == ["clip.mp3", "track.mp3"]
    assert any("skipped" in w for w in job.warnings)


def test_tags_ffmpeg_fallback(fake, runner, tmp_path, monkeypatch):
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: None if name == "py:mutagen" else real_path(name))
    src = make_file(tmp_path, "brano.mp3", MP3_HEAD)
    fake.probes[str(src)] = aprobe(codec="mp3", cover=True)
    meta = {"set": {"Tag:title": "Titolo", "year": "2024", "track": "3/12"}, "remove": ["Tag:genre"],
            "remove_all": False}
    job = run_job(runner, "audio.tags", [src], {"metadata": meta})
    assert job.state == "done", job.error
    args = fake.last()
    assert "title=Titolo" in args and "date=2024" in args and "track=3/12" in args and "genre=" in args
    assert "-id3v2_version" in args and "0:1" in args  # existing cover kept
    assert [p.name for p in outputs(job)] == ["brano (Edited).mp3"]  # a copy by default
    assert src.read_bytes() == MP3_HEAD  # the original is untouched
    job = run_job(runner, "audio.tags", [src], {"metadata": meta, "replace": True})
    assert job.state == "done", job.error
    assert outputs(job) == [src]  # replace chosen: swapped in place, original backed up for undo
    assert job.result["undoable"]
    job = run_job(runner, "audio.tags", [src], {"metadata": {}})
    assert job.state == "done" and job.result["kind"] == "none"


def test_tags_ffmpeg_fallback_on_ogg(fake, runner, tmp_path, monkeypatch):
    """The Ogg muxer prefers stream tags: they go through the file level, where -metadata can change them."""
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: None if name == "py:mutagen" else real_path(name))
    src = make_file(tmp_path, "song.opus", b"OggS" + bytes(range(256)) * 4)
    fake.probes[str(src)] = oggprobe()
    job = run_job(runner, "audio.tags", [src], {"metadata": {"set": {"title": "Nuovo"}, "remove": ["artist"]}})
    assert job.state == "done", job.error
    args = fake.last()
    assert metadata_maps(args) == ["0", "0:s:0"]
    assert "title=Nuovo" in args and "artist=" in args
    assert args.index("-map_metadata") < args.index("-metadata")
    # remove all: nothing is copied, not even the stream tags
    job = run_job(runner, "audio.tags", [src], {"metadata": {"remove_all": True}})
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["-1"]
    # a cover cannot be stream-copied into Ogg: dropped with a warning instead of a muxer error
    fake.probes[str(src)] = oggprobe(cover=True)
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "audio.tags", [src], {"metadata": {"set": {"title": "Nuovo"}}})
    assert job.state == "done", job.error
    assert "0:1" not in fake.last() and any("cover" in w for w in job.warnings)
    # other containers keep their file-level tags as they are
    mp3 = make_file(tmp_path, "song.mp3", MP3_HEAD)
    fake.probes[str(mp3)] = aprobe(codec="mp3", cover=True)
    job = run_job(runner, "audio.tags", [mp3], {"metadata": {"set": {"title": "Nuovo"}}})
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["0"] and "0:1" in fake.last() and not job.warnings


def test_aiff_tags_use_the_id3_chunk(fake, runner, tmp_path, monkeypatch):
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: None if name == "py:mutagen" else real_path(name))
    assert "-write_id3v2" in mu.audio_target("aiff").args
    src = make_file(tmp_path, "song.aiff", b"FORM" + bytes(range(256)) * 4)
    fake.probes[str(src)] = aprobe(codec="pcm_s16be")
    job = run_job(runner, "audio.tags", [src], {"metadata": {"set": {"artist": "Lei"}}})
    assert job.state == "done", job.error
    args = fake.last()
    assert args[args.index("-write_id3v2") + 1] == "1" and "artist=Lei" in args


def test_video_metadata_edit_on_ogv(fake, runner, tmp_path):
    src = make_file(tmp_path, "clip.ogv", b"OggS" + bytes(range(256)) * 4)
    probe = vprobe(codec="theora", audio="vorbis")
    probe["format"]["format_name"] = "ogg"
    fake.probes[str(src)] = probe
    job = run_job(runner, "video.metadata.edit", [src], {"metadata": {"set": {"title": "Nuovo"}}})
    assert job.state == "done", job.error
    assert metadata_maps(fake.last()) == ["0", "0:s:1"] and "title=Nuovo" in fake.last()


def test_tags_with_mutagen(fake, runner, tmp_path):
    pytest.importorskip("mutagen")
    from mutagen.flac import FLAC

    src = tmp_path / "tune.flac"
    streaminfo = (b"\x10\x00\x10\x00" + b"\x00\x00\x00" + b"\x00\x00\x00"
                  + (44100 << 44 | 1 << 41 | 15 << 36 | 44100).to_bytes(8, "big") + b"\x00" * 16)
    src.write_bytes(b"fLaC" + b"\x80\x00\x00\x22" + streaminfo)
    job = run_job(runner, "audio.tags", [src], {"metadata": {"set": {"title": "Ciao", "artist": "Io"}},
                                                "replace": False})
    assert job.state == "done", job.error
    out = outputs(job)[0]
    assert out.name == "tune (Edited).flac"
    tags = FLAC(str(out))
    assert tags["title"] == ["Ciao"] and tags["artist"] == ["Io"]


def test_extract_cover_ffmpeg(fake, runner, tmp_path, monkeypatch):
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: None if name == "py:mutagen" else real_path(name))
    src = make_file(tmp_path, "album.mp3", MP3_HEAD)
    fake.probes[str(src)] = aprobe(codec="mp3", cover=True)
    job = run_job(runner, "audio.extract.cover", [src])
    assert job.state == "done", job.error
    assert outputs(job)[0].name == "album (Cover).jpg"
    fake.probes[str(src)] = aprobe(codec="mp3")
    jobs._PROBE_CACHE.clear()
    job = run_job(runner, "audio.extract.cover", [src])
    assert job.state == "failed" and "cover" in job.error["message"]


def test_visualize(fake, runner, tmp_path):
    src = make_file(tmp_path, "beat.wav", WAV_HEAD)
    fake.probes[str(src)] = aprobe(dur=20)
    job = run_job(runner, "audio.visualize.portrait", [src], {"color": "#00ff00"})
    assert job.state == "done", job.error
    graph = fake.last()[fake.last().index("-filter_complex") + 1]
    assert "showwaves=s=1080x480" in graph and "colors=0x00ff00" in graph and "pad=1080:1920" in graph
    assert outputs(job)[0].name == "beat (Waveform).mp4"


def test_unreadable_media(fake, runner, tmp_path):
    src = make_file(tmp_path, "broken.mp4", MP4_HEAD)
    job = run_job(runner, "video.convert.mkv", [src])
    assert job.state == "failed" and "Cannot read broken.mp4" in job.error["message"]


def test_batch_each_continues_on_failure(fake, runner, tmp_path):
    good = make_file(tmp_path, "good.wav", WAV_HEAD)
    bad = make_file(tmp_path, "bad.wav", WAV_HEAD)
    fake.probes[str(good)] = aprobe()
    job = run_job(runner, "audio.convert.mp3", [good, bad])
    assert job.state == "done"
    assert [p.name for p in outputs(job)] == ["good.mp3"]
    assert "1 failed" in job.result["message"]


def test_missing_filters_degrade_gracefully(fake, runner, tmp_path, media_mod):
    fake.filters = FILTERS_ALL.replace(" ... zscale ", " ... scale  ")
    src = make_file(tmp_path, "hdr.mkv", MKV_HEAD)
    fake.probes[str(src)] = vprobe(codec="hevc", trc="arib-std-b67", pix="yuv420p10le")
    job = run_job(runner, "video.convert.mp4", [src])
    assert job.state == "done", job.error
    assert "zscale" not in fake.last()[fake.last().index("-vf") + 1]
    assert any("zscale missing" in w for w in job.warnings)
    fake.filters = FILTERS_ALL.replace("vidstab", "nostab").replace(" ... subtitles ", " ... nosubs    ")
    with media_mod._CAPS_LOCK:
        media_mod._CAPS.clear()  # the fake's filter list changed
    job = run_job(runner, "video.edit.stabilize", [src])
    assert job.state == "done", job.error
    assert "deshake" in fake.last()[fake.last().index("-vf") + 1]
    (tmp_path / "hdr.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    job = run_job(runner, "video.subtitles.burn", [src])
    assert job.state == "failed" and "subtitles" in job.error["message"]


_SMOKE_PARAMS: dict[str, dict] = {
    "video.crop": {"rect": {"x": 0, "y": 0, "w": 640, "h": 360}},
    "video.trim": {"start": 1, "end": 4},
    "video.metadata.edit": {"metadata": {"set": {"title": "x"}}},
    "audio.trim": {"start": 1, "end": 4},
    "audio.tags": {"metadata": {"set": {"title": "x"}}},
    "audio.volume.custom": {"db": -4},
}


def test_every_media_action_runs_without_bugs(fake, runner, tmp_path, dropwheel_env):
    model = dropwheel_env.data / "dropwheel" / "whisper" / "ggml-base.bin"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"ggml")
    fake.stderr = lambda args: LOUDNORM_STDERR if args[-1] == "-" else ""
    fake.out_size = 8
    video = make_file(tmp_path, "film.mkv", MKV_HEAD)
    fake.probes[str(video)] = vprobe(subs=(("subrip", "ita"),), tags={"location": "+1+2/"})
    video2 = make_file(tmp_path, "film 2.mkv", MKV_HEAD)
    fake.probes[str(video2)] = vprobe(w=1280, h=720)
    (tmp_path / "film.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nciao\n", encoding="utf-8")
    sub = tmp_path / "other.ass"
    sub.write_text("[Script Info]\n", encoding="utf-8")
    audio = make_file(tmp_path, "brano.wav", WAV_HEAD)
    fake.probes[str(audio)] = aprobe(dur=30)
    audio2 = make_file(tmp_path, "brano 2.mp3", MP3_HEAD)
    fake.probes[str(audio2)] = aprobe(codec="mp3", dur=20, cover=True)
    ran = 0
    states: dict[str, int] = {}
    for act in registry.all_actions():
        if not act.module.endswith(".media") or act.id in ("media.cover_video",):
            continue
        params = dict(_SMOKE_PARAMS.get(act.id, {}))
        if act.id in ("video.subtitles.burn_file", "video.subtitles.embed"):
            params["subtitle"] = str(sub)
        cats = act.categories
        if act.id == "media.to_mp3":
            files = [video, audio]
        elif "video" in cats:
            files = [video, video2] if act.multi == "combine" else [video]
        else:
            files = [audio, audio2] if act.multi == "combine" else [audio]
        job = run_job(runner, act.id, files, params)
        ran += 1
        states[job.state] = states.get(job.state, 0) + 1
        if job.state == "failed":
            assert job.error["message"] != "Unexpected error", f"{act.id}: {job.error['details']}"
        else:
            assert job.state == "done", act.id
    assert ran >= 90
    # expected refusals only: source format offered (wav→wav), already-stereo, upscales, no GPS…
    assert states.get("done", 0) >= 80, states


# ====================================================================================== encoders

def test_hw_fallback_to_software(fake, make_runner, media_mod, tmp_path, monkeypatch):
    cfg = cfgmod.load()
    cfg["video"]["hwaccel"] = "vaapi"
    r, _events = make_runner(config=cfg, lang="en")
    monkeypatch.setattr(media_mod, "_hw_probe", lambda ctx, mode, codec: True)
    fake.fail = lambda args: "h264_vaapi" in args
    src = make_file(tmp_path, "clip.mp4", MP4_HEAD)
    fake.probes[str(src)] = vprobe()
    job = run_job(r, "video.resize.720", [src])
    assert job.state == "done", job.error
    encodes = [a for a in fake.ffmpeg_calls() if "-vf" in a]
    assert "h264_vaapi" in encodes[0] and "hwupload" in encodes[0][encodes[0].index("-vf") + 1]
    assert "-init_hw_device" in encodes[0]
    assert "libx264" in encodes[-1] and "-init_hw_device" not in encodes[-1]
    assert any("Hardware encoding failed" in w for w in job.warnings)


def test_venc_args(media_mod):
    vaapi = media_mod.VEnc(codec="hevc", crf=28, hw="vaapi")
    assert vaapi.args() == ["-c:v", "hevc_vaapi", "-qp", "28", "-tag:v", "hvc1"]
    assert vaapi.chain(["scale=1280:720"]) == "scale=1280:720,format=nv12,hwupload"
    assert media_mod.VEnc(codec="h264", hw="nvenc", crf=23).args()[:2] == ["-c:v", "h264_nvenc"]
    sw = media_mod.VEnc(codec="h264", crf=20, bitrate=900)
    args = sw.args()
    assert "-crf" not in args and args[-2:] == ["-b:v", "900k"]
    vp9 = media_mod.VEnc(codec="vp9", crf=31, bitrate=500).args()
    assert vp9.count("-b:v") == 1 and vp9[-1] == "500k"
    assert media_mod.VEnc(codec="h264").chain([]) is None
    assert media_mod.VEnc(codec="h264").vf([]) == []


@pytest.mark.parametrize("kind", ["WAVE", "AIFF", "DSF", "DSDIFF"])
def test_tags_on_formats_without_easy_keys_go_through_ffmpeg(fake, runner, tmp_path, monkeypatch, media_mod, kind):
    """A remove-only edit used to be a silent no-op: "del audio[key]" finds nothing in a raw ID3 object."""
    import sys
    import types

    deleted: list[str] = []

    class Raw(dict):
        tags = {}

        def __delitem__(self, key):
            deleted.append(key)
            raise KeyError(key)

        def save(self, *a, **k):
            pass

    Raw.__name__ = kind
    monkeypatch.setitem(sys.modules, "mutagen", types.SimpleNamespace(File=lambda *a, **k: Raw()))
    with pytest.raises(ValueError):
        media_mod._mutagen_tags(tmp_path / "x", {}, ["artist"], False)
    assert not deleted
    real_path = tools.path
    monkeypatch.setattr(tools, "path", lambda name: "/fake/mutagen" if name == "py:mutagen" else real_path(name))
    src = make_file(tmp_path, "brano.wav", b"RIFF\x24\0\0\0WAVEfmt ")
    fake.probes[str(src)] = aprobe(codec="pcm_s16le")
    job = run_job(runner, "audio.tags", [src], {"metadata": {"set": {}, "remove": ["Tag:artist"], "remove_all": False}})
    assert job.state == "done", job.error
    assert "artist=" in fake.last()
