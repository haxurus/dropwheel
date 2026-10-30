"""Share files with a phone on the same network: a temporary HTTP server plus a QR code.

:func:`start_share` starts a detached ``dropwheel share-serve <id>`` process (so the share
survives the daemon or CLI that created it) and returns ``{id, url, qr, expires, …}``:

* the server binds to the machine's LAN address only (``ip -j -4 addr``, else the routing
  trick), on ``share.port`` (0 = random);
* the URL contains a random token (``http://192.168.1.20:41234/<token>/``); any other path
  answers 404, there is no directory listing;
* the index page (dark Material 3 look, Italian or English) lists the files with sizes and
  thumbnails; folders are zipped on the fly, several items can be downloaded as one ZIP;
* downloads support ``Range`` requests and send ``Content-Disposition`` with an RFC 5987
  ``filename*`` so non-ASCII names survive;
* the server stops by itself after ``minutes`` (``share.minutes``), on :func:`stop_share`
  or on SIGTERM; state files live in ``$XDG_RUNTIME_DIR/dropwheel/shares`` (``<id>.json``
  with the pid, ``<id>.png`` the QR code) and are removed when it stops.

The QR code is made with python-qrcode or ``qrencode``; without either ``qr`` is ``None``
(the URL still works).
"""

from __future__ import annotations

import contextlib
import hmac
import html
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from . import APP_NAME, __version__, i18n, tools, util
from .errors import UserError

log = logging.getLogger("dropwheel.share")

__all__ = ["shares_dir", "lan_addresses", "lan_ip", "start_share", "stop_share", "list_shares", "make_qr",
           "qr_text", "content_disposition", "serve_main"]

DEFAULT_MINUTES = 15
MAX_MINUTES = 1440
_VIRTUAL_IFACES = ("docker", "br-", "virbr", "veth", "vnet", "tun", "tap", "wg", "tailscale", "zt", "lxc", "lxd",
                   "podman", "cni", "flannel", "vmnet", "vboxnet", "ham", "nordlynx", "proton", "utun", "dummy")
_STORED_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "heif", "jxl", "mp4", "m4v", "mkv", "mov", "webm",
                "avi", "mp3", "m4a", "aac", "ogg", "opus", "flac", "zip", "7z", "rar", "gz", "xz", "zst", "bz2", "tgz",
                "pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "apk", "woff", "woff2"}
_CHUNK = 1024 * 1024


# ====================================================================================== paths

