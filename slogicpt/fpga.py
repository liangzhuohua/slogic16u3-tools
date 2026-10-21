"""Tang Nano 9K 信号发生器控制（产测夹具的信号源）。

夹具里的 Tang Nano 9K 跑 `testgen` 设计，产生 10 种测试图案（M0..M9）。
本模块负责找到它的串口、切换模式、并确认切换成功。

协议（见 FPGA 侧 `rtl/cmd_rx.v`）
--------------------------------
    '0'..'9'   选择模式 0..9
    'n' / 'p'  下一个 / 上一个（环形）
    'r'        重新开始当前图案（帧复位脉冲）
    FPGA 在**模式发生变化时**回一行 `"MODE n\r\n"`；模式没变则不回。

串口 115200 8N1。加 `\n` 结尾也可以，FPGA 只取第一个字符、其余忽略。

实测要点（这些都踩过，别重复踩）
--------------------------------
* 端口是 **VID:PID = 0x0403:0x6010**（BL702 模拟 FTDI），序列号形如
  `BL702_FTDI_2038131017A8`。注意 **pyserial 把 vid/pid 报成十进制**
  （0x0403 显示为 1027），比较时务必同时按十六进制写。
* **串口必须常开**。早期版本每次切模式都新起一个 PowerShell 去发字符，
  单次约 1.7 秒；常开后降到约 0.1 秒。40 格的矩阵里这是 60 秒的差别。
* **模式没变就没有回复**，这是正常行为，不是故障。所以本模块缓存"已确认的
  模式"，目标与缓存一致时**直接跳过**，连字符都不发。
* "FPGA 死了" 和 "已经在该模式" 都会表现为没有回复，必须区分。用 `'p'`
  做存活检查：`'p'` 一定会改变模式，所以**一定会回复**。
* 切换后要给图案一点稳定时间（`settle_ms`），否则分析仪可能抓到切换瞬间的
  半截图案。
"""
from __future__ import annotations

import threading
import time
import re
from dataclasses import dataclass

try:                                    # 集成进 slogicpt 时走这条
    from .i18n import t
except ImportError:                     # 独立运行/自测时走这条
    def t(s, **kw):
        return s.format(**kw) if kw else s

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:                     # pragma: no cover
    serial = None
    list_ports = None


# 夹具 BL702 的 USB 标识。pyserial 报十进制（0x0403 == 1027）。
FPGA_VID = 0x0403
FPGA_PID = 0x6010

# 光靠 VID:PID 不够：**Gowin 的 USB 烧录器也是 0403:6010**（FT2232H 双通道），
# 而且它的两个通道会各占一个串口。只要烧录器插着，自动查找就会同时匹配到
# Tang Nano 和烧录器的 A/B 通道，于是报"找到多个"。
#
# 实测三个端口的序列号：
#     COM20  2025030317A                  <- 烧录器通道 A（FT2232）
#     COM21  2025030317B                  <- 烧录器通道 B（FT2232）
#     COM30  BL702_FTDI_2038131017A8      <- Tang Nano 9K
#
# Tang Nano 的 BL702 固件会在序列号里带 `BL702`；普通 FT2232 不带。用它区分。
# 这不是猜：把一个真的 FT2232 烧成 BL702 的序列号格式是不现实的，而反过来
# 一个 BL702 也不会把自己报成别的名字。
FPGA_SERIAL_HINT = "BL702"

# 夹具固件 testgen 现在有 7 个模式（M0..M6）。以前是 10 个，自测曾因此
# 去问 M7..M9，FPGA 正确地拒绝确认，自测却把好硬件报成 FAIL。
MODE_COUNT = 7

BAUD = 115200


class SignalSourceNotFound(Exception):
    """找不到信号发生器的串口。相关测试项据此判 FAIL（原因：没有信号发生器）。"""


class SignalSourceError(Exception):
    """串口打开成功但通信异常（例如存活检查没有回复）。"""


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    serial_number: str | None
    location: str | None = None      # Linux: "1-7.4.4:1.1" (…:config.interface)


def find_ports() -> list[PortInfo]:
    """列出所有看起来像 Tang Nano 的串口。"""
    if list_ports is None:
        return []
    out = []
    for p in list_ports.comports():
        if p.vid == FPGA_VID and p.pid == FPGA_PID:
            out.append(PortInfo(p.device, p.description or "", p.serial_number,
                                getattr(p, "location", None)))
    return out


def is_fixture_serial(serial: str | None) -> bool:
    """True for the fixture's BL702 (its serial carries "BL702"); False for the
    Gowin programmer's FT2232H, which shares VID:PID 0403:6010."""
    return FPGA_SERIAL_HINT in (serial or "").upper()


