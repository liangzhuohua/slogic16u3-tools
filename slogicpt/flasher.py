"""DFU firmware flashing, wrapping the slogicpt.dfu library."""
from __future__ import annotations

import contextlib
import io
import threading
import time
from pathlib import Path
from typing import Callable

from .dfu.spi_flash import flash_firmware
from .i18n import t


class FlashError(Exception):
    pass


class _LogBridge(io.TextIOBase):
    """Redirect dfu print() progress into a line callback."""

    def __init__(self, log_cb: Callable[[str], None]):
        self._log_cb = log_cb
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._log_cb(line)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._log_cb(self._buf)
        self._buf = ""


def flash_app_firmware(*, vid: int, pid: int, addr: int, firmware: Path,
                       verify: bool = True, ota=None,
                       log_cb: Callable[[str], None] | None = None,
                       cancel: threading.Event | None = None,
                       app_pid: int | None = None) -> None:
    """Flash the application firmware onto a device in DFU mode.

    `ota` (optional): a profiles.OtaParams carrying USB-SPI bridge tuning
    (write_chunk / read_chunk / timeouts); None keeps conservative defaults.
    `app_pid` (optional): the APP-mode PID.  Only needed so a hung DFU device can
    be rebooted back into DFU when recovering: RECONFIG toggles DFU<->APP, so
    without it the recovery cannot know which PID to look for after the reboot.
    Raises FlashError with a readable message on any failure.
    """
    firmware = Path(firmware)
    if not firmware.is_file():
        raise FlashError(t("Firmware file not found: {path}").format(path=firmware))
    if cancel is not None and cancel.is_set():
        raise FlashError(t("Cancelled"))
    data = firmware.read_bytes()
    if log_cb:
        log_cb(t("Firmware: {path} ({size} bytes) -> {vid:#06x}:{pid:#06x} @ {addr:#x}").format(
            path=firmware, size=len(data), vid=vid, pid=pid, addr=addr))

    out = _LogBridge(log_cb) if log_cb else io.StringIO()

    def _flash() -> None:
        flash_firmware(vid, pid, addr, data, verify=verify, ota=ota)

    def _rec_log(msg: str) -> None:
        # 恢复阶段的日志直连 log_cb，避免与 redirect_stdout 的桥接重复
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    try:
        with contextlib.redirect_stdout(out):
            try:
                _flash()
            except Exception as first:
                import usb.core
                if not isinstance(first, usb.core.USBError):
                    raise
                # 1) 端点 stall：dev.reset() 重枚举后重试一次（多数情况够用）
                print(t("USB transfer failed ({err}), resetting device and "
                        "retrying once…").format(err=first))
                _usb_reset(vid, pid)
                try:
                    _flash()
                except Exception as second:
                    if not isinstance(second, usb.core.USBError):
                        raise
                    # 2) dev.reset() 只重枚举 USB 端点，清不掉芯片内部（SPI
                    #    桥接器/传输状态机）的挂死——只有 RECONFIG 让 FPGA
                    #    重新配置才是真正的软重启。
                    print(t("Still no response after reset ({err}); rebooting the "
                            "device (RECONFIG) and retrying…").format(err=second))
                    if not _reconfig_reboot(vid, pid, app_pid, _rec_log):
                        raise FlashError(t(
                            "DFU flash failed: device unresponsive ({err}); "
                            "power-cycle the device (unplug and replug its USB) "
                            "and retry").format(err=second)) from second
                    _flash()
    except FlashError:
        raise
    except Exception as e:  # usb.core errors, RuntimeError, assertion...
        raise FlashError(t("DFU flash failed: {err}").format(err=e)) from e
    finally:
        out.flush()


def _usb_reset(vid: int, pid: int, settle_s: float = 2.0) -> None:
    import time
    import usb.core
    import usb.util
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise FlashError(t("Device not found after reset"))
    try:
        dev.reset()   # 常见现象：抛 "Entity not found" 但设备已实际重枚举
    except usb.core.USBError:
        pass
    usb.util.dispose_resources(dev)
    time.sleep(settle_s)


def _reconfig_reboot(vid: int, dfu_pid: int, app_pid: int | None, log,
                     settle_s: float = 2.0, timeout_s: float = 20.0) -> bool:
    """把卡死的 DFU 设备真正重启回 DFU 模式（RECONFIG 触发 FPGA 重配置）。

    为什么需要：`dev.reset()` 只让 USB 端点重枚举，清不掉芯片内部（SPI 桥接器 /
    传输状态机）的挂死状态 —— 实测"上一次烧写中途被异常打断"就会留下这种状态，
    之后每一笔 bulk 传输都报 Pipe error / I/O error。RECONFIG 让 FPGA 重新配置，
    是唯一不需要人工断电的软重启手段。

    代价：RECONFIG 会让设备以**另一模式**重新枚举（DFU -> APP），所以若它落到
    APP，要再发一次 RECONFIG 把它带回 DFU。

    返回 True 表示 DFU 设备已重新可寻址（可以重试烧写）；False 表示重启失败，
    只能人工断电重来。
    """
    from . import mode_switch
    from .device_watch import find_pid

    def _send(pid: int) -> None:
        try:
            mode_switch.reconfig(vid, pid, log)
        except Exception as e:                                   # noqa: BLE001
            # 控制传输本身因设备离线报错属预期，记录下来继续等待重枚举
            log(t("RECONFIG to {vid:#06x}:{pid:#06x} reported: {err}").format(
                vid=vid, pid=pid, err=e))

    def _wait_any(pids: set[int]) -> int | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for p in pids:
                if find_pid(vid, p):
                    return p
            time.sleep(0.5)
        return None

    log(t("Rebooting the device with RECONFIG ({vid:#06x}:{pid:#06x})…").format(
        vid=vid, pid=dfu_pid))
    _send(dfu_pid)
    time.sleep(settle_s)                 # 让旧实例离线、新实例重枚举
    back = _wait_any({p for p in (dfu_pid, app_pid) if p is not None})
    if back is None:
        log(t("Device did not re-enumerate after RECONFIG"))
        return False
    if back == dfu_pid:
        log(t("Device rebooted straight back into DFU mode"))
        return True
    # 落到了 APP：再发一次 RECONFIG 切回 DFU
    log(t("Device rebooted into APP mode; sending RECONFIG once more to "
          "return to DFU"))
    _send(back)
    time.sleep(settle_s)
    if _wait_any({dfu_pid}) == dfu_pid:
        log(t("Device is back in DFU mode after the RECONFIG reboot"))
        return True
    log(t("Device did not return to DFU mode after RECONFIG"))
    return False


if __name__ == "__main__":
    # smoke test: imports resolve (run as python -m slogicpt.flasher)
    from .dfu.spi_flash import SPIFlashDevice  # noqa: F401
    print("flasher: dfu imports OK")
    print("用法示例: flash_app_firmware(vid=0x359F, pid=0x30F1, addr=0x0, firmware=Path('app.bin'))")
