"""Product profile loading / validation / resource self-check.

All product differences (VID/PID, channels, bandwidth, expected signal,
firmware paths, timeouts) live in resources/products/<id>/.  Each product is
a directory:

    resources/products/<id>/
        product.toml      # profile (public)
        firmware/          # app.bin, testgen.fs (factory, gitignored)

Adding a new product requires only a new directory + resources -- no code
changes.
"""
from __future__ import annotations

import platform as platform_mod
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .i18n import t

if getattr(sys, "frozen", False):
    # PyInstaller single binary: resources/ sits NEXT TO the executable
    # (admin-editable), never inside the bundle
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "out"
RESOURCES_DIR = REPO_ROOT / "resources"
PRODUCTS_DIR = RESOURCES_DIR / "products"

PLATFORM_KEY = {"Linux": "linux", "Windows": "windows"}.get(
    platform_mod.system(), "linux")

SCHEMA_VERSION = 1

# USB link names -> libusb_speed (LIBUSB_SPEED_*) codes, for [usb] links and
# the per-config `link` tag of [[plan]].
LINK_SPEEDS = {"USB2": 3, "5G": 4, "10G": 5, "20G": 6}


def link_name(speed: int | None) -> str:
    """libusb_speed code -> short link name (the inverse of LINK_SPEEDS; full-
    and low-speed both read as USB1)."""
    if speed in (1, 2):
        return "USB1"
    return next((k for k, v in LINK_SPEEDS.items() if v == speed), "?")


def parse_link(name: str) -> int:
    """'10G' / '5G' / 'USB2' -> libusb speed code (ValueError if unknown)."""
    key = str(name).strip().upper()
    if key not in LINK_SPEEDS:
        raise ValueError(t("unknown link {name!r} (expected one of {opts})").format(
            name=name, opts="/".join(LINK_SPEEDS)))
    return LINK_SPEEDS[key]


def parse_rate(value) -> int:
    """'200M' / '10k' / '500000' / 200000000 -> Hz (int)."""
    if isinstance(value, int):
        return value
    m = re.match(r"^(\d+)([kKmMgG]?)$", str(value).strip())
    if not m:
        raise ValueError(f"Invalid rate: {value!r} (expected e.g. 200M, 10K, 500000)")
    n = int(m.group(1))
    unit = m.group(2).upper()
    return n * {"": 1, "K": 1_000, "M": 1_000_000, "G": 1_000_000_000}[unit]


def format_rate(hz: int) -> str:
    """200000000 -> '200M' (sigrok --config accepts this form)."""
    if hz % 1_000_000 == 0:
        return f"{hz // 1_000_000}M"
    if hz % 1_000 == 0:
        return f"{hz // 1_000}k"
    return str(hz)


@dataclass(frozen=True)
class CaptureTest:
    channels: int
    samplerate_hz: int
    samples: str


# =============================================================================
# Signal source and test plan
#
# The original profile drove a single fixed external source (10 MHz 50% TTL) and
# could only check frequency and duty. The Tang Nano 9K fixture replaces it with
# ten patterns, which needs two new sections:
#
#   [signal_source]   how to reach the generator (serial port, VID/PID, bitstream)
#   [[plan]]          which modes to run, at which channel counts and rates, and
#                     which kinds of check apply
#
# The expectations themselves deliberately do NOT live in the TOML: the full matrix
# is 10 modes x 4 rate groups x 32 channels = 1280 numbers, and a change to the RTL
# should need exactly one edit. They are computed in patterns.py instead.
# =============================================================================

@dataclass(frozen=True)
class SignalSource:
    """The fixture that generates the test patterns."""

    kind: str                            # "fpga" | "external" | "none"
    serial: str                          # "auto" or an explicit port name
    vid: int
    pid: int
    baud: int
    settle_ms: int                       # quiet time after a mode change
    reply_timeout_ms: int
    firmware: Path | None                # Tang Nano bitstream, for the fixture step
    firmware_run: int                    # Gowin operation index (2 = SRAM Program)
    firmware_device: str                 # Gowin --device, e.g. GW1NR-9C
    firmware_cable: str                  # Gowin --cable, e.g. "USB Debugger A"
    channel_order: tuple[int, ...]       # generator channel -> analyser channel

    @property
    def is_fpga(self) -> bool:
        return self.kind == "fpga"


