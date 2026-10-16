"""Run an external native tool re-parented onto a Windows service.

Why this exists
---------------
Gowin's ``programmer_cli.exe`` embeds its own CPython 3.6 + Qt.  When *any*
PyInstaller-frozen process sits anywhere in its ancestor chain it fails to load
its entry module and dies with ::

    Error: MAINCMD module not found.        (exit code -200 / 0xFFFFFED8)

That is why ``python -m slogicpt`` (from source) scans fine but the packaged
``build.py`` exe does not.  We ruled the usual suspects out one by one on the
Windows bench: it is NOT the environment (including the ``_PYI_*`` markers --
stripping them from the frozen run and injecting them into the source run both
changed nothing), NOT a job object (neither process is in one), NOT the access
token (same interactive logon session), NOT the console, working directory or
the std handles.  Inserting a plain ``python`` process between us and the tool
does not help either: it fails whenever a frozen process is anywhere above it,
at any depth.

The only thing that works is to make ``programmer_cli`` a *non-descendant* of
our frozen process.  WMI's ``Win32_Process.Create`` does exactly that -- the new
process is parented to the WMI provider service (``wmiprvse.exe``), so no frozen
process is in its ancestry, and it runs correctly (verified on the bench:
``--scan`` -> rc 49 with the cable detected).  We drive WMI through PowerShell's
``Invoke-CimMethod``; the PowerShell messenger may itself be a frozen descendant
because it only *asks* the service to create the process -- the service is the
actual parent.

The child's stdout+stderr is redirected to a temp file which we tail line by
line (so ``log_cb`` still gets near-live progress), and the batch writes the
real exit code to a sentinel file which we read back.

Windows+frozen only.  From source and on POSIX, callers use plain
``subprocess`` and never import this module's launcher.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

_CNW = 0x08000000            # CREATE_NO_WINDOW
_STILL_ACTIVE = 259


def _hidden_startupinfo():
    """STARTUPINFO：要求子进程的窗口隐藏。

    只给 CREATE_NO_WINDOW **不足以**隐藏 powershell —— 打包成 --windowed 后实测
    仍会闪出一个 powershell 窗口（每跑一次 WMI 调用闪一下）。再叠加
    STARTF_USESHOWWINDOW + SW_HIDE 才真正不显示。非 Windows 返回 None。
    """
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def needs_reparent() -> bool:
    """True only when we are the packaged (frozen) exe on Windows -- the one
    case where a native child inherits the poisoned ancestry."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _taskkill(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=_CNW,
                       startupinfo=_hidden_startupinfo())
    except OSError:
        pass


def _alive(pid: int) -> bool:
    """Cheap liveness check so an orphaned child (killed without writing the
    sentinel) does not hang us until the watchdog deadline."""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)          # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def _wmi_create(cmdline: str, work: Path) -> tuple[int | None, str]:
    """Ask the WMI service to create `cmdline`; return (pid, diagnostic).
    The PowerShell script goes into a .ps1 (run via -File) with the command line
    in a here-string, so nothing needs quoting and there is no stdin parsing."""
    ps1 = work / "wmi.ps1"
    ps1.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$cl = @'\n" + cmdline + "\n'@\n"
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        "-Arguments @{CommandLine=$cl}\n"
        "if ($r.ReturnValue -ne 0) { [Console]::Out.Write('ERR=' + "
        "$r.ReturnValue); exit 0 }\n"
        "[Console]::Out.Write('PID=' + $r.ProcessId)\n",
        encoding="utf-8")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30, creationflags=_CNW,
            startupinfo=_hidden_startupinfo())
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"powershell/WMI invocation failed: {e}"
    text = ((r.stdout or "") + (r.stderr or "")).strip()
    m = re.search(r"PID=(\d+)", text)
    if m:
        return int(m.group(1)), ""
    return None, text or f"WMI returned no PID (rc={r.returncode})"


