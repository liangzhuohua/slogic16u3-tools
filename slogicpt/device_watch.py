"""USB device detection: which product, in which mode (APP / DFU).

The (vid, pid) -> (product, mode) map is derived entirely from the loaded
product profiles -- no hardcoded IDs.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

import usb.backend.libusb1
import usb.core
import usb.util

from .profiles import (  # noqa: F401  (link_name / parse_link re-exported)
    PLATFORM_KEY, RESOURCES_DIR, ProductProfile,
    link_name, parse_link)


class Mode(Enum):
    APP = "APP"
    DFU = "DFU"


_backend_resolved = False
_backend = None


def libusb_backend():
    """libusb 后端。Windows 上**优先用内置的 resources/bin/libusb-1.0.dll**（这样
    DLL 随项目分发即可，工位无需另装/入 PATH）；否则退回系统查找。找不到任何
    libusb 时返回 None。结果缓存。"""
    global _backend_resolved, _backend
    if not _backend_resolved:
        if PLATFORM_KEY == "windows":
            dll = RESOURCES_DIR / "bin" / "libusb-1.0.dll"
            if dll.is_file():
                _backend = usb.backend.libusb1.get_backend(
                    find_library=lambda _n: str(dll))
        if _backend is None:
            _backend = usb.backend.libusb1.get_backend()   # 系统查找
        _backend_resolved = True
    return _backend


def backend_ok() -> bool:
    """libusb 后端是否可用。无后端时 usb.core.find 抛 NoBackendError，而扫描函数
    都 except 静默 -> 表现为"看不到任何设备"；GUI 用本函数把这一致命情形显式告警。
    典型缺失：Windows 未内置/未装 libusb-1.0.dll。"""
    return libusb_backend() is not None


@dataclass(frozen=True)
class DeviceStatus:
    """One physical device.  Several boards of the same product enumerate
    with the same VID:PID, so each is reported separately; bus/port_numbers
    locate it and speed is the negotiated link (LIBUSB_SPEED_*)."""
    profile: ProductProfile
    mode: Mode
    pid: int
    serial: str | None = None   # USB iSerialNumber descriptor (version marker)
    bus: int | None = None
    port_numbers: tuple[int, ...] = ()
    address: int | None = None
    speed: int | None = None

    @property
    def link(self) -> str:
        return link_name(self.speed)

    def __str__(self) -> str:
        base = f"{self.profile.display_name} ({self.mode.value})"
        if self.speed:
            base += f" {self.link}"
        return f"{base} SN:{self.serial}" if self.serial else base


def _serial_of(dev) -> str | None:
    """Read the USB iSerialNumber string descriptor, or None if the device
    has no serial / it can't be read (perms, backend, kernel driver)."""
    try:
        if not dev.iSerialNumber:
            return None
        s = usb.util.get_string(dev, dev.iSerialNumber)
        return s.strip() if s else None
    except Exception:
        return None


def read_serial(vid: int, pid: int) -> str | None:
    """SerialNumber of the (vid, pid) device currently enumerated, or None."""
    try:
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=libusb_backend())
        return _serial_of(dev) if dev is not None else None
    except Exception:
        return None


def scan_devices(profiles: list[ProductProfile]) -> list[DeviceStatus]:
    """Every SLogic device on the host, one entry per physical board (with
    its link speed and port), across all products and both modes."""
    found: list[DeviceStatus] = []
    for p in profiles:
        candidates: list[tuple[int, Mode]] = [(p.app_pid, Mode.APP)]
        if p.dfu_pid is not None:
            candidates.append((p.dfu_pid, Mode.DFU))
        for pid, mode in candidates:
            try:
                devs = usb.core.find(find_all=True, idVendor=p.vid, idProduct=pid,
                                     backend=libusb_backend())
                for dev in devs:
                    found.append(DeviceStatus(
                        p, mode, pid, _serial_of(dev), bus=dev.bus,
                        port_numbers=tuple(dev.port_numbers or ()),
                        address=dev.address, speed=dev.speed))
            except Exception:
                pass  # backend hiccups shouldn't kill the poll loop
    return found


def find_pid(vid: int, pid: int) -> bool:
    try:
        return usb.core.find(idVendor=vid, idProduct=pid, backend=libusb_backend()) is not None
    except Exception:
        return False


def wait_for_pid(vid: int, pid: int, timeout_s: float,
                 poll_s: float = 0.5,
                 cancel: threading.Event | None = None) -> bool:
    """Poll until (vid, pid) enumerates.  False on timeout/cancel; the
    caller decides whether to prompt the operator and keep waiting."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            return False
        if find_pid(vid, pid):
            return True
        time.sleep(poll_s)
    return False


if __name__ == "__main__":
    from .profiles import load_profiles
    profiles, _ = load_profiles()
    print("Ctrl+C 退出；插拔设备观察状态变化")
    last = None
    while True:
        found = scan_devices(profiles)
        desc = ", ".join(str(d) for d in found) if found else "无设备"
        if desc != last:
            print(time.strftime("[%H:%M:%S]"), desc)
            last = desc
        time.sleep(1)
