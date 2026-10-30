"""Protocol tests for ``python -m dropwheel daemon`` (SPEC §5), run as a real subprocess.

A small action plugin is loaded through ``$DROPWHEEL_EXTRA_ACTIONS`` so the tests do not depend
on which action modules or external tools are installed.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

PLUGIN = '''
import shutil
import time

from dropwheel.errors import UserError
from dropwheel.registry import P, action


@action(id="test.copy", label="Test copy", icon="content_copy", categories={"image"}, multi="each")
def _test_copy(ctx):
    out = ctx.output_path(ctx.file, suffix="sfx.copy")
    with ctx.atomic(out) as tmp:
        shutil.copyfile(ctx.file.path, tmp)
    return ctx.result([out])


@action(id="test.slow", label="Test slow", icon="hourglass_empty", categories={"*"}, multi="any",
        undoable=False, params=[P.int("steps", default=400)])
def _test_slow(ctx):
    steps = ctx.pint("steps", 400)
    for i in range(steps):
        ctx.check_cancel()
        ctx.progress(i / steps, f"step {i}")
        time.sleep(0.025)
    return ctx.result(kind="text", text="finished")


@action(id="test.fail", label="Test fail", icon="error", categories={"*"}, multi="any", undoable=False)
def _test_fail(ctx):
    raise UserError("err.not_supported", details="detail text")
'''


def write_plugin(directory: Path) -> Path:
    """Write the test action plugin and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "dw_test_actions.py"
    path.write_text(PLUGIN, encoding="utf-8")
    return path


def make_png(path: Path, size: tuple[int, int] = (640, 480)) -> Path:
    from PIL import Image

    im = Image.new("RGB", size, (30, 120, 200))
    for x in range(0, size[0], 7):
        for y in range(0, size[1], 11):
            im.putpixel((x, y), (250, 250, 0))
    im.save(path)
    return path