def shares_dir() -> Path:
    """``$XDG_RUNTIME_DIR/dropwheel/shares`` (``$XDG_CACHE_HOME/dropwheel/shares`` without a runtime dir)."""
    runtime = util.xdg_runtime_dir()
    base = (runtime / "dropwheel") if runtime is not None else util.cache_dir()
    path = base / "shares"
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _state_path(share_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{4,32}", share_id or ""):
        raise UserError("share.err.unknown", id=str(share_id))
    return shares_dir() / f"{share_id}.json"


def _read_state(share_id: str) -> dict | None:
    try:
        data = json.loads(_state_path(share_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(share_id: str, data: dict) -> None:
    path = _state_path(share_id)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(3)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _remove_files(share_id: str) -> None:
    base = shares_dir()
    for name in (f"{share_id}.json", f"{share_id}.png"):
        with contextlib.suppress(OSError):
            (base / name).unlink()


# ==================================================================================== network

def _rank_address(addr: str) -> int:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return 99
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return 99
    if ip in ipaddress.ip_network("192.168.0.0/16"):
        return 0
    if ip in ipaddress.ip_network("10.0.0.0/8"):
        return 1
    if ip in ipaddress.ip_network("172.16.0.0/12"):
        return 2
    if ip in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT / tailnets: rarely reachable from a phone
        return 5
    return 3


def lan_addresses() -> list[str]:
    """IPv4 addresses of the active, non-virtual interfaces, best candidate first."""
    found: list[str] = []
    if tools.have("ip"):
        res = util.run_quiet(["ip", "-j", "-4", "addr", "show", "up"], timeout=5)
        if res is not None and res.returncode == 0:
            try:
                data = json.loads(res.stdout or "[]")
            except ValueError:
                data = []
            for iface in data if isinstance(data, list) else []:
                name = str(iface.get("ifname", ""))
                flags = iface.get("flags") or []
                if "LOOPBACK" in flags or name.startswith(_VIRTUAL_IFACES):
                    continue
                for addr in iface.get("addr_info") or []:
                    local = addr.get("local")
                    if addr.get("family") == "inet" and local and addr.get("scope", "global") == "global":
                        if local not in found:
                            found.append(str(local))
    for target in ("8.8.8.8", "10.255.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 9))
                local = s.getsockname()[0]
        except OSError:
            continue
        if local and local not in found:
            found.append(local)
    usable = [a for a in found if _rank_address(a) < 99]
    return sorted(usable, key=lambda a: (_rank_address(a), found.index(a)))


def lan_ip() -> str | None:
    """The address a phone on the same Wi-Fi can most likely reach (``None`` when offline)."""
    env = os.environ.get("DROPWHEEL_SHARE_HOST", "").strip()
    if env:
        return env
    addrs = lan_addresses()
    return addrs[0] if addrs else None


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


# ========================================================================================= QR

def make_qr(text: str, out: str | os.PathLike) -> str | None:
    """Write a QR code PNG for ``text`` (python-qrcode, else ``qrencode``); ``None`` if impossible."""
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.stem}.{secrets.token_hex(3)}.png")
    try:
        try:
            import qrcode  # type: ignore
            from qrcode.constants import ERROR_CORRECT_M  # type: ignore

            qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            qr.make_image(fill_color="black", back_color="white").save(str(tmp))
            if tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, target)
                return str(target)
        except ImportError:
            pass
        except Exception as exc:  # broken qrcode/PIL install: try the CLI
            log.warning("python-qrcode failed: %s", exc)
        if tools.have("qrencode"):
            res = util.run_quiet(["qrencode", "-o", str(tmp), "-s", "10", "-m", "2", "-l", "M", "--", text],
                                 timeout=15)
            if res is not None and res.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, target)
                return str(target)
        return None
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def qr_text(text: str) -> str | None:
    """The QR code drawn with Unicode half blocks for a terminal (``None`` if impossible)."""
    if tools.have("qrencode"):
        res = util.run_quiet(["qrencode", "-t", "ANSIUTF8", "-m", "2", "-l", "M", "--", text], timeout=15)
        if res is not None and res.returncode == 0 and res.stdout.strip():
            return res.stdout
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=2)
        qr.add_data(text)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except Exception:
        return None


# ================================================================================ public API

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_our_server(pid: int, share_id: str) -> bool:
    """Guard against pid reuse: on Linux the command line must be our share-serve for this id."""
    cmdline = Path(f"/proc/{pid}/cmdline")
    if not Path("/proc").is_dir():
        return _pid_alive(pid)
    try:
        args = cmdline.read_bytes().split(b"\0")
    except OSError:
        return False
    return b"share-serve" in args and share_id.encode() in args


def _clean_paths(paths: Iterable[str | os.PathLike]) -> list[str]:
    out: list[str] = []
    for p in paths:
        text = os.fspath(p)
        if text.lower().startswith("file:"):
            text = util.uri_to_path(text)
        full = os.path.abspath(os.path.expanduser(text))
        if not os.path.exists(full):
            raise UserError("err.file_not_found", name=os.path.basename(full) or full)
        if not os.access(full, os.R_OK):
            raise UserError("err.not_readable", name=os.path.basename(full))
        if full not in out:
            out.append(full)
    if not out:
        raise UserError("err.no_files")
    return out