def fixture_ports() -> list[str]:
    """Serial ports of the fixture (empty when absent or pyserial is missing).
    The single source for "is the fixture plugged in" -- pipeline and the GUI
    both ask here."""
    try:
        return [p.device for p in find_ports() if is_fixture_serial(p.serial_number)]
    except Exception:                       # noqa: BLE001  (enumeration can fail mid-replug)
        return []


def fixture_present() -> bool:
    return bool(fixture_ports())


def _usb_interface(location: str | None) -> int | None:
    """USB interface number from pyserial's location ("1-7.4.4:1.1" ->
    config 1, interface 1).  None where the platform does not report it."""
    m = re.search(r":\d+\.(\d+)$", location or "")
    return int(m.group(1)) if m else None


def find_port(explicit: str | None = None) -> str:
    """定位信号发生器的串口。

    `explicit` 给出时直接用它（但仍要求存在）。否则按 VID:PID 自动查找。
    找不到、或有多个候选而无法区分时抛 SignalSourceNotFound。
    """
    if explicit:
        if list_ports is not None:
            known = {p.device.upper() for p in list_ports.comports()}
            if explicit.upper() not in known:
                raise SignalSourceNotFound(
                    t("Configured port {p} is not present").format(p=explicit))
        return explicit

    cands = find_ports()
    if not cands:
        raise SignalSourceNotFound(
            t("No Tang Nano signal generator found (looking for {vid:#06x}:{pid:#06x})")
            .format(vid=FPGA_VID, pid=FPGA_PID))
    if len(cands) == 1:
        return cands[0].device

    # More than one 0403:6010 port. The usual cause is the Gowin USB programmer,
    # which is an FT2232H with the same IDs and TWO serial ports of its own -- so
    # plugging it in for a production run makes this look ambiguous even though
    # only one of the ports is the fixture.
    #
    # The fixture's BL702 puts "BL702" in its serial number; an FT2232 does not.
    bl702 = [c for c in cands if is_fixture_serial(c.serial_number)]
    if len(bl702) == 1:
        return bl702[0].device
    # One BL702 seen twice: on Linux its FT2232 emulation exposes BOTH interfaces
    # as ttyUSB -- A (interface 0) is JTAG, B (interface 1) is the UART that
    # speaks the mode protocol. Windows usually shows only one COM port.
    if len({c.serial_number for c in bl702}) == 1:
        uart = [c for c in bl702 if _usb_interface(c.location) == 1]
        if len(uart) == 1:
            return uart[0].device

    names = ", ".join("%s(%s)" % (c.device, c.serial_number) for c in cands)
    hint = (t("none of them reports a serial containing \"{h}\"")
            .format(h=FPGA_SERIAL_HINT) if not bl702 else
            t("{n} of them report a serial containing \"{h}\"")
            .format(n=len(bl702), h=FPGA_SERIAL_HINT))
    raise SignalSourceNotFound(
        t("Multiple 0403:6010 ports found ({names}); {hint}. "
          "The Gowin USB programmer is an FT2232H with the same IDs and two "
          "ports of its own, so unplug it if you do not need it, or set "
          "signal_source.serial explicitly to the fixture's port.")
        .format(names=names, hint=hint))


