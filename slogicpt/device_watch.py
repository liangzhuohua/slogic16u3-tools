"""USB device detection: which product, in which mode (APP / DFU).

The (vid, pid) -> (product, mode) map is derived entirely from the loaded
product profiles -- no hardcoded IDs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

import usb.backend.libusb1
import usb.core
import usb.util

from .profiles import PLATFORM_KEY, RESOURCES_DIR, ProductProfile, link_name


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


# RECONFIG (the "USB LA" spec's 0x30 device-management extension): a vendor OUT
# control request that makes the FPGA reconfigure itself, so the board drops
# off the bus and comes back in the other mode (APP <-> DFU).
RECONFIG_REQTYPE = 0x40    # Vendor, host-to-device, device recipient
RECONFIG_BREQUEST = 0x30
RECONFIG_TRIGGER = 0x0001  # 1 = reconfigure (0 = no-op probe)
RECONFIG_MAGIC = 0x5253    # 'RS'


def reconfig(d: DeviceStatus) -> str:
    """Send RECONFIG to the board `d` describes (found again by its bus and
    port, so a second board of the same PID is never hit).  Returns "" when
    sent, else why not.  The device may leave the bus before the status stage
    completes, so an error from the transfer itself still counts as sent:
    whether it worked is judged by the board re-enumerating in the other mode."""
    try:
        dev = usb.core.find(idVendor=d.profile.vid, idProduct=d.pid,
                            backend=libusb_backend(),
                            custom_match=lambda x: (x.bus == d.bus and tuple(
                                x.port_numbers or ()) == d.port_numbers))
    except Exception as e:                                  # noqa: BLE001
        return str(e)
    if dev is None:
        return "device not found"
    try:
        dev.ctrl_transfer(RECONFIG_REQTYPE, RECONFIG_BREQUEST,
                          RECONFIG_TRIGGER, RECONFIG_MAGIC, None)
    except usb.core.USBError:
        pass                      # gone mid-transfer: expected
    finally:
        usb.util.dispose_resources(dev)
    return ""


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