def _package_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def _reap(proc: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        proc.wait()


def start_share(paths: Iterable[str | os.PathLike], minutes: float | None = None, port: int | None = None, *,
                host: str | None = None, lang: str | None = None, config: dict | None = None,
                timeout: float = 10.0) -> dict:
    """Share ``paths`` on the LAN for ``minutes`` and return ``{id, url, qr, expires, expires_h,
    minutes, host, port, files, pid}``.

    ``minutes``/``port`` default to ``config["share"]`` (15 min, random port).  Raises
    :class:`UserError` when nothing can be shared, no network address exists or the server
    does not start.
    """
    files = _clean_paths(paths)
    share_cfg = (config or {}).get("share") if isinstance((config or {}).get("share"), dict) else {}
    if minutes is None:
        minutes = share_cfg.get("minutes", DEFAULT_MINUTES)
    if port is None:
        port = share_cfg.get("port", 0)
    try:
        minutes_f = max(1.0, min(float(MAX_MINUTES), float(minutes)))
    except (TypeError, ValueError):
        minutes_f = float(DEFAULT_MINUTES)
    try:
        port_i = max(0, min(65535, int(port)))
    except (TypeError, ValueError):
        port_i = 0
    bind_host = host or lan_ip()
    if not bind_host:
        raise UserError("share.err.no_network")
    lang = i18n.normalize_lang(lang or i18n.detect_lang(config))
    share_id = secrets.token_hex(4)
    token = secrets.token_urlsafe(12)
    spec = {"id": share_id, "token": token, "host": bind_host, "port": port_i, "minutes": minutes_f, "paths": files,
            "lang": lang, "state": "starting", "created": time.time(), "version": __version__}
    _write_state(share_id, spec)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_package_root(), env.get("PYTHONPATH", "")) if p)
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([sys.executable, "-P", "-m", "dropwheel", "share-serve", share_id],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, close_fds=True, cwd=str(shares_dir()), **kwargs)
    except OSError as exc:
        _remove_files(share_id)
        raise UserError("share.err.start_failed", details=str(exc)) from None
    threading.Thread(target=_reap, args=(proc,), daemon=True, name="dropwheel-share-reap").start()
    deadline = time.monotonic() + timeout
    state: dict | None = None
    while time.monotonic() < deadline:
        state = _read_state(share_id)
        if state and state.get("state") in ("running", "error"):
            break
        if proc.poll() is not None:
            state = _read_state(share_id)
            break
        time.sleep(0.05)
    if not state or state.get("state") != "running":
        error = (state or {}).get("error") or ""
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        _remove_files(share_id)
        if (state or {}).get("error_key") == "share.err.port_busy":
            raise UserError("share.err.port_busy", details=error, port=port_i)
        raise UserError("share.err.start_failed", details=error or "timeout")
    url = str(state["url"])
    qr = make_qr(url, shares_dir() / f"{share_id}.png")
    expires = float(state.get("expires") or time.time() + minutes_f * 60)
    return {
        "id": share_id, "url": url, "qr": qr, "expires": expires,
        "expires_h": datetime.fromtimestamp(expires).strftime("%H:%M"), "minutes": minutes_f,
        "host": bind_host, "port": int(state.get("port") or 0), "files": len(files), "pid": int(state.get("pid") or 0),
        "qr_install": "" if qr else tools.install_hint(["qrencode"]),
    }


def stop_share(share_id: str, wait: float = 3.0) -> bool:
    """Stop share ``share_id``; ``True`` when it was running (its files are removed in any case)."""
    try:
        state = _read_state(share_id)
    except UserError:
        return False
    if state is None:
        _remove_files(share_id)
        return False
    pid = int(state.get("pid") or 0)
    was_running = bool(pid) and _pid_alive(pid) and _is_our_server(pid, share_id)
    if was_running:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        if _pid_alive(pid) and os.name == "posix":
            with contextlib.suppress(OSError):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    _remove_files(share_id)
    return was_running


def list_shares() -> list[dict]:
    """Running shares ``[{id, url, expires, paths, pid}]`` (stale state files are cleaned up)."""
    out = []
    for path in sorted(shares_dir().glob("*.json")):
        share_id = path.stem
        state = _read_state(share_id)
        if not state:
            continue
        pid = int(state.get("pid") or 0)
        if state.get("state") == "running" and pid and _pid_alive(pid):
            out.append({k: state.get(k) for k in ("id", "url", "expires", "paths", "pid", "port", "host")})
        elif state.get("state") != "starting" or time.time() - float(state.get("created") or 0) > 60:
            _remove_files(share_id)
    return out


# =============================================================================== HTTP server

def content_disposition(name: str, inline: bool = False) -> str:
    """``attachment; filename="ascii"; filename*=UTF-8''percent-encoded`` (RFC 6266 / RFC 5987)."""
    raw = os.fsencode(name) if isinstance(name, str) else bytes(name)
    text = raw.decode("utf-8", "replace")
    fallback = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r'[\x00-\x1f\x7f"\\;]', "_", fallback).strip() or "download"
    encoded = urllib.parse.quote(text.encode("utf-8"), safe="!#$&+-.^_`|~")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


class _Item:
    def __init__(self, index: int, path: str) -> None:
        self.index = index
        self.path = path
        self.name = os.path.basename(path.rstrip("/\\")) or path
        self.is_dir = os.path.isdir(path)
        try:
            self.size = util.dir_size(path) if self.is_dir else os.path.getsize(path)
        except OSError:
            self.size = 0
        stem, ext = util.split_name(self.name, is_dir=self.is_dir)
        self.ext = ext
        self.download_name = f"{self.name}.zip" if self.is_dir else self.name