@dataclass(frozen=True)
class ThresholdGroup:
    """Channels that need a different input threshold from the default.

    The fixture drives two logic families into one analyser: most channels are
    3.3 V, but CH20..CH25 come from bank 3, which the on-board PSRAM locks to
    1.8 V. There is one global threshold per capture, and measurement showed no
    single value serves both -- so those channels are captured a second time at a
    lower threshold and their verdict is taken from that pass.
    """

    channels: tuple[int, ...]
    voltage_threshold_v: float
    label: str


@dataclass(frozen=True)
class PlanConfig:
    """One channel-count / sample-rate combination."""

    channels: int
    samplerate_hz: int
    samples: str
    window_ms: float                     # capture length, chosen per mode
    max_samples: int                     # cap, so a high rate cannot explode
    # the test-station pass this config belongs to (libusb speed code, e.g. 5 =
    # 10G).  A slower link caps the rate (32U3 on 5G: 32ch@100M), so each link
    # pass has its own configs.  None -> used in every pass.
    link: int | None = None


@dataclass(frozen=True)
class PlanEntry:
    """One pattern mode and how to exercise it."""

    mode: int
    label: str
    ready: bool                          # False -> report SKIP with `note`
    note: str
    checks: tuple[str, ...]
    configs: tuple[PlanConfig, ...]


@dataclass(frozen=True)
class ExpectedSignal:
    freq_hz: float
    duty_pct: float
    freq_tol_pct: float
    duty_tol_pp: float


@dataclass(frozen=True)
class Timeouts:
    wait_dfu_device_s: float = 30
    wait_app_device_s: float = 30
    dfu_flash_s: float = 600
    capture_s: float = 120


@dataclass(frozen=True)
class OtaParams:
    """DFU app.bin 烧录（USB-SPI 桥）调参，来自 product.toml [ota]。默认即当前
    保守稳定值——不写 [ota] 行为完全不变。提速主杠杆是 write_chunk：调大 = 每笔
    USB 往返写更多字节 = 更快，但受桥接器 TX FIFO/单事务上限约束，过大会卡死总线
    （SR1 恒读 0xFF）；调参时保持 verify=on，逐级上调确认稳定。各字段已在解析时
    钳到安全范围。"""
    write_chunk: int = 12        # 页编程每次 SPI 事务的数据字节（1..256，不跨 256 页边界）
    read_chunk: int = 64         # 回读校验每次 CMD_READ_DATA 字节（1..512）
    usb_timeout_ms: int = 1000   # 单次 USB 批量传输超时
    wip_timeout_s: float = 5.0   # 页写/擦除完成轮询超时
    reset_settle_s: float = 2.0  # 建链前整机复位后的重枚举等待（每次烧录固定开销）


def _parse_ota(raw: dict) -> OtaParams:
    """解析 [ota]，缺省回退默认值，并钳到安全范围（防止写坏/卡死）。"""
    def ci(key: str, default: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, int(raw.get(key, default))))

    def cf(key: str, default: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(raw.get(key, default))))

    return OtaParams(
        write_chunk=ci("write_chunk", 12, 1, 256),
        read_chunk=ci("read_chunk", 64, 1, 512),
        usb_timeout_ms=ci("usb_timeout_ms", 1000, 50, 600_000),
        wip_timeout_s=cf("wip_timeout_s", 5.0, 0.1, 600.0),
        reset_settle_s=cf("reset_settle_s", 2.0, 0.0, 30.0),
    )


@dataclass
class Problem:
    severity: Literal["error", "warning"]
    product_id: str | None
    message: str

    def __str__(self) -> str:
        prefix = f"[{self.product_id}] " if self.product_id else ""
        return f"{self.severity.upper()}: {prefix}{self.message}"