class DaemonProc:
    """A running daemon with a background reader (responses by id, events in a list)."""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen([sys.executable, "-m", "dropwheel", "daemon"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(ROOT))
        self.lines: queue.Queue = queue.Queue()
        self.responses: dict = {}
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.next_id = 1
        self.stderr_chunks: list[bytes] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8")
            msg = json.loads(line)  # every line must be valid JSON
            with self.cond:
                if "id" in msg:
                    self.responses[msg["id"]] = msg
                else:
                    self.events.append(msg)
                self.cond.notify_all()

    def _read_err(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr_chunks.append(raw)

    @property
    def stderr(self) -> str:
        return b"".join(self.stderr_chunks).decode("utf-8", "replace")

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        rid = self.next_id
        self.next_id += 1
        self.send_raw((json.dumps({"id": rid, "method": method, "params": params or {}}) + "\n").encode("utf-8"))
        deadline = time.monotonic() + timeout
        with self.cond:
            while rid not in self.responses:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no response to {method} within {timeout}s; stderr:\n{self.stderr}")
                self.cond.wait(left)
            return self.responses.pop(rid)

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0):
        msg = self.request(method, params, timeout)
        assert "error" not in msg, msg
        return msg["result"]

    def wait_event(self, pred, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for ev in self.events:
                    if pred(ev):
                        return ev
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"event not received; events={self.events!r}\nstderr:\n{self.stderr}")
                self.cond.wait(left)

    def job_event(self, job: str, states=("done", "failed", "cancelled"), timeout: float = 60.0) -> dict:
        return self.wait_event(lambda e: e.get("event") == "job" and e.get("job") == job and e.get("state") in states,
                               timeout)

    def close(self, timeout: float = 15.0) -> int:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
                raise
        return self.proc.returncode


def daemon_env(base: Path) -> dict[str, str]:
    env = os.environ.copy()
    for var in ("LC_ALL", "LC_MESSAGES", "DROPWHEEL_CONFIG", "DROPWHEEL_DEBUG"):
        env.pop(var, None)
    dirs = {}
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                     ("XDG_DATA_HOME", "data")):
        d = base / sub
        d.mkdir(parents=True, exist_ok=True)
        env[var] = str(d)
        dirs[sub] = d
    (base / "volatile").mkdir(exist_ok=True)
    (base / "Downloads").mkdir(exist_ok=True)
    env.update({
        "PYTHONPATH": os.pathsep.join(p for p in (str(SRC), os.environ.get("PYTHONPATH", "")) if p),
        "DROPWHEEL_EXTRA_ACTIONS": str(write_plugin(base / "plugins")),
        "DROPWHEEL_NO_NOTIFY": "1",
        "DROPWHEEL_VOLATILE_DIRS": str(base / "volatile"),
        "XDG_DOWNLOAD_DIR": str(base / "Downloads"),
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    pytest.importorskip("PIL")
    base = tmp_path_factory.mktemp("daemon")
    work = base / "work"
    work.mkdir()
    d = DaemonProc(daemon_env(base))
    d.base = base  # type: ignore[attr-defined]
    d.work = work  # type: ignore[attr-defined]
    d.png = make_png(work / "photo one.png")  # type: ignore[attr-defined]
    try:
        yield d
    finally:
        d.close()


# ======================================================================================= tests

def test_ready_event_first(daemon):
    ev = daemon.wait_event(lambda e: e.get("event") == "ready", timeout=60)
    assert ev["version"] == 1
    assert daemon.events[0]["event"] == "ready"


def test_hello(daemon):
    res = daemon.call("hello")
    assert res["lang"] == "en"
    assert isinstance(res["version"], str) and res["version"]
    strings = res["strings"]
    assert strings["grp.convert"] == "Convert"
    assert "cli.help.start" in strings and "daemon.err.unknown_method" in strings
    assert isinstance(res["config"], dict) and "wheel" in res["config"]
    assert not any(str(k).startswith("_") for k in res["config"])
    assert isinstance(res["warnings"], list)


def test_ping_inline(daemon):
    assert daemon.call("ping")["pong"] is True


def test_menu_for_png(daemon):
    menu = daemon.call("menu", {"files": [str(daemon.png)], "context": "drag"})
    assert menu["category"] == "image"
    assert menu["count"] == 1
    assert menu["files"][0]["category"] == "image"
    assert menu["files"][0]["width"] == 640
    assert [p["id"] for p in menu["pages"]] == ["main", "file"]
    assert menu["thumb"] and os.path.isfile(menu["thumb"])
    ids = json.dumps(menu)
    assert "test.copy" in ids


def test_menu_accepts_file_uri(daemon):
    from dropwheel import util

    menu = daemon.call("menu", {"files": [util.path_to_uri(daemon.png)], "context": "click"})
    assert menu["category"] == "image"
    assert menu["files"][0]["path"] == str(daemon.png)


def test_run_copy_action(daemon):
    res = daemon.call("run", {"action": "test.copy", "files": [str(daemon.png)], "params": {}})
    job = res["job"]
    ev = daemon.job_event(job)
    assert ev["state"] == "done", ev
    outputs = ev["result"]["outputs"]
    assert len(outputs) == 1
    out = Path(outputs[0]["path"])
    assert out.is_file() and out.parent == daemon.png.parent
    assert out.name == "photo one (Copy).png"
    assert outputs[0]["size"] == daemon.png.stat().st_size
    # queued/running events came before the final one
    states = [e["state"] for e in daemon.events if e.get("event") == "job" and e.get("job") == job]
    assert states[0] == "queued" and states[-1] == "done"
    assert ev["result"]["undoable"] is True


def test_run_failure_reports_error(daemon):
    job = daemon.call("run", {"action": "test.fail", "files": [str(daemon.png)]})["job"]
    ev = daemon.job_event(job)
    assert ev["state"] == "failed"
    assert ev["error"]["message"] == "Unsupported format"
    assert ev["error"]["details"] == "detail text"


def test_run_unknown_action_is_an_error(daemon):
    msg = daemon.request("run", {"action": "no.such.action", "files": [str(daemon.png)]})
    assert "error" in msg
    assert "no.such.action" in msg["error"]["message"]


def test_cancel_running_job(daemon):
    job = daemon.call("run", {"action": "test.slow", "files": [str(daemon.png)], "params": {"steps": 2000}})["job"]
    daemon.wait_event(lambda e: e.get("job") == job and e.get("state") == "running", timeout=30)
    assert daemon.call("cancel", {"job": job}) == {"ok": True}
    ev = daemon.job_event(job, timeout=30)
    assert ev["state"] == "cancelled"
    assert daemon.call("cancel", {"job": job}) == {"ok": False}
    assert daemon.call("cancel", {"job": "nope"}) == {"ok": False}


def test_progress_events_are_throttled(daemon):
    job = daemon.call("run", {"action": "test.slow", "files": [str(daemon.png)], "params": {"steps": 40}})["job"]
    daemon.job_event(job, timeout=60)
    running = [e for e in daemon.events if e.get("job") == job and e.get("state") == "running"]
    assert running, "no progress events"
    # 40 steps of 25 ms = 1 s of work: at most one event per 80 ms (plus the first one)
    assert len(running) <= 1 + 1.0 / 0.08 + 3


def test_undo(daemon):
    job = daemon.call("run", {"action": "test.copy", "files": [str(daemon.png)]})["job"]
    ev = daemon.job_event(job)
    out = Path(ev["result"]["outputs"][0]["path"])
    assert out.is_file()
    res = daemon.call("undo", {"job": job})
    assert res["ok"] is True, res
    assert not out.exists()
    again = daemon.call("undo", {"job": job})
    assert again["ok"] is False


def test_preview_thumb_and_image(daemon):
    th = daemon.call("preview", {"kind": "thumb", "path": str(daemon.png), "size": 128})
    assert os.path.isfile(th["png"])
    assert max(th["width"], th["height"]) == 128
    im = daemon.call("preview", {"kind": "image", "path": str(daemon.png), "max": 320})
    assert (im["width"], im["height"]) == (640, 480)
    assert im["scale"] == pytest.approx(0.5)
    assert os.path.isfile(im["png"])


def test_preview_text(daemon):
    txt = daemon.work / "note.txt"
    txt.write_text("ciao è così\nriga 2\n", encoding="utf-8")
    res = daemon.call("preview", {"kind": "text", "path": str(txt)})
    assert res["text"].startswith("ciao è così")
    assert res["encoding"] == "utf-8" and res["truncated"] is False


def test_preview_errors(daemon):
    msg = daemon.request("preview", {"kind": "thumb", "path": str(daemon.work / "missing.png")})
    assert "missing.png" in msg["error"]["message"]
    msg = daemon.request("preview", {"kind": "bogus", "path": str(daemon.png)})
    assert "bogus" in msg["error"]["message"]


def test_preview_pdf(daemon):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = daemon.work / "doc.pdf"
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Hello Dropwheel page {i + 1}", fontsize=14)
    doc.save(str(pdf))
    doc.close()
    info = daemon.call("preview", {"kind": "pdf_info", "path": str(pdf)})
    assert info["pages"] == 2 and info["needs_password"] is False
    assert info["sizes"][0] == [595.0, 842.0]
    page = daemon.call("preview", {"kind": "pdf_page", "path": str(pdf), "page": 1, "dpi": 72})
    assert os.path.isfile(page["png"])
    assert (page["width_pt"], page["height_pt"]) == (595.0, 842.0)
    found = daemon.call("preview", {"kind": "pdf_search", "path": str(pdf), "query": "dropwheel"})
    assert [m["page"] for m in found["matches"]] == [0, 1]


def test_unknown_method_and_bad_input(daemon):
    msg = daemon.request("frobnicate", {})
    assert msg["error"]["message"] == "Unknown method: frobnicate"
    # invalid JSON and non-object lines are ignored; the daemon keeps serving
    daemon.send_raw(b"this is not json\n[1, 2]\n\n")
    msg = daemon.request("hello", {"x": 1})
    assert "result" in msg
    # params must be an object
    daemon.send_raw(b'{"id": "p1", "method": "menu", "params": [1]}\n')
    deadline = time.monotonic() + 20
    with daemon.cond:
        while "p1" not in daemon.responses and time.monotonic() < deadline:
            daemon.cond.wait(0.5)
        bad = daemon.responses.pop("p1")
    assert "error" in bad


def test_non_ascii_and_invalid_utf8_lines(daemon):
    weird = daemon.work / "fïle ☃ — name.png"
    make_png(weird, (32, 32))
    menu = daemon.call("menu", {"files": [str(weird)]})
    assert menu["files"][0]["name"] == weird.name
    daemon.send_raw(b'{"id": 999, "method": "ping", "params": {}, "junk": "\xff\xfe"}\n')
    deadline = time.monotonic() + 20
    with daemon.cond:
        while 999 not in daemon.responses and time.monotonic() < deadline:
            daemon.cond.wait(0.5)
        assert daemon.responses.pop(999)["result"]["pong"] is True


def test_util_and_misc_methods(daemon):
    msg = daemon.request("util", {"op": "explode"})
    assert "explode" in msg["error"]["message"]
    saved = daemon.call("util", {"op": "save_text", "text": "hello", "paths": [str(daemon.png)]})
    assert saved["ok"] is True
    p = Path(saved["path"])
    assert p.read_text(encoding="utf-8") == "hello" and p.parent == daemon.png.parent
    assert isinstance(daemon.call("clipboard_files")["files"], list)
    apps = daemon.call("apps", {"files": [str(daemon.png)]})
    assert isinstance(apps, list)
    cfg = daemon.call("config")
    assert isinstance(cfg, dict) and "hud" in cfg
    report = daemon.call("doctor", timeout=120)
    assert isinstance(report["tools"], list) and "required_missing" in report
    assert daemon.call("share_stop", {"id": "deadbeef"}) == {"ok": False}


def test_config_changed_event(daemon):
    cfg_dir = daemon.base / "config" / "dropwheel"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text('language = "it"\n[hud]\ntimeout_ms = 9000\n', encoding="utf-8")
    # the explicit "config" RPC of an earlier test changed nothing, so it emitted no event
    ev = daemon.wait_event(lambda e: e.get("event") == "config_changed", timeout=20)
    assert ev["lang"] == "it" and ev["lang_changed"] is True
    cfg_ev = daemon.wait_event(lambda e: e.get("event") == "config", timeout=5)
    assert cfg_ev["config"]["hud"]["timeout_ms"] == 9000
    assert ev["config"]["hud"]["timeout_ms"] == 9000
    assert daemon.call("hello")["strings"]["grp.convert"] == "Converti"


def test_eof_exits_cleanly(tmp_path):
    pytest.importorskip("PIL")
    d = DaemonProc(daemon_env(tmp_path))
    d.wait_event(lambda e: e.get("event") == "ready", timeout=60)
    assert d.close(timeout=20) == 0


# ================================================================================== supervisor

class _Exited(Exception):
    pass


@pytest.fixture
def fork_stubs(monkeypatch):
    """What ``_supervise`` calls, recorded: the fork and wait results are set by each test."""
    from dropwheel import daemon as dm

    state = types.SimpleNamespace(fork=lambda: 4242, status=0, masks=[], handlers=[], killed=[], order=[])

    def sigmask(how, signals):
        state.masks.append(how)
        state.order.append("mask")
        return {"old-mask"}

    def fake_exit(code):
        raise _Exited(code)

    monkeypatch.setattr(os, "fork", lambda: state.fork(), raising=False)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (pid, state.status), raising=False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: state.killed.append((pid, sig)))
    monkeypatch.setattr(os, "_exit", fake_exit)
    monkeypatch.setattr(dm.signal, "pthread_sigmask", sigmask, raising=False)
    monkeypatch.setattr(dm.signal, "SIG_BLOCK", "block", raising=False)
    monkeypatch.setattr(dm.signal, "SIG_SETMASK", "setmask", raising=False)
    monkeypatch.setattr(dm, "_set_signal_handlers", lambda h: (state.handlers.append(h), state.order.append("handlers")))
    monkeypatch.setattr(dm, "_exit_code", lambda status: status)
    return state


def test_supervisor_child_runs_the_daemon(fork_stubs):
    from dropwheel import daemon as dm

    fork_stubs.fork = lambda: 0
    dm._supervise()  # returns: the caller goes on to serve
    assert fork_stubs.masks == ["block", "setmask"] and fork_stubs.handlers == []


def test_supervisor_parent_forwards_signals_and_reports_the_status(fork_stubs):
    from dropwheel import daemon as dm

    fork_stubs.status = 7
    with pytest.raises(_Exited) as exc:
        dm._supervise()
    assert exc.value.args == (7,)
    # the handlers are in place before pending signals are let through
    assert fork_stubs.order == ["mask", "handlers", "mask"]
    fork_stubs.handlers[0](15, None)
    assert fork_stubs.killed == [(4242, 15)]


def test_supervisor_without_fork(fork_stubs, monkeypatch):
    from dropwheel import daemon as dm

    def no_more_processes():
        raise OSError("EAGAIN")

    fork_stubs.fork = no_more_processes
    dm._supervise()  # runs in-process, with the signal mask restored
    assert fork_stubs.masks == ["block", "setmask"] and fork_stubs.handlers == []
    monkeypatch.delattr(os, "fork")
    fork_stubs.masks.clear()
    dm._supervise()
    assert fork_stubs.masks == []


def test_shutdown_signal_passes_through_except_exception():
    from dropwheel import daemon as dm

    assert not issubclass(dm._Shutdown, Exception)


SLOW_FILE_PLUGIN = '''
import time

from dropwheel.registry import action


@action(id="test.slowfile", label="Test slow file", icon="hourglass_empty", categories={"*"}, multi="any",
        undoable=False)
def _test_slow_file(ctx):
    out = ctx.output_path(ctx.file, suffix="sfx.copy")
    with ctx.atomic(out) as tmp:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("partial")
        ctx.progress(0.1, "writing")
        for _ in range(4000):
            ctx.check_cancel()
            time.sleep(0.025)
    return ctx.result([out])
'''


def _supervised_daemon(base: Path) -> tuple[DaemonProc, str, Path]:
    """A daemon running a job that keeps a partial output file in the work folder."""
    pytest.importorskip("PIL")
    env = daemon_env(base)
    extra = base / "plugins" / "dw_slow_file.py"
    extra.write_text(SLOW_FILE_PLUGIN, encoding="utf-8")
    env["DROPWHEEL_EXTRA_ACTIONS"] = str(extra)
    work = base / "work"
    work.mkdir()
    png = make_png(work / "photo.png", (32, 32))
    d = DaemonProc(env)
    d.wait_event(lambda e: e.get("event") == "ready", timeout=60)
    job = d.call("run", {"action": "test.slowfile", "files": [str(png)]})["job"]
    d.wait_event(lambda e: e.get("job") == job and e.get("state") == "running" and e.get("progress"), timeout=30)
    assert len(list(work.iterdir())) == 2  # the source and the partial output
    return d, job, work


def _assert_cleaned_up(d: DaemonProc, job: str, work: Path) -> None:
    assert d.job_event(job, timeout=30)["state"] == "cancelled"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and [p.name for p in work.iterdir()] != ["photo.png"]:
        time.sleep(0.05)
    assert [p.name for p in work.iterdir()] == ["photo.png"]


def test_cancel_removes_the_partial_output(tmp_path):
    d, job, work = _supervised_daemon(tmp_path)
    try:
        assert d.call("cancel", {"job": job}) == {"ok": True}
        _assert_cleaned_up(d, job, work)
    finally:
        d.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_sigkill_of_the_managed_process_still_cleans_up(tmp_path):
    """``qs kill`` ends with SIGKILL for the process it started, then the pipes close."""
    d, job, work = _supervised_daemon(tmp_path)
    worker = d.call("hello")["pid"]
    assert worker != d.proc.pid
    d.proc.kill()
    d.proc.wait(timeout=10)
    d.proc.stdin.close()
    _assert_cleaned_up(d, job, work)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_sigterm_is_forwarded_to_the_daemon(tmp_path):
    d, job, work = _supervised_daemon(tmp_path)
    d.proc.terminate()
    assert d.proc.wait(timeout=20) == 0
    _assert_cleaned_up(d, job, work)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_second_signal_does_not_break_the_shutdown(tmp_path):
    d, job, work = _supervised_daemon(tmp_path)
    d.proc.terminate()
    d.proc.terminate()
    assert d.proc.wait(timeout=20) == 0
    _assert_cleaned_up(d, job, work)