class _Share:
    def __init__(self, spec: dict) -> None:
        self.id = str(spec["id"])
        self.token = str(spec["token"])
        self.lang = i18n.normalize_lang(spec.get("lang"))
        self.items = [_Item(i, p) for i, p in enumerate(spec.get("paths") or [])]
        self.expires = time.time() + float(spec.get("minutes") or DEFAULT_MINUTES) * 60
        self.downloads = 0
        self.lock = threading.Lock()

    def t(self, key: str, **fmt: Any) -> str:
        return i18n.t(key, self.lang, **fmt)


_ICON_FILE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 '
              '2 0 0 1 2-2zm7 1.5V9h5.5" fill="none" stroke="currentColor" stroke-width="1.6" '
              'stroke-linejoin="round"/></svg>')
_ICON_FOLDER = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 '
                '0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" stroke-width="1.6" '
                'stroke-linejoin="round"/></svg>')
_ICON_DOWNLOAD = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14" '
                  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                  'stroke-linejoin="round"/></svg>')

_PAGE_CSS = """
:root{color-scheme:dark;--bg:#141218;--c1:#1d1b20;--c2:#211f26;--c3:#2b2930;--primary:#d0bcff;--on-primary:#381e72;
--text:#e6e0e9;--muted:#cac4d0;--outline:#49454f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 "Google Sans","Roboto",system-ui,-apple-system,
"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}
main{max-width:720px;margin:0 auto;padding:28px 16px 40px}
header{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.logo{width:48px;height:48px;border-radius:16px;background:var(--primary);color:var(--on-primary);display:grid;
place-items:center;font-weight:700;font-size:22px}
h1{font-size:22px;font-weight:600;margin:0}
.sub{color:var(--muted);font-size:14px;margin-top:2px}
ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
li{background:var(--c2);border-radius:20px;display:flex;align-items:center;gap:14px;padding:10px 12px}
.thumb{position:relative;width:56px;height:56px;border-radius:14px;background:var(--c3);flex:none;display:grid;
place-items:center;overflow:hidden;color:var(--primary)}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:var(--c3)}
.thumb svg{width:28px;height:28px}
.info{flex:1;min-width:0}
.name{font-weight:500;overflow-wrap:anywhere}
.meta{color:var(--muted);font-size:13px}
a.btn{display:inline-flex;align-items:center;gap:8px;text-decoration:none;font-weight:600;font-size:14px;
border-radius:999px;padding:10px 16px;background:var(--c3);color:var(--primary);flex:none}
a.btn svg{width:20px;height:20px}
a.btn.filled{background:var(--primary);color:var(--on-primary)}
.all{margin:18px 0 0;display:flex;justify-content:center}
footer{margin-top:28px;color:var(--muted);font-size:13px;text-align:center}
@media (max-width:420px){a.btn span{display:none}a.btn{padding:10px}}
"""


def _render_index(share: _Share) -> bytes:
    t = share.t
    rows = []
    for item in share.items:
        href = f"f/{item.index}/{urllib.parse.quote(item.download_name.encode('utf-8', 'surrogateescape'))}"
        name = html.escape(item.name.encode("utf-8", "surrogateescape").decode("utf-8", "replace"))
        if item.is_dir:
            meta = t("share.page.folder_zip", size=util.human_size(item.size, share.lang))
            thumb = _ICON_FOLDER
        else:
            meta = util.human_size(item.size, share.lang)
            # the icon stays visible when the thumbnail cannot be made (the image removes itself)
            thumb = f'{_ICON_FILE}<img src="t/{item.index}" alt="" loading="lazy" onerror="this.remove()">'
        rows.append(
            f'<li><div class="thumb">{thumb}</div><div class="info"><div class="name">{name}</div>'
            f'<div class="meta">{html.escape(meta)}</div></div>'
            f'<a class="btn" href="{href}" download>{_ICON_DOWNLOAD}<span>{html.escape(t("share.page.download"))}'
            f'</span></a></li>')
    all_zip = ""
    if len(share.items) > 1:
        all_zip = (f'<div class="all"><a class="btn filled" href="all.zip" download>{_ICON_DOWNLOAD}'
                   f'<span>{html.escape(t("share.page.download_all"))}</span></a></div>')
    until = datetime.fromtimestamp(share.expires).strftime("%H:%M")
    count = i18n.tn("share.page.count", len(share.items), share.lang)
    title = html.escape(t("share.page.title"))
    doc = (
        f'<!doctype html><html lang="{share.lang}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">'
        f'<title>{title}</title><style>{_PAGE_CSS}</style></head><body><main>'
        f'<header><div class="logo">D</div><div><h1>{title}</h1>'
        f'<div class="sub">{html.escape(count)} · {html.escape(t("share.page.until", time=until))}</div></div></header>'
        f'<ul>{"".join(rows)}</ul>{all_zip}'
        f'<footer>{html.escape(t("share.page.footer", app=APP_NAME))}</footer></main></body></html>'
    )
    return doc.encode("utf-8")


