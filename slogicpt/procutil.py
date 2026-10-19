"""Cross-platform subprocess helpers shared by every module that runs a tool.

One place for the things each tool wrapper used to re-implement:

- hidden_kwargs():  start a child with no console window on Windows.  Only
  CREATE_NO_WINDOW is not enough for long-lived children of the --windowed
  build (powershell still flashes a window), so STARTF_USESHOWWINDOW + SW_HIDE
  is added too.  Empty elsewhere.
- tool_env():       the environment external tools should see: PyInstaller's
  _PYI_* / _MEIPASS2 markers removed and its LD/DYLD library-path overrides
  undone, so a tool resolves its own libraries, not ours; on Linux also the
  FUSE-less AppImage fallback.
- popen_kwargs():   the above plus an own process group/session (so kill_tree
  reaches grandchildren) and UTF-8 decoding with replacement (a Chinese
  Windows locale is GBK, and one stray byte would otherwise raise mid-run).
- kill_tree() / kill_pid_tree():  kill a child and everything it spawned.
- kill_image():     Windows `taskkill /IM`; a no-op elsewhere.
- run_to_file():    run a tool with its output going to a temp FILE, never a
  pipe, with a timeout and cancel: a pipe nobody drains can block the child
  forever, and some tools print progress bars that fill a pipe quickly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def hidden_kwargs() -> dict:
    """Popen kwargs that keep a child's console window hidden (Windows only)."""
    if not IS_WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


def strip_pyinstaller_env(env: dict) -> None:
    """Undo what a frozen (PyInstaller) parent leaks into a child's environment:
    the _PYI_* / _MEIPASS2 markers and the library-path overrides (saved by the
    bootloader as <VAR>_ORIG).  No-op when not frozen."""
    if not getattr(sys, "frozen", False):
        return
    for k in [k for k in env if k.startswith("_PYI_") or k == "_MEIPASS2"]:
        del env[k]
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)


def tool_env() -> dict:
    env = os.environ.copy()
    if IS_LINUX:
        env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")   # FUSE-less fallback
    strip_pyinstaller_env(env)
    return env


def popen_kwargs() -> dict:
    """Standard kwargs for launching an external tool we may have to kill."""
    kw = hidden_kwargs()
    if IS_WINDOWS:
        kw["creationflags"] = kw["creationflags"] | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True      # own process group, see kill_tree
    kw["env"] = tool_env()
    kw["encoding"] = "utf-8"
    kw["errors"] = "replace"
    return kw


def kill_pid_tree(pid: int) -> None:
    """Kill a process and its descendants by PID.  On POSIX the child must
    have been started in its own session (popen_kwargs does that)."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, **hidden_kwargs())
        else:
            import signal
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and its whole process group -- proc.kill() alone leaves
    grandchildren (e.g. `sh -c '...; sleep 30'`) holding the stdout pipe open,
    which keeps a readline blocked long past the timeout."""
    kill_pid_tree(proc.pid)
    try:
        proc.kill()
    except OSError:
        pass


def kill_image(*names: str) -> None:
    """Windows: force-kill every process with one of these image names (e.g. a
    tool that outlived its run and still holds the USB device).  No-op
    elsewhere, where kill_tree already reaches the whole process group."""
    if not IS_WINDOWS:
        return
    for name in names:
        try:
            subprocess.run(["taskkill", "/F", "/IM", name],
                           capture_output=True, **hidden_kwargs())
        except OSError:
            pass


def run_to_file(argv: list[str], timeout_s: float,
                cancel: threading.Event | None = None,
                cwd: str | None = None) -> tuple[int | None, str, str | None]:
    """Run `argv` with stdout+stderr captured to a temp file.

    Returns (returncode, output, failure) where failure is None on a normal
    exit, "timeout", "cancel" or "launch: <error>"; returncode is None unless
    the process exited by itself."""
    with tempfile.TemporaryDirectory(prefix="slogic_run_") as tmp:
        logfile = Path(tmp) / "out.log"
        with open(logfile, "wb") as lf:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=lf,
                                        stderr=subprocess.STDOUT, cwd=cwd,
                                        **{k: v for k, v in popen_kwargs().items()
                                           if k not in ("encoding", "errors")})
            except OSError as e:
                return None, "", "launch: %s" % e
            deadline = time.time() + timeout_s
            failure = None
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    failure = "cancel"
                elif time.time() > deadline:
                    failure = "timeout"
                if failure:
                    kill_tree(proc)
                    proc.wait()
                    break
                time.sleep(0.05)
        out = logfile.read_bytes().decode("utf-8", "replace")
    return (None if failure else proc.returncode), out, failure
