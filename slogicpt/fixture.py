"""Load the Tang Nano 9K fixture bitstream over JTAG (Gowin programmer CLI).

WHY THIS RUNS AT THE START OF EVERY TEST RUN
--------------------------------------------
The FPGA is configured into SRAM, not flash, so it loses its configuration on any
small power disturbance -- re-seating a probe cable is enough. When that happens
every pin floats and the analyser reads all 32 channels high, which looks exactly
like a catastrophic DUT failure but is not one. Reflashing at the start of a run
makes the fixture state deterministic, and costs about three seconds.

WHY NOT THE EXTERNAL SPI FLASH
------------------------------
Flash would survive power-off and need no step at all, but it cannot be written:
every exFlash operation (`programmer_cli --operation_index 8/9/10/11`) reports
"spi flash not found" (0x00019020), on both the installed V1.9.12.03 and the copy
bundled with this tool. The hardware is not at fault -- the schematic shows the
P25Q32U flash fitted and wired to the FPGA's dedicated MSPI pins (59=MCLK,
60=MCS, 61=MOSI, 62=MISO). `openFPGALoader` was tried instead and cannot reach the
device either, because it wants libusb/WinUSB while the FTDI serial driver owns
the interface -- and that driver is what provides COM30, which the mode switching
needs. See docs/production_test_plan.md R1.

OPERATION INDEX
---------------
From `programmer_cli --help`:
    2 = SRAM Program            4 = SRAM Program and Verify
    8 = exFlash Erase,Program   9 = exFlash Erase,Program,Verify
The profile picks the index via `signal_source.firmware_run`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .i18n import t
from .profiles import RESOURCES_DIR
from .sigrok import _popen_kwargs

# The Gowin CLI must be invoked by ABSOLUTE path: it locates its own module
# directory relative to the executable, and a bare name gives
# "Error: MAINCMD module not found".
DEFAULT_PROGRAMMER = Path(
    r"C:\Gowin\Gowin_V1.9.12.03_x64\Programmer\bin\programmer_cli.exe")


def find_programmer(explicit: list[str] | None = None) -> Path | None:
    """Locate programmer_cli.exe, preferring an explicit argv prefix."""
    if explicit:
        cand = Path(explicit[0])
        if cand.is_file():
            return cand
    if DEFAULT_PROGRAMMER.is_file():
        return DEFAULT_PROGRAMMER
    found = os.environ.get("GOWIN_PROGRAMMER_CLI")
    if found and Path(found).is_file():
        return Path(found)
    return None


def _openfpga_exe() -> Path | None:
    """openFPGALoader 的位置：产测资源里自带的一份，按平台挑；非 Windows 再退到
    PATH（Linux 台架用发行版包）。资源目录里只有 Windows 的 .exe 时，Linux 绝不能
    选它——执行失败会被吞掉，最后只报一句误导性的"找不到夹具"。"""
    import shutil
    import sys
    bundled = RESOURCES_DIR / "bin" / "openFPGALoader"
    if sys.platform == "win32":
        cand = bundled / "openFPGALoader.exe"
        return cand if cand.is_file() else None
    cand = bundled / "openFPGALoader"
    if cand.is_file():
        return cand
    found = shutil.which("openFPGALoader")
    return Path(found) if found else None


# Gowin op indexes that target the GW1NR's embedded flash (5 = embFlash
# Erase,Program; 6 = ...,Verify). Anything else (2 = SRAM Program, 4 = SRAM
# Program+Verify) loads SRAM -- the profile's intent: fast (~1.5 s) and the
# fixture state is identical every run.
_EMBFLASH_RUNS = (5, 6)


def _flash_openfpga(bitstream: Path, run: int, log, timeout_s: float,
                    cancel) -> bool:
    """用 openFPGALoader 按 FTDI 序列号烧夹具。成功返回 True。

    按档案 firmware_run 选目标：SRAM（默认）或片上 Flash（-f，仅 run 5/6）。
    以前固定带 -f 写 Flash：慢（~15 s）、磨损片上 Flash，且实测 GW1NR-9C 回报
    "CRC check : FAIL" 后夹具不应答——与档案 firmware_run=2(SRAM) 的本意不符。

    序列号取"含 BL702 的那个"——夹具 Tang Nano 的 BL702 会把它报在自己的序列号
    里，本产品的 Gowin 烧录器不会。这与 programmer.py 挑烧录器的判据互为反面。
    """
    exe = _openfpga_exe()
    if exe is None:
        return False
    serial = _fixture_ftdi_serial(log)
    if serial is None:
        return False
    log(t("Loading fixture with openFPGALoader: {name} (FTDI serial {s})").format(
        name=bitstream.name, s=serial))
    argv = [str(exe), "-c", "ft2232", "--ftdi-serial", serial]
    if run in _EMBFLASH_RUNS:
        argv.append("-f")
    argv.append(str(bitstream))
    import tempfile
    with tempfile.TemporaryDirectory(prefix="slogic_fixture_") as tmp:
        logfile = Path(tmp) / "openfpga.log"
        try:
            with open(logfile, "wb") as lf:
                proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT,
                                        **_popen_kwargs())
                try:
                    rc = proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    log(t("openFPGALoader timed out after {s}s").format(s=f"{timeout_s:.0f}"))
                    return False
            out = logfile.read_bytes().decode("utf-8", "replace")
        except OSError as e:
            log(t("Launch failed: {e}").format(e=e))
            return False
    for line in out.splitlines():
        log("   " + line.rstrip())
    # 只认 "CRC check: Success"，不看退出码，也不认 "Done"：openFPGALoader 每个
    # 阶段结束都打印 Done，CRC 失败（"CRC check : FAIL"）时照样有 Done——以前
    # 认 Done 会把配置失败的夹具当成功，只能靠随后的串口存活检查兜住。
    ok = "CRC check: Success" in out
    if not ok:
        log(t("openFPGALoader reported no success marker (rc={rc})").format(rc=rc))
    return ok


def _fixture_ftdi_serial(log) -> str | None:
    """夹具 BL702 的 FTDI 序列号，问 openFPGALoader 自己要。"""
    exe = _openfpga_exe()
    if exe is None:
        return None
    try:
        proc = subprocess.run([str(exe), "--scan-usb"], capture_output=True,
                              text=True, timeout=20, **_popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines():
        m = re.search(r"0x0403:0x6010\s+\S+\s+(\S+)\s+(\S+)\s+(.+?)\s*$", line)
        if m and "BL702" in m.group(2).upper():
            return m.group(2)
    log(t("Cannot find the fixture: no 0403:6010 device reports a BL702 serial"))
    return None


def flash(bitstream: Path, run: int, log_cb=None, *,
          device: str = "GW1NR-9C", cable: str = "USB Debugger A",
          channel: int = 0, cli: list[str] | None = None,
          timeout_s: float = 300, cancel=None) -> bool:
    """Program the fixture and return True on success.

    Output goes to a temporary FILE, never through a pipe. subprocess.run with
    capture_output=True does not honour its timeout when the child leaves a
    grandchild holding the pipe, and a wedged Gowin process would hang the whole
    production run with no way out.

    Tries openFPGALoader FIRST, addressed by the fixture's FTDI serial.

    The note this replaces said openFPGALoader could not reach the fixture
    "because it wants libusb/WinUSB while the FTDI serial driver owns the
    device". That was true only while the fixture's FTDI channel had no WinUSB
    binding. Once the bench installs WinUSB (which the product REQUIRES anyway
    for its own DFU flash path), openFPGALoader reaches the fixture fine:

        openFPGALoader -c ft2232 --ftdi-serial BL702_FTDI_2038131017A8 \
                       -f impl/pnr/testgen.fs
        -> write Flash 100%, Done, CRC check: Success

    The reason to prefer it is the same one that made the DFU path fail: the
    fixture's BL702 and the product's Gowin programmer are BOTH FTDI 0403:6010,
    and `programmer_cli` can only choose between them by ENUMERATION INDEX
    (--cable "USB Debugger A" resolved to whichever device came first, and after
    WinUSB was installed it stopped opening the fixture entirely). openFPGALoader
    takes --ftdi-serial, which names one device and cannot be knocked onto the
    other by plugging something in.

    programmer_cli stays as the fallback so an un-updated bench still works.
    """
    def log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    if not bitstream.is_file():
        log(t("Fixture bitstream not found: {p}").format(p=bitstream))
        return False

    if _flash_openfpga(bitstream, run, log, timeout_s, cancel):
        return True
    log(t("openFPGALoader could not load the fixture; falling back to the "
          "Gowin programmer"))

    exe = find_programmer(cli)
    if exe is None:
        log(t("Gowin programmer_cli not found; cannot load the fixture"))
        return False

    cmd = [str(exe), "--device", device, "--operation_index", str(run),
           "--fsFile", str(bitstream), "--cable", cable, "--channel", str(channel)]
    log(t("Loading fixture: {name} (run {run})").format(
        name=bitstream.name, run=run))

    # 打包（PyInstaller frozen）后必须经 WMI 重定父再启动 programmer_cli，否则它
    # 只要祖先链里有任何 frozen 进程就报 "Error: MAINCMD module not found." 并以
    # 非零退出。programmer._run_argv 早就为此走了 winexec.run_reparented，而这条
    # 夹具路径当初是直接 subprocess.run —— 从源码跑一切正常，**打包后就必然失败**
    # （实测：源码版夹具烧写正常，打包版 5 秒即报 could not load the signal
    #  generator）。这里补上同一条路径。
    from . import winexec

    reparented = winexec.needs_reparent()
    if reparented:
        rc, text = winexec.run_reparented(cmd, str(exe.parent), timeout_s,
                                          log, cancel)
        if rc == -1:
            log(t("Fixture programming timed out after {s}s").format(
                s=int(timeout_s)))
            _kill_strays()
            return False
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="slogic_fixture_") as tmp:
            logfile = Path(tmp) / "programmer.log"
            with open(logfile, "wb") as lf:
                try:
                    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                          stdout=lf, stderr=subprocess.STDOUT,
                                          timeout=timeout_s)
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    log(t("Fixture programming timed out after {s}s").format(s=int(timeout_s)))
                    _kill_strays()
                    return False
                except FileNotFoundError:
                    log(t("Cannot execute {p}").format(p=exe))
                    return False
            text = logfile.read_text(encoding="utf-8", errors="replace")

    # 重定父那条路径已经逐行写过日志了，不要再重复一遍。
    if not reparented:
        for line in text.splitlines():
            s = line.strip()
            if any(k in s for k in ("Operation", "Erasing", "Programming",
                                    "Verify", "Error", "error", "Finished", "Cost")):
                log("   " + s)

    bad = any(k in text for k in ("Error:", "error:", "not found", "FAILED"))
    if rc != 0 or bad:
        log(t("Fixture programming FAILED (exit {rc})").format(rc=rc))
        return False

    if cancel is not None:
        cancel.wait(0.0)          # no-op; keeps the signature honest for callers
    time.sleep(0.5)               # let the FPGA finish configuring before talking
    return True


def _kill_strays() -> None:
    """Kill a wedged programmer.

    Matched with a wildcard because Windows truncates process names to 15
    characters: `taskkill /IM programmer_cli.exe` works, but the same trick for
    sigrok-cli silently matched nothing and left a hung process holding the
    analyser, which then failed every later capture with LIBUSB_ERROR_ACCESS.
    """
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -like "
                    "'*programmer*' } | Stop-Process -Force "
                    "-ErrorAction SilentlyContinue"],
                   capture_output=True, timeout=20)


def _selftest() -> int:
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # openFPGALoader is the primary loader; programmer_cli is only the fallback,
    # so either one being present means the fixture can be loaded.
    ofl = _openfpga_exe()
    exe = find_programmer()
    print("openFPGALoader: %s" % ofl)
    print("programmer_cli: %s" % exe)
    if ofl is None and exe is None:
        print("RESULT: no fixture loader found")
        return 1
    if exe is not None:
        r = subprocess.run([str(exe), "--scan-cables", "F"],
                           capture_output=True, text=True, timeout=60)
        print((r.stdout or "").strip())
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
