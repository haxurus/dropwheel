"""Tests for dropwheel.jobs (naming, runner state machine, batches, cancel, undo, processes)."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from dropwheel import config as cfgmod
from dropwheel import util
from dropwheel.errors import Cancelled, MissingTool, ToolError, UserError
from dropwheel.jobs import Context, Result, atomic_target

PY = sys.executable


def write(path: Path, text: str = "data") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def states(events, job_id):
    out = []
    for ev in events:
        if ev.get("job") == job_id and (not out or out[-1] != ev["state"]):
            out.append(ev["state"])
    return out


def wait_state(runner, job_id, predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = runner.get(job_id)
        if job is not None and predicate(job):
            return job
        time.sleep(0.02)
    raise AssertionError("timeout waiting for job state")


def make_ctx(runner, action_id, files, params=None):
    job = runner._create(action_id, files, params or {})
    job.params = job.action.resolve_params(job.files, job.given_params, runner.config)
    return Context(runner, job, job.files)


# --------------------------------------------------------------------------- output naming

def test_output_path_rules(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.t", label="T", categories={"text"})(lambda ctx: None)
    src = write(dropwheel_env.work / "nota.txt")
    runner, _ = make_runner(lang="it")
    ctx = make_ctx(runner, "text.t", [str(src)])
    d = dropwheel_env.work
    assert ctx.output_path(ext="md") == d / "nota.md"
    # same extension conversion never equals the input
    assert ctx.output_path() == d / "nota 2.txt"
    assert ctx.output_path(suffix="sfx.compressed") == d / "nota (compresso).txt"
    # reserved names are not handed out twice within the daemon
    assert ctx.output_path(suffix="compressed") == d / "nota (compresso) 2.txt"
    write(d / "nota.pdf")
    assert ctx.output_path(ext="pdf") == d / "nota 2.pdf"
    assert ctx.output_path(suffix="Literal text", ext="log") == d / "nota (Literal text).log"
    assert ctx.output_path(name="Archivio 2026-10-29 14.03", ext="zip") == d / "Archivio 2026-10-29 14.03.zip"
    sub = d / "out"
    assert ctx.output_path(name="page-001.png", directory=sub) == sub / "page-001.png"
    assert sub.is_dir()
    folder = ctx.output_dir(suffix="sfx.pages")
    assert folder == d / "nota (pagine)" and folder.is_dir()
    assert ctx.output_dir(suffix="sfx.pages") == d / "nota (pagine) 2"


def test_output_path_english_and_folder_mode(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.t", label="T", categories={"text"})(lambda ctx: None)
    src = write(dropwheel_env.work / "a.txt")
    cfg = cfgmod.defaults()
    target = dropwheel_env.base / "Outputs"
    cfg["output"] = {"location": "folder", "folder": str(target), "open_after": False}
    runner, _ = make_runner(config=cfg, lang="en")
    ctx = make_ctx(runner, "text.t", [str(src)])
    assert ctx.output_path(suffix="sfx.cropped") == target / "a (Cropped).txt"
    assert target.is_dir()


def test_output_path_fallback_for_volatile_and_readonly(clean_registry, make_runner, dropwheel_env, monkeypatch):
    clean_registry.action(id="text.t", label="T", categories={"text"})(lambda ctx: None)
    vol = write(dropwheel_env.volatile / "dragged.txt")
    runner, _ = make_runner(lang="en")
    ctx = make_ctx(runner, "text.t", [str(vol)])
    out = ctx.output_path(ext="md")
    assert out == dropwheel_env.downloads / "Dropwheel" / "dragged.md"
    assert any("temporary" in w for w in ctx._job.warnings)

    ro_dir = dropwheel_env.work / "readonly"
    ro_dir.mkdir()
    ro = write(ro_dir / "locked.txt")
    real_access = os.access

    def fake_access(path, mode, *a, **kw):
        if Path(path) == ro_dir and mode & os.W_OK:
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr(os, "access", fake_access)
    ctx2 = make_ctx(runner, "text.t", [str(ro)])
    assert ctx2.output_path(ext="md").parent == dropwheel_env.downloads / "Dropwheel"
    assert any("not writable" in w for w in ctx2._job.warnings)


def test_output_for_directory_source(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="folder.zip", label="Z", categories={"folder"})(lambda ctx: None)
    folder = dropwheel_env.work / "Foto Vacanze"
    folder.mkdir()
    runner, _ = make_runner()
    ctx = make_ctx(runner, "folder.zip", [str(folder)])
    assert ctx.output_path(ext="zip") == dropwheel_env.work / "Foto Vacanze.zip"


# ---------------------------------------------------------------------------- atomic target

def test_atomic_target(dropwheel_env):
    final = dropwheel_env.work / "out.png"
    with atomic_target(final) as tmp:
        assert tmp.parent == final.parent and tmp.name.startswith(".out.png.part-") and tmp.suffix == ".png"
        tmp.write_bytes(b"ok")
    assert final.read_bytes() == b"ok"
    assert [p.name for p in final.parent.iterdir()] == ["out.png"]
    bad = dropwheel_env.work / "bad.png"
    with pytest.raises(RuntimeError):
        with atomic_target(bad) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("boom")
    assert not bad.exists() and [p.name for p in final.parent.iterdir()] == ["out.png"]
    with pytest.raises(ToolError):
        with atomic_target(dropwheel_env.work / "nothing.png"):
            pass
    clash = dropwheel_env.work / "clash.txt"
    with pytest.raises(ToolError):
        with atomic_target(clash) as tmp:
            tmp.write_text("new")
            clash.write_text("someone else")
    assert clash.read_text() == "someone else"


def test_atomic_target_part_name_within_name_max(dropwheel_env):
    # 253 bytes: a legal output name, with no room left for the hidden ".part-…" decoration
    final = dropwheel_env.work / ("\u65e5" * 83 + ".jpg")
    assert len(final.name.encode("utf-8")) == 253
    with atomic_target(final) as tmp:
        assert len(tmp.name.encode("utf-8")) <= util.NAME_MAX
        assert tmp.name.startswith(".\u65e5") and ".part-" in tmp.name and tmp.suffix == ".jpg"
        tmp.write_bytes(b"ok")
    assert final.read_bytes() == b"ok"
    assert [p.name for p in final.parent.iterdir()] == [final.name]


# ------------------------------------------------------------------------------- run_sync

def _converter(ctx):
    out = ctx.output_path(ext="md")
    with atomic_target(out) as tmp:
        tmp.write_text(ctx.file.p.read_text(encoding="utf-8").upper(), encoding="utf-8")
    ctx.progress(0.5, "half")
    return ctx.result([out], message="ok", size_before=ctx.file.size)


def test_run_sync_success_and_events(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", text="MD", categories={"text"})(_converter)
    clean_registry.group("text.convert", "grp.convert", "", ["text.convert.md"])
    src = write(dropwheel_env.work / "doc.txt", "hello")
    runner, events = make_runner(lang="it")
    job = runner.run_sync("text.convert.md", [str(src)])
    assert job.state == "done", job.error
    assert states(events, job.id) == ["queued", "running", "done"]
    final = events[-1]
    assert final["label"] == "Converti · MD" and final["files"] == ["doc.txt"]
    res = final["result"]
    out = res["outputs"][0]
    assert Path(out["path"]) == dropwheel_env.work / "doc.md" and out["name"] == "doc.md"
    assert out["size"] == 5 and out["size_h"] == "5 B" and out["category"] == "markdown"
    assert out["uri"].startswith("file://")
    assert res["message"] == "ok" and res["undoable"] is True and res["size_before"] == 5 and res["size_after"] == 5
    assert (dropwheel_env.work / "doc.md").read_text() == "HELLO"
    history = runner.history()
    assert history[0]["job"] == job.id and history[0]["undone"] is False
    assert not (dropwheel_env.cache / "dropwheel" / "tmp" / job.id).exists()


def test_user_error_and_unexpected(clean_registry, make_runner, dropwheel_env):
    def fail_user(ctx):
        raise UserError("err.file_not_found", details="more", name="x.txt")

    def fail_bug(ctx):
        raise ValueError("kaboom")

    clean_registry.action(id="text.fail", label="F", categories={"text"})(fail_user)
    clean_registry.action(id="text.bug", label="B", categories={"text"})(fail_bug)
    src = write(dropwheel_env.work / "x.txt")
    runner, events = make_runner(lang="it")
    job = runner.run_sync("text.fail", [str(src)])
    assert job.state == "failed"
    assert job.error == {"message": "File non trovato: x.txt", "details": "more", "install": ""}
    job2 = runner.run_sync("text.bug", [str(src)])
    assert job2.state == "failed" and job2.error["message"] == "Errore imprevisto"
    assert "ValueError: kaboom" in job2.error["details"]
    assert events[-1]["state"] == "failed" and events[-1]["error"]["message"] == "Errore imprevisto"


def test_missing_tool_and_not_applicable(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.needs", label="N", categories={"text"},
                          requires=["dropwheel-test-missing-tool"])(lambda ctx: None)
    clean_registry.action(id="image.only", label="I", categories={"image"})(lambda ctx: None)
    src = write(dropwheel_env.work / "x.txt")
    runner, _ = make_runner()
    job = runner.run_sync("text.needs", [str(src)])
    assert job.state == "failed" and "dropwheel-test-missing-tool" in job.error["message"]
    assert job.error["install"] == "sudo pacman -S --needed dropwheel-test-missing-tool"
    job2 = runner.run_sync("image.only", [str(src)])
    assert job2.state == "failed" and job2.error["message"] == "This action does not apply to these files"
    with pytest.raises(UserError):
        runner.run_sync("does.not.exist", [str(src)])
    with pytest.raises(UserError):
        runner.run_sync("text.needs", [])


def test_missing_input_file(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.t", label="T", categories={"text"})(lambda ctx: None)
    src = write(dropwheel_env.work / "vanishing.txt")
    runner, _ = make_runner()
    from dropwheel.detect import classify

    info = classify(src)
    src.unlink()
    job = runner.run_sync("text.t", [info])
    assert job.state == "failed" and job.error["message"] == "File not found: vanishing.txt"


def test_params_and_none_result(clean_registry, make_runner, dropwheel_env):
    seen = {}
    P = clean_registry.P

    def act(ctx):
        seen["q"] = ctx.pint("quality")
        seen["flag"] = ctx.pbool("flag")
        seen["missing"] = ctx.p("nope", "dflt")
        return None

    clean_registry.action(id="text.p", label="P", categories={"text"},
                          params=[P.int("quality", "Q", "cfg:image.jpeg_quality"), P.bool("flag", "F", False)])(act)
    src = write(dropwheel_env.work / "x.txt")
    runner, events = make_runner()
    job = runner.run_sync("text.p", [str(src)], {"flag": "yes"})
    assert job.state == "done" and seen == {"q": 85, "flag": True, "missing": "dflt"}
    assert events[-1]["result"]["kind"] == "none" and events[-1]["result"]["undoable"] is False


# --------------------------------------------------------------------------------- batches

def _maybe_fail(ctx):
    if ctx.file.name.startswith("bad"):
        raise UserError("err.damaged")
    return _converter(ctx)


def test_batch_with_one_failure(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(_maybe_fail)
    files = [write(dropwheel_env.work / n) for n in ("a.txt", "bad.txt", "c.txt")]
    runner, events = make_runner(lang="it")
    job = runner.run_sync("text.convert.md", [str(f) for f in files])
    assert job.state == "done"
    res = events[-1]["result"]
    assert res["message"] == "2/3 completati · non riusciti: 1"
    assert [o["name"] for o in res["outputs"]] == ["a.md", "c.md"]
    assert res["errors"][0]["file"] == "bad.txt" and res["errors"][0]["message"] == "Il file sembra danneggiato"
    assert any(w.startswith("bad.txt:") for w in events[-1]["warnings"])
    progress = [e["progress"] for e in events if e["state"] == "running" and e["progress"] is not None]
    assert progress and max(progress) <= 1.0


def test_batch_all_failed(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.f", label="F", categories={"text"})(_maybe_fail)
    files = [write(dropwheel_env.work / n) for n in ("bad1.txt", "bad2.txt")]
    runner, _ = make_runner()
    job = runner.run_sync("text.f", [str(f) for f in files])
    assert job.state == "failed" and job.error["message"] == "The file seems damaged"
    assert "bad1.txt" in job.error["details"] and "bad2.txt" in job.error["details"]


def test_batch_skips_unsupported(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(_converter)
    a = write(dropwheel_env.work / "a.txt")
    folder = dropwheel_env.work / "dir"
    folder.mkdir()
    runner, events = make_runner()
    job = runner.run_sync("text.convert.md", [str(a), str(folder)])
    assert job.state == "done" and "1 file skipped (not supported)" in events[-1]["warnings"]


def test_combine_receives_all_files(clean_registry, make_runner, dropwheel_env):
    got = {}

    def combine(ctx):
        got["names"] = [f.name for f in ctx.files]
        out = ctx.output_path(suffix="sfx.merged")
        with atomic_target(out) as tmp:
            tmp.write_text("".join(f.p.read_text() for f in ctx.files))
        return ctx.result([out])

    clean_registry.action(id="text.combine.merge", label="M", categories={"text"}, multi="combine")(combine)
    files = [write(dropwheel_env.work / n, n) for n in ("a.txt", "b.txt")]
    runner, events = make_runner(lang="en")
    job = runner.run_sync("text.combine.merge", [str(f) for f in files])
    assert job.state == "done" and got["names"] == ["a.txt", "b.txt"]
    assert events[-1]["result"]["outputs"][0]["name"] == "a (Merged).txt"


def test_progress_throttled(clean_registry, make_runner, dropwheel_env):
    def chatty(ctx):
        for i in range(200):
            ctx.progress(i / 200)
        time.sleep(0.15)
        return None

    clean_registry.action(id="text.chatty", label="C", categories={"text"})(chatty)
    src = write(dropwheel_env.work / "x.txt")
    runner, events = make_runner()
    job = runner.run_sync("text.chatty", [str(src)])
    running = [e for e in events if e["job"] == job.id and e["state"] == "running"]
    assert 1 <= len(running) <= 6
    assert events[-1]["state"] == "done"


# --------------------------------------------------------------------------------- cancel

def test_cancel_cooperative(clean_registry, make_runner, dropwheel_env):
    def loop(ctx):
        while True:
            ctx.check_cancel()
            time.sleep(0.01)

    clean_registry.action(id="text.loop", label="L", categories={"text"})(loop)
    src = write(dropwheel_env.work / "x.txt")
    runner, events = make_runner()
    jid = runner.submit("text.loop", [str(src)])
    wait_state(runner, jid, lambda j: j.state == "running")
    assert runner.cancel(jid) is True
    job = runner.wait(jid, timeout=10)
    assert job.state == "cancelled" and states(events, jid)[-1] == "cancelled"
    assert runner.cancel(jid) is False


def test_cancel_kills_process(clean_registry, make_runner, dropwheel_env):
    def sleeper(ctx):
        ctx.run([PY, "-c", "import time; time.sleep(60)"])
        return None

    clean_registry.action(id="text.sleep", label="S", categories={"text"})(sleeper)
    src = write(dropwheel_env.work / "x.txt")
    runner, _ = make_runner()
    jid = runner.submit("text.sleep", [str(src)])
    wait_state(runner, jid, lambda j: j.state == "running" and j.procs)
    start = time.time()
    runner.cancel(jid)
    job = runner.wait(jid, timeout=15)
    assert job.state == "cancelled" and time.time() - start < 10


def test_cancel_queued_job(clean_registry, make_runner, dropwheel_env):
    def slow(ctx):
        while not ctx.cancelled:
            time.sleep(0.01)
        raise Cancelled()

    clean_registry.action(id="text.slow", label="S", categories={"text"})(slow)
    src = write(dropwheel_env.work / "x.txt")
    cfg = cfgmod.defaults()
    cfg["jobs"]["max_parallel"] = 1
    runner, _ = make_runner(config=cfg)
    first = runner.submit("text.slow", [str(src)])
    second = runner.submit("text.slow", [str(src)])
    assert runner.cancel(second) is True
    runner.cancel(first)
    assert runner.wait(first, 10).state == "cancelled"
    assert runner.wait(second, 10).state == "cancelled"


# ------------------------------------------------------------------------------- processes

def test_ctx_run_success_failure_and_lines(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.t", label="T", categories={"text"})(lambda ctx: None)
    src = write(dropwheel_env.work / "x.txt")
    runner, _ = make_runner(lang="en")
    ctx = make_ctx(runner, "text.t", [str(src)])
    res = ctx.run([PY, "-c", "import sys; print('héllo'); sys.stderr.write('warn\\n')"],
                  env={"PYTHONIOENCODING": "utf-8"})
    assert res.returncode == 0 and res.stdout.strip() == "héllo" and "warn" in res.stderr
    lines = []
    ctx.run([PY, "-c", "print('a'); print('b', end='\\r'); print('c')"], on_line=lines.append)
    assert lines == ["a", "b", "c"]
    err_lines = []
    ctx.run([PY, "-c", "import sys; sys.stderr.write('e1\\ne2\\n')"], on_line=err_lines.append, stderr_lines=True)
    assert err_lines == ["e1", "e2"]
    raw = ctx.run([PY, "-c", "import sys; sys.stdout.buffer.write(bytes([0, 255, 1]))"], text=False)
    assert raw.stdout == b"\x00\xff\x01"
    echoed = ctx.run([PY, "-c", "import sys; print(sys.stdin.read().upper())"], input="abc")
    assert echoed.stdout.strip() == "ABC"
    script = "import sys\nfor i in range(30): sys.stderr.write(f'line {i}\\n')\nsys.exit(3)"
    with pytest.raises(ToolError) as info:
        ctx.run([PY, "-c", script])
    err = info.value.to_json("en")
    assert "failed (exit code 3)" in err["message"]
    assert err["details"].splitlines() == [f"line {i}" for i in range(15, 30)]
    assert ctx.run([PY, "-c", "import sys; sys.exit(3)"], ok_codes=(0, 3)).returncode == 3
    with pytest.raises(ToolError) as tinfo:
        ctx.run([PY, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert "did not finish" in tinfo.value.to_json("en")["message"]
    with pytest.raises(MissingTool) as minfo:
        ctx.run(["dropwheel-test-missing-tool", "--x"])
    assert minfo.value.install == "sudo pacman -S --needed dropwheel-test-missing-tool"


def test_ctx_ffmpeg_progress_parsing(clean_registry, make_runner, dropwheel_env, monkeypatch):
    clean_registry.action(id="video.t", label="T", categories={"text"})(lambda ctx: None)
    src = write(dropwheel_env.work / "x.txt")
    runner, events = make_runner()
    ctx = make_ctx(runner, "video.t", [str(src)])
    ctx._job.state = "running"
    captured = {}

    def fake_run(argv, *, on_line=None, **kw):
        captured["argv"] = argv
        for line in ("frame=1", "out_time_us=N/A", "out_time_us=5000000", "out_time_ms=10000000", "progress=end"):
            on_line(line)
            time.sleep(0.1)
        return None

    monkeypatch.setattr(ctx, "run", fake_run)
    ctx.ffmpeg(["-i", str(src), "-t", "10", "out.mp4"], label="Encoding")
    assert captured["argv"][:9] == ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress",
                                    "pipe:1", "-nostats"]
    values = [e["progress"] for e in events if e.get("progress") is not None]
    assert 0.5 in values and values[-1] == 1.0


# --------------------------------------------------------------------- staging / replace / undo

def test_volatile_input_is_staged(clean_registry, make_runner, dropwheel_env):
    seen = {}

    def act(ctx):
        seen["path"] = ctx.file.path
        seen["original"] = ctx.original().path
        return _converter(ctx)

    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(act)
    src = write(dropwheel_env.volatile / "from-browser.txt", "tmp")
    runner, events = make_runner()
    job = runner.run_sync("text.convert.md", [str(src)])
    assert job.state == "done"
    staging = dropwheel_env.cache / "dropwheel" / "staging" / job.id
    assert seen["path"].startswith(str(staging)) and seen["original"] == str(src)
    out = Path(events[-1]["result"]["outputs"][0]["path"])
    assert out == dropwheel_env.downloads / "Dropwheel" / "from-browser.md"
    assert not staging.exists()
    assert src.exists()


def test_undo_trashes_outputs(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(_converter)
    src = write(dropwheel_env.work / "u.txt")
    runner, _ = make_runner()
    job = runner.run_sync("text.convert.md", [str(src)])
    out = dropwheel_env.work / "u.md"
    assert out.exists()
    res = runner.undo(job.id)
    assert res["ok"] is True and res["trashed"] == 1 and not out.exists() and src.exists()
    assert runner.undo(job.id)["message"] == "Already undone"
    assert runner.history()[0]["undone"] is True
    assert runner.undo("nope")["ok"] is False


def test_undo_latest_and_changed_output(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(_converter)
    src = write(dropwheel_env.work / "v.txt")
    runner, _ = make_runner()
    runner.run_sync("text.convert.md", [str(src)])
    out = dropwheel_env.work / "v.md"
    out.write_text("edited by the user, much longer content")
    res = runner.undo()
    assert out.exists() and res["trashed"] == 0 and "was modified" in res["message"]


def test_not_undoable_action(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"}, undoable=False)(_converter)
    src = write(dropwheel_env.work / "w.txt")
    runner, events = make_runner()
    job = runner.run_sync("text.convert.md", [str(src)])
    assert events[-1]["result"]["undoable"] is False
    assert runner.undo(job.id)["message"] == "This operation cannot be undone"


def test_replace_original_and_undo(clean_registry, make_runner, dropwheel_env):
    def shrink(ctx):
        out = ctx.output_path(suffix="sfx.compressed")
        with atomic_target(out) as tmp:
            tmp.write_text("small")
        final = ctx.replace_original(ctx.file, out)
        return ctx.result([final])

    clean_registry.action(id="text.compress.x", label="X", categories={"text"}, replace_ok=True)(shrink)
    src = write(dropwheel_env.work / "big.txt", "original content")
    runner, events = make_runner()
    job = runner.run_sync("text.compress.x", [str(src)], {"replace": True})
    assert job.state == "done", job.error
    assert src.read_text() == "small"
    assert not (dropwheel_env.work / "big (Compressed).txt").exists()
    backups = list((dropwheel_env.state / "dropwheel" / "backups" / job.id).rglob("big.txt"))
    assert backups and backups[0].read_text() == "original content"
    assert events[-1]["result"]["undoable"] is True
    res = runner.undo(job.id)
    assert res["ok"] and res["restored"] == 1 and res["trashed"] == 1
    # two statements, not one run-on sentence
    assert res["message"] == "Undone: 1 file moved to the trash · Original restored"
    assert src.read_text() == "original content"

    job2 = runner.run_sync("text.compress.x", [str(src)])
    assert (dropwheel_env.work / "big (Compressed).txt").exists() and src.read_text() == "original content"
    assert job2.state == "done"


def test_undo_message_separates_problems(clean_registry, make_runner, dropwheel_env):
    clean_registry.action(id="text.convert.md", label="MD", categories={"text"})(_converter)
    src = write(dropwheel_env.work / "a.txt", "x")
    other = write(dropwheel_env.work / "b.txt", "y")
    runner, _ = make_runner(lang="it")
    job = runner.run_sync("text.convert.md", [str(src), str(other)])
    assert job.state == "done", job.error
    (dropwheel_env.work / "b.md").write_text("modificato da qualcun altro", encoding="utf-8")
    res = runner.undo(job.id)
    assert res["message"] == "Annullato: 1 file nel cestino · b.md è stato modificato: non spostato nel cestino"


def test_replace_names_stay_within_name_max(clean_registry, make_runner, dropwheel_env):
    def to_webp(ctx):
        out = ctx.output_path(ext="webp")
        with atomic_target(out) as tmp:
            tmp.write_text("new")
        return ctx.result([ctx.replace_original(ctx.file, out)])

    clean_registry.action(id="text.convert.webp", label="W", categories={"text"}, replace_ok=True)(to_webp)
    long_stem = "\u65e5" * 83 + "xx"
    src = write(dropwheel_env.work / f"{long_stem}.txt", "original")  # 255 bytes: ".webp" does not fit
    runner, _ = make_runner()
    job = runner.run_sync("text.convert.webp", [str(src)], {"replace": True})
    assert job.state == "done", job.error
    produced = [p for p in dropwheel_env.work.iterdir() if p.suffix == ".webp"]
    assert len(produced) == 1 and len(produced[0].name.encode("utf-8")) <= util.NAME_MAX
    assert not src.exists()
    # the original's name got taken meanwhile: the restored copy gets a longer name, still legal
    write(src, "someone else")
    res = runner.undo(job.id)
    assert res["ok"] and res["restored"] == 1 and src.read_text() == "someone else"
    restored = [p for p in dropwheel_env.work.iterdir() if "(Restored)" in p.name or "(restored)" in p.name]
    assert len(restored) == 1 and restored[0].read_text() == "original"
    assert len(restored[0].name.encode("utf-8")) <= util.NAME_MAX


def test_job_ids_stay_unique_across_sessions(make_runner):
    # the counter restarts in every session while the history and its undo markers are keyed by id
    first, _ = make_runner()
    second, _ = make_runner()
    a, b = first._new_id(), second._new_id()
    assert re.fullmatch(r"j1-[0-9a-f]{12}", a) and re.fullmatch(r"j1-[0-9a-f]{12}", b) and a != b


def test_replace_skipped_for_volatile(clean_registry, make_runner, dropwheel_env):
    def shrink(ctx):
        out = ctx.output_path(suffix="sfx.compressed")
        with atomic_target(out) as tmp:
            tmp.write_text("small")
        return ctx.result([ctx.replace_original(ctx.file, out)])

    clean_registry.action(id="text.compress.x", label="X", categories={"text"}, replace_ok=True)(shrink)
    src = write(dropwheel_env.volatile / "t.txt", "orig")
    runner, events = make_runner()
    job = runner.run_sync("text.compress.x", [str(src)], {"replace": True})
    assert job.state == "done" and src.read_text() == "orig"
    assert any("Original not replaced" in w for w in events[-1]["warnings"])


def test_failed_item_partial_output_removed(clean_registry, make_runner, dropwheel_env):
    def half(ctx):
        out = ctx.output_path(ext="md")
        out.write_text("partial")
        raise UserError("err.damaged")

    clean_registry.action(id="text.half", label="H", categories={"text"})(half)
    src = write(dropwheel_env.work / "h.txt")
    runner, _ = make_runner()
    job = runner.run_sync("text.half", [str(src)])
    assert job.state == "failed" and not (dropwheel_env.work / "h.md").exists()


def test_result_from_plain_values(clean_registry, make_runner, dropwheel_env):
    def plain(ctx):
        out = ctx.output_path(ext="log")
        out.write_text("x")
        return out

    clean_registry.action(id="text.plain", label="P", categories={"text"})(plain)
    src = write(dropwheel_env.work / "p.txt")
    runner, events = make_runner()
    runner.run_sync("text.plain", [str(src)])
    assert events[-1]["result"]["outputs"][0]["name"] == "p.log"
    assert isinstance(Result(), Result)


def test_mask_args_hides_passwords():
    from dropwheel.jobs import _mask_args

    assert _mask_args(["pdfinfo", "-upw", "s3cret", "/x.pdf"]) == "pdfinfo -upw *** /x.pdf"
    assert _mask_args(["pdftoppm", "-opw", "s3cret", "/x.pdf"]) == "pdftoppm -opw *** /x.pdf"
    assert _mask_args(["mutool", "draw", "-p", "s3cret", "/x.pdf"]) == "mutool draw -p *** /x.pdf"
    assert _mask_args(["7z", "x", "-ps3cret", "/a.7z"]) == "7z x -p*** /a.7z"
    assert _mask_args(["gs", "-sPDFPassword=s3cret", "-dPassThroughJPEGImages=false"]) == (
        "gs -sPDFPassword=*** -dPassThroughJPEGImages=false")