def run_reparented(argv: list[str], cwd: str | None, timeout_s: float,
                   log_cb: Callable[[str], None],
                   cancel: threading.Event | None) -> tuple[int, str]:
    """Launch `argv` via WMI so it escapes our frozen ancestry, streaming its
    combined stdout+stderr to `log_cb`.  Returns (returncode, joined output);
    rc = -1 on launch failure / timeout / cancel -- matching `_run_argv`."""
    work = Path(tempfile.mkdtemp(prefix="slpt_"))
    out = work / "out.txt"
    rcf = work / "rc.txt"
    bat = work / "run.bat"
    try:
        out.write_bytes(b"")                       # exist before we tail it
        body = ["@echo off", "set PYTHONUNBUFFERED=1"]
        if cwd:
            body.append(f'cd /d "{cwd}"')
        body.append(f'{subprocess.list2cmdline([str(a) for a in argv])} '
                    f'> "{out}" 2>&1')
        # 注意 `%errorlevel%` 和 `>` 之间**必须有空格**：写成 `echo %errorlevel%>`
        # 时展开成 `echo 7> "rc.txt"`，cmd 会把 `7>` 当成"重定向句柄 7"，于是
        # rc.txt 是空的、上层永远拿到 rc=-1（实测两种创建方式都一样）。
        # 这个 bug 会让依赖 rc 的判据（_write_ok：Gowin 烧录/切换）恒判失败。
        body.append(f'echo %errorlevel% > "{rcf}"')
        # cmd reads a .bat in the ANSI code page; paths are ASCII in practice
        bat.write_text("\r\n".join(body) + "\r\n", encoding="mbcs",
                       errors="replace")

        # 用 wscript + VBS 以**隐藏窗口**启动 bat。
        #
        # 为什么不能直接让 WMI 创建 `cmd.exe /c bat`：cmd.exe 是控制台程序，而 WMI
        # 的 Win32_Process.Create 是在调用者的交互桌面上建进程 —— 于是每跑一次就弹
        # 出一个命令窗口再消失。打包成 --windowed 后没有父控制台可继承，这个现象尤
        # 其明显（实测：自动检测每 2.5 秒跑一次 programmer_cli，屏幕上就一直闪黑框）。
        #
        # wscript.exe 是 GUI 子系统程序，自己不产生控制台；VBS 里用
        # WshShell.Run(cmd, 0, True) 的 0 = 窗口隐藏，于是全程无窗口。
        vbs = work / "run.vbs"
        inner = f'cmd.exe /c ""{bat}""'
        vbs.write_text(
            'Set sh = CreateObject("WScript.Shell")\r\n'
            'rc = sh.Run("' + inner.replace('"', '""') + '", 0, True)\r\n'
            'WScript.Quit rc\r\n',
            encoding="mbcs", errors="replace")

        pid, diag = _wmi_create(f'wscript.exe //nologo "{vbs}"', work)
        if pid is None:
            log_cb("[programmer] " + f"WMI launch failed: {diag}")
            return -1, ""

        lines: list[str] = []
        buf = ""
        deadline = time.time() + timeout_s
        killed: str | None = None
        grace_left = 6           # ~1s of polls after the child is gone
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            while True:
                buf += fh.read()
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    s = line.rstrip()
                    if s:
                        lines.append(s)
                        log_cb(s)
                done = rcf.exists()
                if done:
                    break
                if cancel is not None and cancel.is_set():
                    killed = "cancel"
                elif time.time() > deadline:
                    killed = "timeout"
                if killed:
                    _taskkill(pid)
                    break
                if not _alive(pid):
                    grace_left -= 1
                    if grace_left <= 0:
                        break
                time.sleep(0.15)
            buf += fh.read()     # final drain after exit / kill
        for line in buf.splitlines():
            s = line.rstrip()
            if s:
                lines.append(s)
                log_cb(s)

        if killed:
            return -1, "\n".join(lines)
        rc = -1
        if rcf.exists():
            try:
                rc = int(rcf.read_text(errors="replace").strip())
            except ValueError:
                rc = -1
        return rc, "\n".join(lines)
    finally:
        shutil.rmtree(work, ignore_errors=True)
