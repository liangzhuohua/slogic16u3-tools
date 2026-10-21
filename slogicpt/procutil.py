"""Cross-platform subprocess helpers shared by every module that runs a tool.

One place for the things each tool wrapper used to re-implement:

- hidden_kwargs():  start a child with no console window on Windows.  Only
  CREATE_NO_WINDOW is not enough for long-lived children of the --windowed
  build (powershell still flashes a window), so STARTF_USESHOWWINDOW + SW_HIDE
  is added too.  Empty elsewhere.
- tool_env():       the environment external tools should see: PyInstaller's
  _PYI_* / _MEIPASS2 markers removed and its LD_LIBRARY_PATH override
  undone, so a tool resolves its own libraries, not ours; on Linux also the
  FUSE-less AppImage fallback.
- popen_kwargs():   the above plus an own process group/session (so kill_tree
  reaches grandchildren) and UTF-8 decoding with replacement (a Chinese
  Windows locale is GBK, and one stray byte would otherwise raise mid-run).
- kill_tree() / kill_pid_tree():  kill a child and everything it spawned.
- kill_image():     Windows `taskkill /IM`; a no-op elsewhere.
"""
from __future__ import annotations

import os
import subprocess
import sys

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
    the _PYI_* / _MEIPASS2 markers and the LD_LIBRARY_PATH override (saved by
    the bootloader as LD_LIBRARY_PATH_ORIG).  No-op when not frozen."""
    if not getattr(sys, "frozen", False):
        return
    for k in [k for k in env if k.startswith("_PYI_") or k == "_MEIPASS2"]:
        del env[k]
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)


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