class SignalSource:
    """保持串口常开的信号发生器控制器。

    线程安全（产测流程在独立线程里跑步骤）。所有公开方法都加锁。
    """

    def __init__(self, port: str | None = None, baud: int = BAUD,
                 settle_ms: int = 400, reply_timeout_ms: int = 1000):
        self._port_arg = port
        self._baud = baud
        self._settle_s = settle_ms / 1000.0
        self._reply_timeout_s = reply_timeout_ms / 1000.0
        self._ser = None
        self._lock = threading.RLock()
        self._mode: int | None = None      # 已确认的模式；None = 未知
        self.port = None

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        """找端口、打开、做存活检查、回到 M0。"""
        if serial is None:
            raise SignalSourceError(
                "pyserial is not installed (pip install pyserial)")
        with self._lock:
            if self._ser is not None:
                return
            self.port = find_port(self._port_arg)
            try:
                self._ser = serial.Serial(self.port, self._baud, timeout=0.05,
                                          write_timeout=2.0)
            except Exception as e:                       # noqa: BLE001
                raise SignalSourceError(
                    t("Cannot open {p}: {e}").format(p=self.port, e=e)) from e

            time.sleep(0.2)
            self._drain()

            # 存活检查：'p' 一定会改变模式，所以一定会回复。
            if not self._liveness():
                self.close()
                raise SignalSourceError(
                    t("{p} opened but the FPGA did not answer; "
                      "is the bitstream loaded?").format(p=self.port))

            self._mode = None
            self.select_mode(0)

    def close(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:                        # noqa: BLE001
                    pass
                self._ser = None
            self._mode = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- mode control -------------------------------------------------------

    def select_mode(self, mode: int) -> bool:
        """切到指定模式。返回 True 表示已确认。

        目标与已确认模式一致时直接返回，不发字符 —— 因为模式没变 FPGA 不会回复，
        发了也确认不了，只会白等一个超时。
        """
        if not 0 <= mode <= 9:
            raise ValueError("mode must be 0..9, got %r" % (mode,))
        with self._lock:
            if self._ser is None:
                raise SignalSourceError("signal source is not open")
            if self._mode == mode:
                return True

            if not self._send_and_expect(mode):
                # 没有回复：可能本来就在该模式（并发/外部改过），
                # 用一次 'p' 强制变化再试，这样能确定 FPGA 还活着。
                self._write(b"p")
                self._drain()
                if not self._send_and_expect(mode):
                    raise SignalSourceError(
                        t("No confirmation for mode {m} on {p}")
                        .format(m=mode, p=self.port))

            self._mode = mode
            time.sleep(self._settle_s)
            return True

    def restart_pattern(self) -> None:
        """发 'r' 让当前图案从头开始（帧复位脉冲）。"""
        with self._lock:
            if self._ser is None:
                raise SignalSourceError("signal source is not open")
            self._write(b"r")

    def invalidate(self) -> None:
        """忘掉缓存的模式。

        重新烧录 FPGA 之后必须调用 —— FPGA 会回到 M0，而本模块的缓存还是旧的。
        """
        with self._lock:
            self._mode = None

    @property
    def mode(self) -> int | None:
        """已确认的模式；None 表示未知。"""
        return self._mode

    # -- internals ----------------------------------------------------------

    def _write(self, data: bytes) -> None:
        self._ser.reset_input_buffer()
        self._ser.write(data)
        self._ser.flush()

    def _drain(self) -> None:
        """丢掉缓冲区里残留的内容（上一次回复可能还没读完）。"""
        try:
            self._ser.reset_input_buffer()
        except Exception:                                # noqa: BLE001
            pass

    def _read_reply(self, timeout_s: float) -> str:
        deadline = time.time() + timeout_s
        buf = bytearray()
        while time.time() < deadline:
            n = self._ser.in_waiting
            if n:
                buf += self._ser.read(n)
                if b"MODE" in buf:
                    # 收到完整一行就返回
                    if b"\n" in buf:
                        break
            else:
                time.sleep(0.005)
        return buf.decode("ascii", "replace")

    def _send_and_expect(self, mode: int) -> bool:
        self._write(bytes([ord("0") + mode]))
        reply = self._read_reply(self._reply_timeout_s)
        return ("MODE %d" % mode) in reply

    def _liveness(self, tries: int = 3) -> bool:
        for _ in range(tries):
            self._write(b"p")
            if "MODE" in self._read_reply(self._reply_timeout_s):
                return True
        return False


# ---------------------------------------------------------------------------
# 命令行自测：python -m slogicpt.fpga
# ---------------------------------------------------------------------------
def _selftest() -> int:
    print("== 查找信号发生器 ==")
    cands = find_ports()
    if not cands:
        print("  未找到（VID:PID %#06x:%#06x）" % (FPGA_VID, FPGA_PID))
        return 1
    for c in cands:
        print("  %s  %s  sn=%s" % (c.device, c.description, c.serial_number))

    print("== 打开 ==")
    src = SignalSource()
    try:
        src.open()
    except SignalSourceNotFound as e:
        print("  未找到: %s" % e)
        return 1
    except SignalSourceError as e:
        print("  打开失败: %s" % e)
        return 1
    print("  端口 %s 已打开，存活检查通过，当前模式 %s" % (src.port, src.mode))

    # 7 patterns now (M0..M6), not 10. The self-test asked for M7..M9, the FPGA
    # correctly refused to confirm them, and the test reported FAIL against
    # working hardware.
    print("== 依次切到 0..%d，记录耗时 ==" % (MODE_COUNT - 1))
    ok = True
    for m in range(MODE_COUNT):
        t0 = time.time()
        try:
            src.select_mode(m)
        except SignalSourceError as e:
            print("  M%d 失败: %s" % (m, e))
            ok = False
            continue
        print("  M%d  ok   %.3f s" % (m, time.time() - t0))

    print("== 重复切到同一模式应跳过（不发字符）==")
    t0 = time.time()
    src.select_mode(MODE_COUNT - 1)
    print("  重切 M%d  %.4f s" % (MODE_COUNT - 1, time.time() - t0))

    src.close()
    print("RESULT: %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