@dataclass(frozen=True)
class ProductProfile:
    id: str
    display_name: str
    vid: int
    app_pid: int
    dfu_pid: int | None                 # None -> DFU not configured
    driver: str
    num_channels: int                   # device total channels -> unitsize = ceil(n/8)
    max_bandwidth_mbps: int
    channel_options: list[int]
    samplerates_hz: list[int]
    voltage_threshold_v: float
    default_samples: str
    default_channels: int
    default_samplerate_hz: int
    capture_tests: list[CaptureTest]
    expected: ExpectedSignal
    signal_source: SignalSource          # Tang Nano fixture, or an inert source
    plan: list[PlanEntry]                # modes x configs, with their check sets
    threshold_groups: list[ThresholdGroup]   # channels needing another threshold
    app_firmware: Path | None           # resolved absolute path (may not exist yet)
    app_flash_addr: int
    verify_after_flash: bool
    # DFU<->APP 切换：True -> 产品自身支持 USB 控制传输 RECONFIG（无需外置硬件）；
    # False -> 弹窗提示人工按 MODE 键。
    switch_usb_reconfig: bool
    product_dir: Path                   # resources/products/<id>/（固件等资源按此解析）
    timeouts: Timeouts
    ota: OtaParams                      # DFU app.bin 烧录调参（product.toml [ota]）
    # 产测工位按顺序逐轮测试的 USB 链路（libusb speed 码，[usb] links），如 32U3
    # (10G, 5G)：先在 10G 下全测通过，再换插 5G 口重测，保证用户两种链路都可用。
    # 第一轮是门槛（达不到直接 FAIL）；空 -> 不设门槛、只测一轮。
    links: tuple[int, ...] = ()

    @property
    def min_link(self) -> int | None:
        """The first (gating) link pass, or None when no link is required."""
        return self.links[0] if self.links else None

    @property
    def unitsize(self) -> int:
        return (self.num_channels + 7) // 8

    def legal_rates(self, channels: int) -> list[int]:
        """Samplerates satisfying channels*rate/8 <= max_bandwidth."""
        limit = self.max_bandwidth_mbps * 1_000_000
        return [r for r in self.samplerates_hz if channels * r // 8 <= limit]


def _parse_signal_source(doc: dict, product_dir: Path, num_channels: int,
                         problems: list, pid_for_log: str) -> SignalSource:
    """Parse [signal_source]. Absent section -> an inert "none" source."""
    raw = doc.get("signal_source")
    if not raw:
        # Profiles written before the fixture existed have no signal_source. They
        # keep working: the capture steps just report SKIP instead of failing.
        return SignalSource(kind="none", serial="auto", vid=0x0403, pid=0x6010,
                            baud=115200, settle_ms=400, reply_timeout_ms=1000,
                            firmware=None, firmware_run=2,
                            firmware_device="GW1NR-9C",
                            firmware_cable="USB Debugger A",
                            channel_order=tuple(range(num_channels)))

    kind = str(raw.get("type", "none")).lower()
    if kind not in ("fpga", "external", "none"):
        problems.append(Problem("error", pid_for_log,
                                t("signal_source.type {k!r} is not one of "
                                  "fpga/external/none").format(k=kind)))
        kind = "none"

    fw_rel = raw.get("firmware")
    firmware = (product_dir / str(fw_rel)).resolve() if fw_rel else None

    order_raw = raw.get("channel_order")
    if order_raw is None:
        order = tuple(range(num_channels))
    else:
        order = tuple(int(v) for v in order_raw)
        if len(order) != num_channels or sorted(order) != list(range(num_channels)):
            problems.append(Problem(
                "error", pid_for_log,
                t("signal_source.channel_order must be a permutation of 0..{n}").format(
                    n=num_channels - 1)))
            order = tuple(range(num_channels))

    return SignalSource(
        kind=kind,
        serial=str(raw.get("serial", "auto")),
        vid=int(raw.get("vid", 0x0403)),
        pid=int(raw.get("pid", 0x6010)),
        baud=int(raw.get("baud", 115200)),
        settle_ms=int(raw.get("settle_ms", 400)),
        reply_timeout_ms=int(raw.get("reply_timeout_ms", 1000)),
        firmware=firmware,
        firmware_run=int(raw.get("firmware_run", 2)),
        firmware_device=str(raw.get("firmware_device", "GW1NR-9C")),
        firmware_cable=str(raw.get("firmware_cable", "USB Debugger A")),
        channel_order=order,
    )


def _parse_links(usb: dict) -> tuple[int, ...]:
    """[usb] links = ["10G", "5G"] (ordered passes); the older single
    min_link = "10G" still reads as one pass."""
    raw = usb.get("links")
    if raw is None:
        raw = [usb["min_link"]] if usb.get("min_link") else []
    return tuple(parse_link(x) for x in raw)


def _parse_plan(doc: dict, num_channels: int, samplerates_hz: list[int],
                capture: dict, problems: list, pid_for_log: str) -> list[PlanEntry]:
    """Parse [[plan]]. Falls back to wrapping any legacy [[capture.tests]]."""
    entries: list[PlanEntry] = []
    default_samples = str(capture.get("default_samples", "1M"))

    for i, raw in enumerate(doc.get("plan", [])):
        mode = int(raw.get("mode", i))
        configs = []
        for j, c in enumerate(raw.get("configs", [])):
            ch = int(c["channels"])
            rate = parse_rate(c["samplerate"])
            if ch > num_channels:
                problems.append(Problem(
                    "error", pid_for_log,
                    t("plan[{i}].configs[{j}].channels={ch} exceeds num_channels").format(
                        i=i, j=j, ch=ch)))
                continue
            if rate not in samplerates_hz:
                problems.append(Problem(
                    "error", pid_for_log,
                    t("plan[{i}].configs[{j}].samplerate={sr} not in the samplerates "
                      "list").format(i=i, j=j, sr=c["samplerate"])))
                continue
            configs.append(PlanConfig(
                channels=ch,
                samplerate_hz=rate,
                samples=str(c.get("samples", default_samples)),
                window_ms=float(c.get("window_ms", 20.0)),
                max_samples=int(c.get("max_samples", 8_000_000)),
                link=parse_link(c["link"]) if c.get("link") else None,
            ))
        if not configs:
            problems.append(Problem("warning", pid_for_log,
                                    t("plan[{i}] has no usable configs").format(i=i)))
        entries.append(PlanEntry(
            mode=mode,
            label=str(raw.get("label", "mode %d" % mode)),
            ready=bool(raw.get("ready", True)),
            note=str(raw.get("note", "")),
            checks=tuple(str(c) for c in raw.get("checks", ["freq"])),
            configs=tuple(configs),
        ))

    if not entries:
        # No [[plan]]: keep the legacy shape usable by treating every
        # [[capture.tests]] entry as a config of a single "no mode" plan entry.
        legacy = [PlanConfig(int(c["channels"]), parse_rate(c["samplerate"]),
                             str(c.get("samples", default_samples)), 20.0,
                             8_000_000)
                  for c in capture.get("tests", [])]
        if legacy:
            entries.append(PlanEntry(mode=-1, label="legacy capture",
                                     ready=True, note="",
                                     checks=("freq", "duty"),
                                     configs=tuple(legacy)))
    return entries


def _parse_threshold_groups(doc: dict, num_channels: int, default_v: float,
                            problems: list, pid_for_log: str) -> list[ThresholdGroup]:
    """Parse [[capture.threshold_groups]]."""
    out: list[ThresholdGroup] = []
    for i, raw in enumerate(doc.get("capture", {}).get("threshold_groups", [])):
        chans = tuple(int(c) for c in raw.get("channels", []))
        if not chans or any(c < 0 or c >= num_channels for c in chans):
            problems.append(Problem(
                "error", pid_for_log,
                t("capture.threshold_groups[{i}] has an out-of-range channel").format(i=i)))
            continue
        v = float(raw.get("voltage_threshold_v", default_v))
        out.append(ThresholdGroup(channels=chans, voltage_threshold_v=v,
                                  label=str(raw.get("label",
                                                    "%.1f V group" % v))))
    return out


def _parse_profile(product_dir: Path) -> tuple[ProductProfile | None, list[Problem]]:
    problems: list[Problem] = []
    pid_for_log = product_dir.name
    toml_file = product_dir / "product.toml"
    try:
        doc = tomllib.loads(toml_file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        return None, [Problem("error", pid_for_log, t("profile parse failed: {e}").format(e=e))]

    def err(msg: str) -> tuple[None, list[Problem]]:
        problems.append(Problem("error", pid_for_log, msg))
        return None, problems

    if doc.get("schema_version") != SCHEMA_VERSION:
        return err(t("schema_version should be {expected}, actual {actual!r}").format(
            expected=SCHEMA_VERSION, actual=doc.get('schema_version')))

    try:
        product = doc["product"]
        usb = doc["usb"]
        capture = doc["capture"]
        expected = doc.get("expected", {})
        firmware = doc.get("firmware", {})

        prod_id = str(product["id"])
        if prod_id != product_dir.name:
            return err(t("product.id ({pid}) does not match directory name ({dirname})").format(
                pid=prod_id, dirname=product_dir.name))

        dfu_pid = usb.get("dfu_pid")
        if dfu_pid is None:
            problems.append(Problem(
                "warning", prod_id,
                t("usb.dfu_pid not configured; DFU and one-click flow will be disabled")))

        num_channels = int(capture["num_channels"])
        channel_options = [int(c) for c in capture["channel_options"]]
        if any(c <= 0 or c > num_channels for c in channel_options):
            return err(t("channel_options {opts} exceed num_channels={n}").format(
                opts=channel_options, n=num_channels))

        samplerates_hz = sorted(parse_rate(r) for r in capture["samplerates"])

        default_channels = int(capture.get("default_channels", channel_options[-1]))
        if default_channels not in channel_options:
            return err(t("default_channels={dc} not in channel_options").format(dc=default_channels))
        limit = int(capture["max_bandwidth_mbps"]) * 1_000_000
        legal_defaults = [r for r in samplerates_hz
                          if default_channels * r // 8 <= limit]
        default_samplerate_hz = parse_rate(
            capture.get("default_samplerate", legal_defaults[-1] if legal_defaults else samplerates_hz[0]))
        if default_samplerate_hz not in legal_defaults:
            return err(t("default_samplerate={sr} is invalid for {dc}ch (not in the list or exceeds bandwidth)").format(
                sr=capture.get('default_samplerate'), dc=default_channels))

        tests = []
        for i, ct in enumerate(doc["capture"].get("tests", [])):
            ch = int(ct["channels"])
            rate = parse_rate(ct["samplerate"])
            if ch > num_channels:
                return err(t("capture.tests[{i}].channels={ch} exceeds num_channels").format(i=i, ch=ch))
            if rate not in samplerates_hz:
                return err(t("capture.tests[{i}].samplerate={sr} not in samplerates list").format(
                    i=i, sr=ct['samplerate']))
            tests.append(CaptureTest(ch, rate, str(ct.get("samples", capture.get("default_samples", "1M")))))
        if not tests and not doc.get("plan"):
            problems.append(Problem("warning", prod_id,
                                    t("capture.tests is empty; one-click flow has no test points")))

        max_bw = int(capture["max_bandwidth_mbps"])
        for ct in tests:
            if ct.channels * ct.samplerate_hz // 8 > max_bw * 1_000_000:
                return err(t("test point {ch}ch@{rate} exceeds bandwidth {bw}MB/s").format(
                    ch=ct.channels, rate=format_rate(ct.samplerate_hz), bw=max_bw))

        app_rel = firmware.get("app")
        app_firmware = (product_dir / app_rel).resolve() if app_rel else None

        # DFU<->APP 切换能力：产品自身是否支持 USB 控制传输 RECONFIG（"USB LA
        # 协议规范"0x30）；不支持则弹窗提示人工。
        ms_raw = doc.get("mode_switch", {})
        switch_usb_reconfig = bool(ms_raw.get("usb_reconfig", False))

        tmo = doc.get("timeouts", {})
        timeouts = Timeouts(
            wait_dfu_device_s=float(tmo.get("wait_dfu_device_s", 30)),
            wait_app_device_s=float(tmo.get("wait_app_device_s", 30)),
            dfu_flash_s=float(tmo.get("dfu_flash_s", 600)),
            capture_s=float(tmo.get("capture_s", 120)),
        )
        ota = _parse_ota(doc.get("ota", {}))

        default_vth = float(capture.get("voltage_threshold_v", 1.6))
        signal_source = _parse_signal_source(doc, product_dir, num_channels,
                                             problems, prod_id)
        plan = _parse_plan(doc, num_channels, samplerates_hz, capture,
                           problems, prod_id)
        threshold_groups = _parse_threshold_groups(doc, num_channels, default_vth,
                                                   problems, prod_id)
        if plan and signal_source.is_fpga and signal_source.firmware is None:
            problems.append(Problem(
                "warning", prod_id,
                t("signal_source.type is fpga but no firmware is declared; the "
                  "fixture cannot be reflashed by the tool")))

        profile = ProductProfile(
            id=prod_id,
            display_name=str(product["display_name"]),
            vid=int(usb["vid"]),
            app_pid=int(usb["app_pid"]),
            links=_parse_links(usb),
            dfu_pid=int(dfu_pid) if dfu_pid is not None else None,
            driver=str(capture["driver"]),
            num_channels=num_channels,
            max_bandwidth_mbps=max_bw,
            channel_options=channel_options,
            samplerates_hz=samplerates_hz,
            voltage_threshold_v=default_vth,
            default_samples=str(capture.get("default_samples", "1M")),
            default_channels=default_channels,
            default_samplerate_hz=default_samplerate_hz,
            capture_tests=tests,
            signal_source=signal_source,
            plan=plan,
            threshold_groups=threshold_groups,
            expected=ExpectedSignal(
                # Optional now: a fixture-based product gets its expectations from
                # patterns.py, so [expected] only carries the tolerances and the
                # freq/duty pair is left at zero. Legacy profiles that still drive
                # a single external source keep declaring them as before.
                freq_hz=float(expected.get("freq_hz", 0.0)),
                duty_pct=float(expected.get("duty_pct", 0.0)),
                freq_tol_pct=float(expected.get("freq_tol_pct", 5.0)),
                duty_tol_pp=float(expected.get("duty_tol_pp", 5.0)),
            ),
            app_firmware=app_firmware,
            app_flash_addr=int(firmware.get("app_flash_addr", 0)),
            verify_after_flash=bool(firmware.get("verify", True)),
            switch_usb_reconfig=switch_usb_reconfig,
            product_dir=product_dir,
            timeouts=timeouts,
            ota=ota,
        )
        # every link pass needs a config for every ready mode, or that pass would
        # silently test less than the others
        if signal_source.is_fpga:
            for link in profile.links:
                for e in plan:
                    if e.ready and e.mode >= 0 and not any(
                            c.link in (None, link) for c in e.configs):
                        problems.append(Problem(
                            "error", prod_id,
                            t("plan M{mode} has no config for the {link} pass").format(
                                mode=e.mode,
                                link=link_name(link))))
        return profile, problems
    except (KeyError, ValueError, TypeError) as e:
        kind = t("missing required field") if isinstance(e, KeyError) else t("invalid field")
        return err(f"{kind}: {e!r}")


def load_profiles(products_dir: Path = PRODUCTS_DIR) -> tuple[list[ProductProfile], list[Problem]]:
    """Load every resources/products/<id>/product.toml.  A broken product yields
    a Problem and is skipped without affecting other products."""
    profiles: list[ProductProfile] = []
    problems: list[Problem] = []
    if not products_dir.is_dir():
        return [], [Problem("error", None,
                            t("product profile directory does not exist: {d}").format(d=products_dir))]
    dirs = sorted(d for d in products_dir.iterdir()
                  if d.is_dir() and (d / "product.toml").is_file())
    if not dirs:
        return [], [Problem("error", None,
                            t("product profile directory is empty: {d}; please place <product_id>/product.toml").format(d=products_dir))]
    for d in dirs:
        profile, probs = _parse_profile(d)
        problems.extend(probs)
        if profile is not None:
            profiles.append(profile)
    # app_pid must be unique (it identifies the product).  dfu_pid is normally
    # per-product too (16U3=0x30F1, 32U3=0x30F2), but the check still tolerates
    # products sharing one dfu_pid; it only errors when an app_pid collides with
    # some product's dfu_pid.
    app_seen: dict[tuple[int, int], str] = {}
    dfu_pids = {(p.vid, p.dfu_pid): p.id for p in profiles if p.dfu_pid is not None}
    for p in profiles:
        key = (p.vid, p.app_pid)
        if key in app_seen:
            problems.append(Problem(
                "error", p.id,
                t("app VID/PID {vidpid} conflicts with product {other}").format(
                    vidpid=f"{key[0]:#06x}:{key[1]:#06x}", other=app_seen[key])))
        app_seen[key] = p.id
        if key in dfu_pids:
            problems.append(Problem(
                "error", p.id,
                t("app VID/PID {vidpid} conflicts with dfu_pid of product {other}").format(
                    vidpid=f"{key[0]:#06x}:{key[1]:#06x}", other=dfu_pids[key])))
    return profiles, problems


def check_resources(profiles: list[ProductProfile],
                    sigrok_bin: Path | None) -> list[Problem]:
    """Startup self-check for admin-placed resources."""
    problems: list[Problem] = []
    if sigrok_bin is None:
        problems.append(Problem(
            "error", None,
            t("sigrok-cli binary not found; place it in {d} with the platform-specific name (see resources/README.md); capture disabled").format(
                d=RESOURCES_DIR / 'bin')))
    for p in profiles:
        if p.app_firmware is None:
            problems.append(Problem("warning", p.id,
                                    t("firmware.app not configured; DFU and one-click flow disabled")))
        elif not p.app_firmware.is_file():
            problems.append(Problem(
                "warning", p.id,
                t("application firmware missing: {path}; DFU and one-click flow disabled").format(
                    path=p.app_firmware)))
    return problems


if __name__ == "__main__":
    # [ota] 解析自测：缺省回退默认、越界钳位、正常透传
    assert _parse_ota({}) == OtaParams()
    assert _parse_ota({"write_chunk": 9999}).write_chunk == 256   # 上限钳位
    assert _parse_ota({"write_chunk": 0}).write_chunk == 1        # 下限钳位
    assert _parse_ota({"read_chunk": 99999}).read_chunk == 512
    assert _parse_ota({"reset_settle_s": -5}).reset_settle_s == 0.0
    assert _parse_ota({"write_chunk": 128, "read_chunk": 256}) == \
        OtaParams(write_chunk=128, read_chunk=256)
    print("[ota] 解析自测 PASS")

    profiles, problems = load_profiles()
    print(f"== 加载 {len(profiles)} 个产品档案 ==")
    for p in profiles:
        dfu = f"{p.dfu_pid:#06x}" if p.dfu_pid is not None else "未配置"
        print(f"\n{p.display_name} ({p.id})")
        print(f"  USB: {p.vid:#06x} app={p.app_pid:#06x} dfu={dfu}")
        print(f"  {p.num_channels}ch, {p.max_bandwidth_mbps}MB/s, unitsize={p.unitsize}")
        print(f"  采样率: {', '.join(format_rate(r) for r in p.samplerates_hz)}")
        for ch in p.channel_options:
            print(f"  {ch:>2}ch 合法档位: {', '.join(format_rate(r) for r in p.legal_rates(ch))}")
        print(f"  测试点: {', '.join(f'{t.channels}ch@{format_rate(t.samplerate_hz)}' for t in p.capture_tests)}")
        print(f"  期望: {p.expected.freq_hz/1e6:g}MHz ±{p.expected.freq_tol_pct}%, "
              f"{p.expected.duty_pct}% ±{p.expected.duty_tol_pp}pp")
        print(f"  固件: {p.app_firmware} @ {p.app_flash_addr:#x}")
        sw = ["USB RECONFIG"] if p.switch_usb_reconfig else []
        sw.append("弹窗人工")
        print(f"  模式切换: {' -> '.join(sw)}")
    print(f"\n== {len(problems)} 个问题 ==")
    for prob in problems:
        print(f"  {prob}")
    sys.exit(1 if any(p.severity == "error" for p in problems) else 0)