def _zip_items(zf: zipfile.ZipFile, items: list[_Item]) -> None:
    used: set[str] = set()

    def arc_unique(name: str) -> str:
        base, ext = util.split_name(name)
        candidate = name
        n = 2
        while candidate.casefold() in used:
            candidate = f"{base} {n}.{ext}" if ext else f"{base} {n}"
            n += 1
        used.add(candidate.casefold())
        return candidate

    def safe(name: str) -> str:
        return os.fsencode(name).decode("utf-8", "replace")

    for item in items:
        if not item.is_dir:
            if os.path.isfile(item.path):
                _zip_file(zf, item.path, arc_unique(safe(item.name)))
            continue
        top = arc_unique(safe(item.name))
        for root, dirs, files in os.walk(item.path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
            rel = os.path.relpath(root, item.path)
            prefix = top if rel == "." else f"{top}/{safe(rel).replace(os.sep, '/')}"
            if not files and not dirs:
                zf.writestr(zipfile.ZipInfo(prefix + "/"), b"")
            for fname in sorted(files):
                full = os.path.join(root, fname)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                _zip_file(zf, full, f"{prefix}/{safe(fname)}")


def _zip_file(zf: zipfile.ZipFile, path: str, arcname: str) -> None:
    ext = util.split_name(os.path.basename(path))[1]
    method = zipfile.ZIP_STORED if ext in _STORED_EXTS else zipfile.ZIP_DEFLATED
    try:
        zf.write(path, arcname, compress_type=method, compresslevel=1 if method == zipfile.ZIP_DEFLATED else None)
    except (OSError, ValueError) as exc:
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            raise
        log.warning("cannot add %s to the zip: %s", path, exc)


class _Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{__version__}"
    sys_version = ""
    timeout = 120
    share: _Share

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 - BaseHTTPRequestHandler API
        log.info("%s %s", self.client_address[0], fmt % args)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle(head=False)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle(head=True)

    # ------------------------------------------------------------------ routing
    def _handle(self, head: bool) -> None:
        share = self.share
        try:
            raw_path = urllib.parse.urlsplit(self.path).path
            parts = [urllib.parse.unquote(p) for p in raw_path.split("/") if p]
            if not parts or not hmac.compare_digest(parts[0].encode("utf-8", "replace"),
                                                    share.token.encode("ascii")):
                self._plain(404, share.t("share.page.not_found"), head)
                return
            if time.time() > share.expires:
                self._plain(410, share.t("share.page.expired"), head)
                return
            rest = parts[1:]
            if not rest:
                if not raw_path.endswith("/"):
                    self.send_response(301)
                    self.send_header("Location", f"/{share.token}/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._bytes(200, _render_index(share), "text/html; charset=utf-8", head)
            elif rest[0] == "f" and len(rest) >= 2 and rest[1].isdigit() and int(rest[1]) < len(share.items):
                item = share.items[int(rest[1])]
                if item.is_dir:
                    self._zip([item], item.download_name, head)
                else:
                    self._file(item, head, inline="inline" in urllib.parse.parse_qs(
                        urllib.parse.urlsplit(self.path).query))
            elif rest[0] == "t" and len(rest) == 2 and rest[1].isdigit() and int(rest[1]) < len(share.items):
                self._thumb(share.items[int(rest[1])], head)
            elif rest == ["all.zip"] and share.items:
                self._zip(share.items, share.t("share.page.zip_name", date=datetime.now().strftime("%Y-%m-%d")) + ".zip",
                          head)
            else:
                self._plain(404, share.t("share.page.not_found"), head)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:  # never let one request kill the server
            log.exception("request %s failed", self.path)
            with contextlib.suppress(Exception):
                self._plain(500, share.t("err.unexpected"), head)

    # ------------------------------------------------------------------ responses
    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _bytes(self, status: int, body: bytes, ctype: str, head: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _plain(self, status: int, message: str, head: bool) -> None:
        self._bytes(status, (message + "\n").encode("utf-8"), "text/plain; charset=utf-8", head)

    def _file(self, item: _Item, head: bool, inline: bool = False) -> None:
        try:
            fh = open(item.path, "rb")
        except OSError:
            self._plain(404, self.share.t("share.page.gone"), head)
            return
        with fh:
            size = os.fstat(fh.fileno()).st_size
            start, end, status = 0, size - 1, 200
            rng = (self.headers.get("Range") or "").strip()
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
            if m and size > 0 and (m.group(1) or m.group(2)):
                a, b = m.groups()
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            from .detect import mime_for_ext

            self.send_response(status)
            self.send_header("Content-Type", mime_for_ext(item.ext) if item.ext else "application/octet-stream")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", content_disposition(item.name, inline=inline))
            self.send_header("Last-Modified", self.date_time_string(int(os.fstat(fh.fileno()).st_mtime)))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._common_headers()
            self.end_headers()
            if head:
                return
            with self.share.lock:
                self.share.downloads += 1
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _zip(self, items: list[_Item], name: str, head: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", content_disposition(name))
        self.send_header("Connection", "close")
        self._common_headers()
        self.end_headers()
        self.close_connection = True
        if head:
            return
        with self.share.lock:
            self.share.downloads += 1
        with zipfile.ZipFile(self.wfile, "w", allowZip64=True) as zf:
            _zip_items(zf, items)

    def _thumb(self, item: _Item, head: bool) -> None:
        from . import preview

        png = None if item.is_dir else preview.thumb_path(item.path, 128)
        if not png:
            self._plain(404, self.share.t("share.page.not_found"), head)
            return
        try:
            data = Path(png).read_bytes()
        except OSError:
            self._plain(404, self.share.t("share.page.not_found"), head)
            return
        self._bytes(200, data, "image/png", head)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind() calls socket.getfqdn(), a reverse DNS lookup that can hang
        # for seconds on networks without DNS: the name is not needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


class _Server6(_Server):
    address_family = socket.AF_INET6


def serve_main(argv: list[str]) -> int:
    """Body of ``dropwheel share-serve <id>``: serve until expiry, SIGTERM or :func:`stop_share`."""
    if not argv:
        sys.stderr.write("usage: dropwheel share-serve SHARE_ID\n")
        return 2
    share_id = argv[0]
    try:
        spec = _read_state(share_id)
    except UserError:
        spec = None
    if not spec:
        sys.stderr.write(f"dropwheel share-serve: unknown share {share_id}\n")
        return 1
    logging.basicConfig(level=logging.WARNING, format="dropwheel-share: %(message)s")

    def fail(message: str, key: str = "share.err.start_failed") -> int:
        spec.update({"state": "error", "error": message, "error_key": key, "pid": os.getpid()})
        with contextlib.suppress(Exception):
            _write_state(share_id, spec)
        return 1

    try:
        share = _Share(spec)
    except Exception as exc:
        return fail(str(exc))
    if not share.items:
        return fail("no files")
    handler = type("DropwheelShareHandler", (_Handler,), {"share": share})
    host = str(spec.get("host") or "0.0.0.0").strip("[]")
    server_cls = _Server6 if ":" in host else _Server
    try:
        server = server_cls((host, int(spec.get("port") or 0)), handler)
    except OSError as exc:
        busy = getattr(exc, "errno", None) in (98, 48, 10048)
        return fail(str(exc), "share.err.port_busy" if busy else "share.err.start_failed")
    port = int(server.server_address[1])
    url = f"http://{_url_host(host)}:{port}/{share.token}/"
    spec.update({"state": "running", "pid": os.getpid(), "port": port, "url": url, "expires": share.expires,
                 "started": time.time()})
    try:
        _write_state(share_id, spec)
    except OSError as exc:
        server.server_close()
        return fail(str(exc))
    stopping = threading.Event()

    def stop(*_args: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True, name="dropwheel-share-stop").start()

    for sig in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, sig):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(getattr(signal, sig), stop)
    timer = threading.Timer(max(1.0, share.expires - time.time()), stop)
    timer.daemon = True
    timer.start()

    def watch_state() -> None:
        # stop when the state file disappears (stop_share on systems without signals, manual cleanup)
        while not stopping.wait(2.0):
            if not _state_path(share_id).exists():
                stop()

    threading.Thread(target=watch_state, daemon=True, name="dropwheel-share-watch").start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        timer.cancel()
        server.server_close()
        _remove_files(share_id)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(serve_main(sys.argv[1:]))
