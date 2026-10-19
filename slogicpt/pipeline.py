"""Production-test pipeline engine.

Full flow:  eFuse ensure (write+lock the AES key if unlocked -- an encrypted
DFU bitstream cannot boot without it) -> blank-flash the DFU image ->
wait DFU device -> flash app firmware (+verify) -> wait APP device ->
capture+verify each configured test point -> PASS/FAIL summary.

Mode-switch policy (per user decision): after each flashing stage, wait for
the target PID; on timeout, raise a non-blocking operator prompt ("please
replug") and KEEP polling until the device shows up or the run is cancelled.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import device_watch
from . import flasher
from . import mode_switch as mode_switch_mod
from . import programmer as programmer_mod
from . import waveform
from .i18n import t
from .procutil import kill_image
from .profiles import OUTPUT_DIR, ProductProfile, format_rate
from .sigrok import CaptureError, SigrokCli


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PipelineCallbacks:
    on_log: Callable[[str], None] = lambda s: None
    on_step: Callable[[str, StepStatus], None] = lambda name, st: None
    on_user_prompt: Callable[[str], None] = lambda s: None
    on_user_prompt_clear: Callable[[], None] = lambda: None
    # 模式切换只剩人工方案（按板载 MODE 键）时的弹窗提示（GUI 弹 QMessageBox）
    on_manual_switch: Callable[[str], None] = lambda s: None
    on_finished: Callable[[bool, str], None] = lambda ok, report: None


@dataclass
class StepDef:
    id: str                     # stable id, GUI step rows key on this
    label: str                  # operator-facing name
    run: Callable[[], bool]     # True = passed
    abort_on_fail: bool = True  # capture steps keep going to aid diagnosis


# Failures that a second attempt usually clears, because the cause is the previous
# capture's process rather than the board.
_TRANSIENT = (
    "libusb_error_access",
    "no devices found",
    "access is denied",
    "resource busy",
    "failed to open device",
)


def _looks_transient(message: str) -> bool:
    low = message.lower()
    return any(s in low for s in _TRANSIENT)


class PipelineAbort(Exception):
    pass


# Step-id -> English label. Shared by sequence_plan() and _sequence_defs() so
# the two step lists never drift; each label is wrapped in t() at use sites.
STEP_LABELS: dict[str, str] = {
    "blank:efuse": "Blank-flash · eFuse key write-lock",
    "blank:flash": "Blank-flash · write DFU image",
    "wait_dfu": "Wait for DFU device",
    "flash_app": "DFU flash app firmware",
    "switch_app": "Switch to APP mode",
    "wait_app": "Wait for APP device",
    "switch_dfu": "Switch to DFU mode",
    "signal:flash": "Fixture · flash signal generator",
    "link:check": "Check USB link",
}

# Production is split into stations; this tool runs two of them (app-firmware
# OTA is done by a separate batch web tool between the two):
#   jtag -- blank board on the JTAG programmer: eFuse AES key + DFU base image;
#   test -- board running its app firmware: link gate, then the signal tests.
STATIONS = ("jtag", "test")
STATION_LABELS = {"jtag": "JTAG flashing", "test": "Production test"}


def _station_spec(profile: ProductProfile, station: str
                  ) -> list[tuple[str, str, str, tuple, bool]]:
    """(step id, label, Pipeline method, method args, abort_on_fail) for one
    station.  The ONLY definition of a station's steps: sequence_plan() (the
    GUI's rows) and Pipeline._sequence_defs() (what runs) both read it, so the
    two can never drift apart -- that drift once left the engine running 17
    steps while the GUI showed 6."""
    if station not in STATIONS:
        raise ValueError(t("Unknown station: {s}").format(s=station))
    spec: list[tuple[str, str, str, tuple, bool]] = []
    if station == "jtag":
        prog = profile.programmer
        if prog is not None and prog.flash is not None:
            # 加密 DFU 位流须先把 AES 密钥写入 eFuse 才能启动，故 eFuse 写锁是
            # 烧 DFU 底包的前置步骤：未锁 -> 写入并锁定；已锁 -> 跳过。
            if prog.efuse_key_file is not None:
                spec.append(("blank:efuse", t(STEP_LABELS["blank:efuse"]),
                             "_efuse_ensure", (), True))
            spec.append(("blank:flash", t(STEP_LABELS["blank:flash"]),
                         "_flash_blank", (), True))
        return spec

    # test station: one pass per link in [usb] links, e.g. 32U3 = 10G then 5G,
    # so the board is proven on both links a user may plug it into.  The first
    # link is a gate that aborts the run (a board that cannot reach 10G is a
    # FAIL without testing); each later pass asks the operator to move the
    # cable to that port and waits for the board to come back at that speed.
    links = profile.links or (None,)
    for n, link in enumerate(links):
        name = _link_name(link)
        sfx = f"@{name}" if link is not None else ""
        if n == 0:
            spec.append((f"link:check{sfx}",
                         t("Check USB link ({link})").format(link=name) if link
                         else t(STEP_LABELS["link:check"]),
                         "_check_link", (link,), True))
        else:
            spec.append((f"link:replug{sfx}",
                         t("Re-plug into a {link} port").format(link=name),
                         "_await_link", (link,), True))
        if profile.signal_source.is_fpga:
            # reload the fixture each pass: moving the cable can disturb its SRAM
            spec.append((f"signal:flash{sfx}", t(STEP_LABELS["signal:flash"]),
                         "_signal_flash", (), False))
            if profile.plan:
                spec.append((f"signal:all{sfx}",
                             t("Signal generator · all {n} patterns").format(
                                 n=len(profile.plan)) + (f" ({name})" if link else ""),
                             "_run_all_modes", (link,), False))
        elif n == 0:
            # Legacy path: a profile with no [[plan]] drives an external fixed
            # source; it is not link-specific, so it runs once.
            for i, ct in enumerate(profile.capture_tests):
                spec.append((f"capture:{i}",
                             t("Capture {ch}ch@{rate}").format(
                                 ch=ct.channels, rate=format_rate(ct.samplerate_hz)),
                             "_capture_test", (i,), False))
    return spec


def _link_name(link: int | None) -> str:
    return device_watch.link_name(link) if link else "-"


def sequence_plan(profile: ProductProfile, station: str) -> list[tuple[str, str]]:
    """(id, label) of a station's sequence, without a Pipeline instance -- the
    GUI builds its step list from this so ids always match."""
    return [(sid, label) for sid, label, *_ in _station_spec(profile, station)]


class Pipeline:
    def __init__(self, profile: ProductProfile, sigrok: SigrokCli | None,
                 callbacks: PipelineCallbacks, steps: list[StepDef]):
        self.profile = profile
        self.sigrok = sigrok
        self.cb = callbacks
        self.steps = steps
        self.cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.report_lines: list[str] = []
        self.app_serial: str | None = None   # APP USB SerialNumber (version id)
        # (mode label, channel text, check, measured, expected, passing channels)
        self.failures: list[tuple[str, str, str, str, str, str]] = []
        # 结构化结果（每个「模式 x 配置」一条），供**独立的测试工具**直接渲染界面。
        # 纯附加：产测引擎自己不读它，行为完全不变。有它就不必去解析 report_lines
        # 里那些本地化文案（"M4 32ch@200M: PASS (32 ok / 0 fail / 0 skip)"）。
        self.mode_results: list[dict] = []

    # ---- construction helpers -------------------------------------------

    def _sequence_defs(self, station: str) -> list[StepDef]:
        return [StepDef(sid, label,
                        (lambda m=getattr(self, meth), a=args: m(*a)),
                        abort_on_fail=abort)
                for sid, label, meth, args, abort in _station_spec(self.profile, station)]

    def _capture_test(self, i: int) -> bool:
        ct = self.profile.capture_tests[i]
        return self._capture_one(ct.channels, ct.samplerate_hz, ct.samples,
                                 self.profile.voltage_threshold_v, None)

    @classmethod
    def for_station(cls, profile: ProductProfile, station: str,
                    sigrok: SigrokCli | None, callbacks: PipelineCallbacks, *,
                    cable_index: int | None = None,
                    all_profiles: list[ProductProfile] | None = None) -> "Pipeline":
        """Every step of one station for the board that was just detected.
        `all_profiles` lets link:check see analyzers of OTHER products too
        (sigrok-cli cannot pick one device out of several)."""
        p = cls(profile, sigrok, callbacks, [])
        p._cable_index = cable_index
        p._all_profiles = all_profiles
        p.steps = p._sequence_defs(station)
        return p

    @classmethod
    def single_step(cls, profile: ProductProfile, sigrok: SigrokCli | None,
                    callbacks: PipelineCallbacks, step_id: str, station: str, *,
                    cable_index: int | None = None,
                    all_profiles: list[ProductProfile] | None = None) -> "Pipeline":
        """Manual mode: run exactly one of a station's steps by id."""
        p = cls(profile, sigrok, callbacks, [])
        p._cable_index = cable_index
        p._all_profiles = all_profiles
        matches = [d for d in p._sequence_defs(station) if d.id == step_id]
        if not matches:
            raise ValueError(t("Unknown step: {step_id}").format(step_id=step_id))
        p.steps = matches
        return p

    @classmethod
    def capture_only(cls, profile: ProductProfile, sigrok: SigrokCli,
                     callbacks: PipelineCallbacks, *,
                     channels: int, samplerate_hz: int, samples: str,
                     voltage_threshold_v: float,
                     expected_rows: list[tuple[float, float]] | None = None
                     ) -> "Pipeline":
        """Custom capture+verify with operator-tweaked parameters."""
        p = cls(profile, sigrok, callbacks, [])
        label = t("Custom capture {ch}ch@{rate}").format(
            ch=channels, rate=format_rate(samplerate_hz))
        p.steps = [StepDef("capture:custom", label, lambda: p._capture_one(
            channels, samplerate_hz, samples, voltage_threshold_v, expected_rows),
            abort_on_fail=False)]
        return p

    @classmethod
    def reflash(cls, profile: ProductProfile, callbacks: PipelineCallbacks,
                firmware_path=None, cable_index: int | None = None) -> "Pipeline":
        """返修复烧：（若设备在 APP 模式则）切回 DFU -> 等待 DFU 设备（超时提示
        人工操作）-> 重写应用固件 -> 切换到 APP -> 等待应用模式回归。"""
        p = cls(profile, None, callbacks, [])
        p._firmware_override = firmware_path
        p._cable_index = cable_index
        p.steps = [StepDef("switch_dfu", t(STEP_LABELS["switch_dfu"]), p._switch_to_dfu),
                   StepDef("wait_dfu", t(STEP_LABELS["wait_dfu"]), p._wait_dfu),
                   StepDef("flash_app", t(STEP_LABELS["flash_app"]), p._flash_app),
                   StepDef("switch_app", t(STEP_LABELS["switch_app"]), p._switch_to_app),
                   StepDef("wait_app", t(STEP_LABELS["wait_app"]), p._wait_app)]
        return p

    @classmethod
    def switch_mode(cls, profile: ProductProfile,
                    callbacks: PipelineCallbacks, *, to_mode: str,
                    cable_index: int | None = None) -> "Pipeline":
        """辅助操作：在 DFU（烧录模式）与 APP（应用模式）之间手动切换当前
        设备，切换后等待目标模式就绪。to_mode='app' 即 DFU->APP，'dfu' 即
        APP->DFU（方向由 GUI 依当前设备模式决定）。"""
        p = cls(profile, None, callbacks, [])
        p._cable_index = cable_index
        if to_mode == "app":
            p.steps = [StepDef("switch_app", t(STEP_LABELS["switch_app"]), p._switch_to_app),
                       StepDef("wait_app", t(STEP_LABELS["wait_app"]), p._wait_app)]
        else:
            p.steps = [StepDef("switch_dfu", t(STEP_LABELS["switch_dfu"]), p._switch_to_dfu),
                       StepDef("wait_dfu", t(STEP_LABELS["wait_dfu"]), p._wait_dfu)]
        return p

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request_cancel(self) -> None:
        self.cancel.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        all_pass = True
        try:
            for step in self.steps:
                self.cb.on_step(step.id, StepStatus.RUNNING)
                if self.cancel.is_set():
                    raise PipelineAbort(t("Cancelled"))
                ok = step.run()
                self.cb.on_step(step.id,
                                StepStatus.PASSED if ok else StepStatus.FAILED)
                if not ok:
                    all_pass = False
                    if step.abort_on_fail:
                        raise PipelineAbort(
                            t("Step \"{label}\" failed").format(label=step.label))
        except PipelineAbort as e:
            all_pass = False
            self._log(t("Flow aborted: {e}").format(e=e))
            self.report_lines.append(t("Flow aborted: {e}").format(e=e))
        except Exception:
            all_pass = False
            self._log(t("Flow exception:") + "\n" + traceback.format_exc())
            self.report_lines.append(t("Flow exception, see log for details"))
        finally:
            # Release the fixture's serial port. Without this, a single-step run
            # leaves the port open and the NEXT step cannot open it:
            #   PermissionError(13, 'Access is denied') on COM30
            # which the operator sees as "no signal generator" from then on.
            self._signal_close()
            self.cb.on_user_prompt_clear()

            # End with "which wire, in which mode" rather than leaving the operator
            # to find it among the per-mode lines and the capture noise.
            #
            # Every failing verdict was recorded as it happened, with the channel
            # text the check itself produced: a per-channel failure names one input
            # ("CH7"), while a structured one names the wires it spans ("CH4..CH7"
            # for an SPI master, "CH24/CH25" for I2C). That is the unit the operator
            # has to go and look at, so it is what the summary leads with.
            if self.failures:
                lines = [t("---- failing channels ({n}) ----").format(
                    n=len(self.failures))]
                for mode, where, kind, got, want, ok_in in self.failures:
                    lines.append("  %-14s %-8s %s" % (where, kind, mode))
                    lines.append("      %-6s %s" % (t("got"), got))
                    lines.append("      %-6s %s" % (t("want"), want))
                    if ok_in:
                        # A group verdict names several wires, and some of them
                        # usually measured fine. Without this line "CH24/CH25 i2c
                        # FAIL" reads as two suspect wires; with it, the reader
                        # sees CH24 passed its own 100 kHz check and only CH25 is
                        # actually in question.
                        lines.append("      %-6s %s" % (t("ok in group"), ok_in))
                for ln in lines:
                    self._log(ln)
                self.report_lines.extend(lines)

            lines = list(self.report_lines)
            if self.app_serial:
                lines.insert(0, f"APP SN: {self.app_serial}")
            self.cb.on_finished(all_pass, "\n".join(lines))

    # ---- steps -----------------------------------------------------------

    def _log(self, msg: str) -> None:
        self.cb.on_log(msg)

    def _note_mode_result(self, entry, *, config: str = "-", ok: bool = True,
                          passed: int = 0, failed: int = 0, skipped: int = 0,
                          verdicts=None, note: str = "",
                          skip: bool = False) -> None:
        """追加一条结构化结果到 `mode_results`（供独立测试工具渲染界面）。

        纯附加信息：产测引擎自己不读它，行为与判定完全不变。
        """
        self.mode_results.append({
            "mode": entry.mode,
            "mode_label": entry.label,
            "config": config,
            "ok": bool(ok),
            "skip": bool(skip),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "verdicts": list(verdicts or []),
            "note": note,
        })

    _cable_index = None         # external-programmer cable index (from the GUI probe)
    _all_profiles = None        # every product profile (link:check sees all analyzers)
    _link = None                # link of the current test pass (picks the plan configs)
    _image_override = None      # GUI 会话级：覆盖 DFU 镜像路径
    _efuse_key_override = None  # GUI 会话级：覆盖 eFuse 密钥路径

    # ---- fixture USB presence -------------------------------------------

    @staticmethod
    def _fixture_ports() -> list[str]:
        """夹具 Tang Nano 的串口（按 BL702 序列号认出，见 fpga.fixture_ports）。"""
        from .fpga import fixture_ports
        return fixture_ports()

    def _ensure_fixture(self, plugged: bool) -> bool:
        """确保夹具处于插入 / 拔出的状态，需要时提示操作员并等待。

        为什么需要这个：`programmer_cli` 选线缆只能按**枚举序号**（--scan-cables
        的 SN 是 null），而夹具的 BL702 和本产品的烧录器都是 FTDI 0403:6010。
        夹具一插上，所有候选序号都落到夹具上，eFuse 那一步就报 IDCODE 不符并中止；
        而序号还会随着任何 USB 插拔而移动，所以它无法靠配置绕开。

        实测：夹具插着时 --cable-index 0..9 无一能读到本产品；拔掉后同一条命令
        立刻读到 0x0001481B。

        这里不弹"请点继续"，而是**直接轮询等到操作员真的做完** —— 少一次点击，
        也不会出现"提示还在、人已经点过了"的错位。
        """
        want = plugged
        ports = self._fixture_ports()
        if bool(ports) == want:
            return True

        if want:
            msg = t("Please PLUG IN the signal-generator fixture (Tang Nano 9K) "
                    "and wait -- the flow continues by itself")
        else:
            msg = t("Please UNPLUG the signal-generator fixture (Tang Nano 9K) "
                    "and wait -- it shares the FTDI ID with the board's "
                    "programmer, so this step cannot address the board while the "
                    "fixture is connected")
        self._log("[fixture] " + msg)
        self.cb.on_user_prompt(msg)
        try:
            waited = 0.0
            while not self.cancel.is_set():
                ports = self._fixture_ports()
                if bool(ports) == want:
                    self._log("[fixture] " + (
                        t("Fixture detected ({p}), continuing").format(
                            p=", ".join(ports)) if want else
                        t("Fixture unplugged, continuing")))
                    return True
                time.sleep(0.5)
                waited += 0.5
                # 每 30 秒重述一次，避免操作员以为卡住了。用 int(waited) 时
                # waited=0.5 会让 int()=0 也满足 %30==0，于是刚提示完就马上又打
                # 一条 "still waiting (0s)"，看起来像出错。
                if waited >= 30 and int(waited) % 30 == 0:
                    self._log("[fixture] " + t("still waiting ({s}s): {m}").format(
                        s=int(waited), m=msg))
            return False
        finally:
            self.cb.on_user_prompt_clear()

    def _efuse_ensure(self) -> bool:
        """烧空板前置：加密 DFU 位流须以 eFuse 中的 AES 密钥启动（write），
        lock 防止密钥被读出。已锁 -> 密钥已在，跳过；未锁 -> 写入并锁定
        （不可逆），随后回读锁定位校验。"""
        # 必须先让夹具让开：这一步走 programmer_cli，而它只能按枚举序号选线缆，
        # 夹具插着时所有序号都落到夹具上（详见 _ensure_fixture）。
        if not self._ensure_fixture(False):
            self.report_lines.append(t("eFuse write-lock") + ": FAIL")
            return False
        prog = self.profile.programmer
        state, cable = programmer_mod.efuse_state(
            prog, self._cable_index, log_cb=self._log, cancel=self.cancel)
        if cable is not None:
            self._cable_index = cable   # 后续烧写步骤复用同一 cable
        self._log(f"[criteria] eFuse: pre-state={state}; rule: locked->skip, "
                  f"unlocked->write+lock, written->skip, unknown->abort; "
                  f"success = readback state == locked (expect Device Locked)")
        if state == "locked":
            self._log(t("eFuse already locked (key written), skipping write-lock"))
            self.report_lines.append(
                t("eFuse write-lock") + ": SKIP" + t(" (already locked)"))
            return True
        if state == "written":
            # 已有密钥却没锁：再写只会多烧位、必然校验失败。不写，继续烧 DFU——
            # 密钥若不对，产测工位会发现板子起不来，到时再记录异常。
            self._log(t("eFuse key already written but not locked; not writing it "
                        "again (irreversible), continuing with the DFU image. If the "
                        "key is wrong the board will not boot at the test station"))
            self.report_lines.append(
                t("eFuse write-lock") + ": SKIP" + t(" (key already written, not locked)"))
            return True
        if state != "unlocked":
            self._log(t("Cannot confirm eFuse state (external programmer not "
                        "connected / cable fault?), aborting"))
            self.report_lines.append(
                t("eFuse write-lock") + ": FAIL" + t(" (state unknown)"))
            return False
        self._log(t("eFuse not locked: writing AES key and locking (prerequisite "
                    "for encrypted DFU boot, irreversible)..."))
        if not programmer_mod.efuse_lock(
                prog, self._cable_index, key_file=self._efuse_key_override,
                log_cb=self._log, cancel=self.cancel):
            self.report_lines.append(
                t("eFuse write-lock") + ": FAIL" + t(" (write failed)"))
            return False
        # 回读校验：写锁后 --keyread 应报 Device Locked
        state2, _ = programmer_mod.efuse_state(
            prog, self._cable_index, log_cb=self._log, cancel=self.cancel)
        ok = state2 == "locked"
        self._log(f"[criteria] eFuse readback: got={state2}, expect=locked "
                  f"-> {'OK' if ok else 'FAIL'}")
        self.report_lines.append(
            t("eFuse write-lock") + ": "
            + ("OK" + t(" (readback confirmed locked)") if ok
               else "FAIL" + t(" (readback did not confirm locked)")))
        if not ok:
            self._log(t("eFuse readback did not confirm locked, judged as failed"))
        return ok

    def _flash_blank(self) -> bool:
        op = self.profile.programmer.flash
        img = self._image_override if self._image_override is not None else op.image
        # 这一步本身用 --ftdi-serial 选设备、夹具插着也没事，但如果上一步是
        # eFuse（要拔夹具），这里再确认一次可以让"跳过 eFuse"的档案也走到同样的
        # 提示 —— 反正夹具已经在外面时它直接返回，不打扰操作员。
        if not self._ensure_fixture(False):
            self.report_lines.append(t("Blank-flash DFU image") + ": FAIL")
            return False
        self._log(f"[criteria] blank-flash: image={img.name} @ {op.spiaddr:#x}; "
                  f"success = openFPGALoader read-back verify OK (see its decision "
                  f"line), or Gowin 'Finished' with no Error")
        ok = programmer_mod.flash(
            self.profile.programmer, self._cable_index, image=self._image_override,
            log_cb=self._log, cancel=self.cancel)
        self.report_lines.append(
            t("Blank-flash DFU image") + ": " + ("OK" if ok else "FAIL")
            + f" ({img.name} @ {op.spiaddr:#x})")
        return ok

    def _check_link(self, link: int | None) -> bool:
        """Test-station gate: exactly one analyzer on the host, it is THIS
        product running its app firmware, and its USB link reached the
        product's minimum ([usb] min_link, e.g. 10G).  Fails -- and the station
        aborts the run -- otherwise, so a slow link is never "tested".

        Exactly one, because sigrok-cli cannot choose between several SLogic
        analyzers (the driver rejects conn=) and would capture from any of them."""
        p = self.profile
        devs = device_watch.scan_devices(self._all_profiles or [p])
        apps = [d for d in devs if d.mode is device_watch.Mode.APP]
        mine = [d for d in apps if d.profile.id == p.id]
        need = link
        need_txt = device_watch.link_name(need) if need else "-"
        self._log(f"[criteria] link: exactly 1 analyzer online, {p.display_name} in "
                  f"APP mode, link >= {need_txt}; seen: "
                  + (", ".join(str(d) for d in devs) or "none"))

        def fail(why: str) -> bool:
            self._log(why)
            self.report_lines.append(t("USB link") + ": FAIL (" + why + ")")
            return False

        if len(apps) > 1:
            return fail(t("{n} analyzers online; keep only the board under test "
                          "connected").format(n=len(apps)))
        if not mine:
            if any(d.profile.id == p.id and d.mode is device_watch.Mode.DFU
                   for d in devs):
                return fail(t("{name} is in DFU mode: the app firmware has not been "
                              "flashed").format(name=p.display_name))
            return fail(t("No {name} running its app firmware was found").format(
                name=p.display_name))
        d = mine[0]
        self.app_serial = d.serial
        speed = d.speed or self._sigrok_link_speed()
        got_txt = device_watch.link_name(speed)
        if need and (speed or 0) < need:
            return fail(t("link too slow: {got}, needs {need} (check the cable and "
                          "the host port)").format(got=got_txt, need=need_txt))
        self._log(t("USB link {got} (needs {need}): OK").format(got=got_txt, need=need_txt))
        self.report_lines.append(t("USB link") + f": PASS ({got_txt})")
        return True

    def _await_link(self, link: int) -> bool:
        """A later link pass: ask the operator to move the cable to a `link`
        port, then wait (until cancelled) for exactly one of this product's
        boards to come back in APP mode at EXACTLY that speed -- a 10G link
        does not test 5G.  There is no firmware command to renegotiate a slower
        link, so the re-plug is manual; the run continues by itself."""
        p = self.profile
        want = device_watch.link_name(link)
        self._log(f"[criteria] link pass: exactly 1 {p.display_name} in APP mode at {want}")
        self.cb.on_user_prompt(t("Move the board's USB cable to a {link} port; the "
                                 "test continues when it is detected").format(link=want))
        last = None
        try:
            while not self.cancel.is_set():
                devs = device_watch.scan_devices(self._all_profiles or [p])
                apps = [d for d in devs if d.mode is device_watch.Mode.APP]
                mine = [d for d in apps if d.profile.id == p.id]
                seen = ", ".join(str(d) for d in devs) or "none"
                if seen != last:
                    self._log(t("Waiting for {link}: seen {devs}").format(link=want, devs=seen))
                    last = seen
                if len(apps) == 1 and mine and (mine[0].speed or
                                                self._sigrok_link_speed()) == link:
                    self._log(t("USB link {got} (needs {need}): OK").format(got=want, need=want))
                    self.report_lines.append(t("USB link") + f": PASS ({want})")
                    return True
                time.sleep(0.5)
            return False
        finally:
            self.cb.on_user_prompt_clear()

    def _sigrok_link_speed(self) -> int | None:
        """Link speed from sigrok's scan line ("... - (10G) Sipeed SLogic32 U3"),
        for hosts whose libusb does not report it (old Windows libusb DLLs)."""
        if self.sigrok is None:
            return None
        import re
        try:
            lines = self.sigrok.scan(self.profile.driver)
        except Exception:                                       # noqa: BLE001
            return None
        for line in lines:
            m = re.search(r"\((USB2|5G|10G|20G)\)", line)
            if m:
                from .profiles import parse_link
                return parse_link(m.group(1))
        return None

    def _wait_mode(self, pid: int, timeout_s: float, what: str) -> bool:
        self._log(t("Waiting for {what} device ({vid}:{pid})...").format(
            what=what, vid=f"{self.profile.vid:#06x}", pid=f"{pid:#06x}"))
        self._log(f"[criteria] wait {what}: success = USB "
                  f"{self.profile.vid:#06x}:{pid:#06x} enumerated within "
                  f"{timeout_s:.0f}s (then prompt+poll until replugged)")

        def ready(manual: bool) -> bool:
            self._log(t("{what} device ready").format(what=what)
                      + (t(" (after manual intervention)") if manual else ""))
            self._note_serial(pid, what)
            return True

        if device_watch.wait_for_pid(self.profile.vid, pid, timeout_s,
                                     cancel=self.cancel):
            return ready(False)
        if self.cancel.is_set():
            return False
        # timeout -> prompt operator, keep polling indefinitely until cancel
        self.cb.on_user_prompt(
            t("Waiting for {what} device timed out -- please replug").format(what=what))
        self.cb.on_step(f"wait_{what.lower()}", StepStatus.WAITING_USER)
        try:
            while not self.cancel.is_set():
                if device_watch.find_pid(self.profile.vid, pid):
                    return ready(True)
                time.sleep(0.5)
            return False
        finally:
            self.cb.on_user_prompt_clear()

    def _note_serial(self, pid: int, what: str) -> None:
        """Log the APP device's USB SerialNumber (version identifier); it is
        also prepended to the final report."""
        if what != "APP":
            return
        sn = device_watch.read_serial(self.profile.vid, pid)
        self.app_serial = sn
        self._log(t("APP SerialNumber: {sn} (for version identification/"
                    "distinction)").format(sn=sn) if sn
                  else t("APP device provided no SerialNumber descriptor"))

    def _switch_to_app(self) -> bool:
        """DFU->APP：向 DFU 设备触发切换（真正就绪由随后的 wait_app 判定）。"""
        if self.profile.dfu_pid is None:
            self._log(t("dfu_pid not configured, cannot switch to APP"))
            return False
        return self._switch(from_pid=self.profile.dfu_pid, to_what="APP")

    def _switch_to_dfu(self) -> bool:
        """APP->DFU（复烧前置）：若设备当前在 APP 模式则触发切回 DFU；否则跳过，
        交由 wait_dfu 处理（人工插拔/上电）。"""
        if device_watch.find_pid(self.profile.vid, self.profile.app_pid):
            return self._switch(from_pid=self.profile.app_pid, to_what="DFU")
        self._log(t("No APP device detected, skipping auto-switch, waiting for DFU directly"))
        return True

    def _switch(self, from_pid: int, to_what: str) -> bool:
        """DFU<->APP 切换，按可用方案依次尝试：
        1. usb_reconfig（product.toml 声明的产品自身能力）：USB 控制传输
           RECONFIG，无需外置硬件；
        2. programmer.switch（programmer.toml 声明的保底方案）：经外置烧录器
           CLI 执行；
        3. 都不可用/都失败 -> 弹窗提示按板载 MODE 按键，随后的等待步骤
        （含超时人工提示）确认真正就绪。"""
        p = self.profile
        direction = "dfu2app" if to_what == "APP" else "app2dfu"
        if p.switch_usb_reconfig:
            self._log(t("Sending RECONFIG to switch to {mode} mode ({vid}:{pid})...").format(
                mode=to_what, vid=f"{p.vid:#06x}", pid=f"{from_pid:#06x}"))
            try:
                mode_switch_mod.reconfig(p.vid, from_pid, self._log)
                return True
            except mode_switch_mod.ModeSwitchError as e:
                self._log(t("RECONFIG switch failed: {e}, trying fallback...").format(e=e))
        prog = p.programmer
        if prog is not None and direction in prog.switch:
            if programmer_mod.switch(prog, direction,
                                     cable_index=self._cable_index,
                                     log_cb=self._log, cancel=self.cancel):
                return True
            self._log(t("External programmer switch failed, falling back to manual..."))
        # 只剩硬件操作：弹窗提示工人按板载 MODE 键，等待步骤兜底确认
        msg = t("Please press the onboard MODE button to switch {name} to {mode} "
                "mode; it will continue automatically once the target-mode device "
                "is detected").format(name=p.display_name, mode=to_what)
        self._log(msg)
        self.cb.on_manual_switch(msg)
        return True

    def _wait_dfu(self) -> bool:
        if self.profile.dfu_pid is None:
            self._log(f"{self.profile.display_name}: " + t("dfu_pid not configured"))
            return False
        return self._wait_mode(self.profile.dfu_pid,
                               self.profile.timeouts.wait_dfu_device_s, "DFU")

    def _wait_app(self) -> bool:
        return self._wait_mode(self.profile.app_pid,
                               self.profile.timeouts.wait_app_device_s, "APP")

    _firmware_override = None

    def _flash_app(self) -> bool:
        fw = self._firmware_override or self.profile.app_firmware
        if fw is None or self.profile.dfu_pid is None:
            self._log(t("Firmware or dfu_pid not configured, cannot flash"))
            return False
        self._log(f"[criteria] DFU flash: target {self.profile.vid:#06x}:"
                  f"{self.profile.dfu_pid:#06x} @ {self.profile.app_flash_addr:#x}; "
                  f"success = no USB/DFU error"
                  + (", read-back verify on" if self.profile.verify_after_flash
                     else ", verify OFF"))
        o = self.profile.ota
        self._log(f"[ota] write_chunk={o.write_chunk} read_chunk={o.read_chunk} "
                  f"usb_timeout_ms={o.usb_timeout_ms} wip_timeout_s={o.wip_timeout_s} "
                  f"reset_settle_s={o.reset_settle_s}")
        try:
            flasher.flash_app_firmware(
                vid=self.profile.vid, pid=self.profile.dfu_pid,
                addr=self.profile.app_flash_addr, firmware=fw,
                verify=self.profile.verify_after_flash, ota=self.profile.ota,
                log_cb=self._log, cancel=self.cancel,
                app_pid=self.profile.app_pid)
            self.report_lines.append(t("DFU flash") + f": OK ({fw})")
            return True
        except flasher.FlashError as e:
            self._log(str(e))
            self.report_lines.append(t("DFU flash") + f": FAIL ({e})")
            return False

    def _capture_one(self, channels: int, samplerate_hz: int, samples: str,
                     voltage_threshold_v: float,
                     expected_rows: list[tuple[float, float]] | None) -> bool:
        assert self.sigrok is not None
        label = f"{channels}ch@{format_rate(samplerate_hz)}"
        out_file = OUTPUT_DIR / f"{self.profile.id}_{channels}ch_{format_rate(samplerate_hz)}_wave.bin"
        self._log(t("== Capture {label} ({samples} samples) ==").format(
            label=label, samples=samples))
        # sigrok-cli refuses to capture when its scan matches several
        # devices, and this driver build does not implement the `conn`
        # selector yet ("Not supported now!") -- so detect the ambiguity
        # up front and tell the operator exactly what to do.
        found = self.sigrok.scan(self.profile.driver)
        if len(found) > 1:
            msg = (t("Multiple SLogic devices detected online at once; the current "
                     "sigrok driver does not yet support selecting a device, please "
                     "keep only the device under test and retry:")
                   + "\n  " + "\n  ".join(found))
            self._log(msg)
            self.report_lines.append(
                f"{label}: FAIL (" + t("multiple-device ambiguity") + ")")
            return False
        try:
            result = self.sigrok.capture(
                driver=self.profile.driver, channels=channels,
                samplerate_hz=samplerate_hz, samples=samples,
                voltage_threshold_v=voltage_threshold_v, out_file=out_file,
                timeout_s=self.profile.timeouts.capture_s,
                log_cb=self._log, cancel=self.cancel)
        except CaptureError as e:
            self._log(t("Capture failed: {e}").format(e=e))
            self.report_lines.append(
                f"{label}: FAIL (" + t("Capture failed: {e}").format(e=e) + ")")
            return False

        self._log(t("Capture done: {n} samples, {sec}s -> {file}").format(
            n=result.n_samples, sec=f"{result.elapsed_s:.2f}",
            file=result.out_file.name))
        chans = waveform.load_capture_file(result.out_file, channels, result.unitsize)
        e = self.profile.expected
        if expected_rows is None:
            ok, verdicts = waveform.verify_channels(
                chans, samplerate_hz, e.freq_hz, e.duty_pct,
                e.freq_tol_pct, e.duty_tol_pp)
            exp_per_ch = [(e.freq_hz, e.duty_pct)] * len(verdicts)
        else:
            verdicts = []
            for ch in range(channels):
                exp_f, exp_d = expected_rows[ch]
                _, v = waveform.verify_channels(
                    chans[ch:ch + 1], samplerate_hz, exp_f, exp_d,
                    e.freq_tol_pct, e.duty_tol_pp)
                verdicts.append(waveform.ChannelVerdict(
                    ch, v[0].freq_hz, v[0].duty, v[0].freq_ok, v[0].duty_ok))
            exp_per_ch = [expected_rows[ch] for ch in range(channels)]
            ok = all(v.ok for v in verdicts)
        # 判据：每通道 freq 在 ±freq_tol_pct%、duty 在 ±duty_tol_pp 之内即过；
        # 逐通道打出 实测 vs 期望 + freq/duty 各自过没过，便于分辨误判/超差。
        self._log(f"[criteria] verify: freq within +/-{e.freq_tol_pct}% of "
                  f"expected, duty within +/-{e.duty_tol_pp}pp of expected")
        for i, (v, (exp_f, exp_d)) in enumerate(zip(verdicts, exp_per_ch)):
            f = f"{v.freq_hz / 1e6:.4f}MHz" if v.freq_hz else "N/A"
            d = f"{v.duty * 100:.2f}%" if v.duty is not None else "N/A"
            fv = "ok  " if v.freq_ok else "FAIL"
            dv = "ok  " if v.duty_ok else "FAIL"
            # Dxx 补零两位（冒号对齐）+ 各列定宽，便于肉眼比对实测 vs 期望
            self._log(
                f"  D{v.channel:02d}: freq={f:>11}[exp {exp_f / 1e6:8.4f}MHz {fv}]  "
                f"duty={d:>7}[exp {exp_d:5.1f}% {dv}]  -> "
                f"{'ok' if v.ok else '** FAIL **'}")
            # 每 8 通道插一条横线分割，便于分组观察（最后一组末尾不加）
            if (i + 1) % 8 == 0 and (i + 1) < len(verdicts):
                self._log("  " + "-" * 74)
        self.report_lines.append(
            f"{label}: {'PASS' if ok else 'FAIL'} ("
            + t("{n}/{total} channels passed").format(
                n=sum(v.ok for v in verdicts), total=len(verdicts)) + ")")
        return ok

    # =======================================================================
    # Fixture signal source (Tang Nano 9K)
    # =======================================================================

    def _signal_source(self):
        """Open the fixture once and keep it open.

        Keeping the port open matters: the port round trip is about 6 ms, while
        restarting a helper process per mode change cost ~1.7 s. Across ten modes
        that is the difference between one second and seventeen.
        """
        if getattr(self, "_signal", None) is not None:
            return self._signal
        from .fpga import SignalSource
        ss = self.profile.signal_source
        src = SignalSource(port=None if ss.serial == "auto" else ss.serial,
                           baud=ss.baud, settle_ms=ss.settle_ms,
                           reply_timeout_ms=ss.reply_timeout_ms)
        src.open()
        self._signal = src
        return src

    def _signal_close(self) -> None:
        src = getattr(self, "_signal", None)
        if src is not None:
            try:
                src.close()
            except Exception:                       # noqa: BLE001
                pass
            self._signal = None

    def _signal_flash(self) -> bool:
        """Load the fixture bitstream, then prove the fixture is alive.

        Done here rather than as a separate manual step because the FPGA loses its
        SRAM configuration on any small power disturbance -- re-seating a probe is
        enough -- and when it does, every pin floats and the analyser reads all
        channels high. Reflashing at the start of every run makes the fixture state
        deterministic instead of a source of mystery failures later.
        """
        from . import fixture
        ss = self.profile.signal_source
        if ss.firmware is None:
            self._log(t("signal_source.firmware is not declared"))
            self.report_lines.append("fixture: SKIP (" + t("no firmware declared") + ")")
            return False

        # 夹具要插回来。前面烧被测板的那几步要求把它拔掉（programmer_cli 只能按
        # 枚举序号选线缆，而夹具和烧录器共用 FTDI 0403:6010），这里把它要回来。
        if not self._ensure_fixture(True):
            self.report_lines.append("fixture: FAIL")
            return False

        ok = fixture.flash(ss.firmware, ss.firmware_run, self._log,
                           cancel=self.cancel)
        if not ok:
            self._log(t("Fixture flash FAILED; the capture steps will report SKIP"))
            self.report_lines.append(
                "fixture: FAIL (" + t("could not load the signal generator") + ")")
            return False
        # The FPGA comes up in M0, so the bitstream can be checked immediately.
        self._signal = None            # force a fresh open; the port may be new
        try:
            self._signal_source()
        except Exception as e:                      # noqa: BLE001
            self._log(t("Signal generator not reachable after flashing: {e}").format(e=e))
            self.report_lines.append(
                "fixture: FAIL (" + t("no serial port after flashing") + ")")
            return False
        self._log(t("Signal generator on {p}, alive, mode 0").format(
            p=getattr(self._signal, "port", "?")))
        self.report_lines.append("fixture: PASS")
        return True

    def _run_all_modes(self, link: int | None = None) -> bool:
        """Every pattern mode in one step.

        Runs all of them even if an early one fails: the modes are independent
        and a fault in one pattern must not hide the state of the other nine.
        A single line per mode goes into the operator report, so the summary
        still reads as ten results rather than one aggregate.
        """
        self._link = link
        if link is not None:
            self.report_lines.append("-- %s --" % _link_name(link))
        summary_before = len(self.report_lines)
        all_ok = True
        for entry in self.profile.plan:
            if self.cancel.is_set():
                break
            before = len(self.report_lines)
            ok = self._run_mode(entry)
            all_ok = all_ok and ok
            # Keep the per-mode PASS/FAIL/SKIP line, drop the per-config noise
            # that _run_mode_config already put in the log.
            if len(self.report_lines) == before:
                self.report_lines.append(
                    "M%d %s: %s" % (entry.mode, entry.label,
                                    t("PASS") if ok else t("FAIL")))
        passing = sum(1 for ln in self.report_lines[summary_before:]
                      if ": PASS" in ln or ": SKIP" in ln)
        failing = sum(1 for ln in self.report_lines[summary_before:]
                      if ": FAIL" in ln)
        self._log(t("== signal generator: {p} patterns pass/skip, {f} fail ==")
                  .format(p=passing, f=failing))
        return all_ok

    def _run_mode(self, entry) -> bool:
        """Run one pattern mode at every configured channel count and rate."""
        from . import checks, patterns, waveform

        ss = self.profile.signal_source
        if not entry.ready:
            self._log(t("M{mode} is not ready: {note}").format(
                mode=entry.mode, note=entry.note or "-"))
            self.report_lines.append(
                "M%d %s: SKIP (%s)" % (entry.mode, entry.label,
                                       entry.note or t("not ready")))
            self._note_mode_result(entry, note=entry.note or t("not ready"),
                                   skip=True)
            return True                       # SKIP is not a failure

        try:
            src = self._signal_source()
        except Exception as e:                      # noqa: BLE001
            self._log(t("Signal generator unavailable: {e}").format(e=e))
            self.report_lines.append(
                "M%d %s: SKIP (%s)" % (entry.mode, entry.label,
                                       t("no signal generator")))
            self._note_mode_result(entry, note=t("no signal generator"),
                                   skip=True)
            return True                       # absence must not read as a DUT failure

        try:
            src.select_mode(entry.mode)
        except Exception as e:                      # noqa: BLE001
            self._log(t("Mode {m} not confirmed: {e}").format(m=entry.mode, e=e))
            self.report_lines.append(
                "M%d %s: FAIL (%s)" % (entry.mode, entry.label, t("mode not confirmed")))
            self._note_mode_result(entry, ok=False, failed=1,
                                   note=t("mode not confirmed"))
            return False

        self._log(t("== M{mode} {label} ==").format(mode=entry.mode, label=entry.label))
        configs = [c for c in entry.configs if c.link is None or c.link == self._link]
        if not configs:
            # a pass that silently tests less is exactly what must not happen
            why = t("no config for the {link} pass").format(link=_link_name(self._link))
            self.report_lines.append("M%d %s: FAIL (%s)" % (entry.mode, entry.label, why))
            self._note_mode_result(entry, ok=False, failed=1, note=why)
            return False
        all_ok = True
        for cfg in configs:
            ok = self._run_mode_config(entry, cfg, ss, checks, patterns, waveform)
            all_ok = all_ok and ok
            if self.cancel.is_set():
                break
        return all_ok

    def _run_mode_config(self, entry, cfg, ss, checks, patterns, waveform) -> bool:
        """One mode at one channel count, at the default and any group thresholds.

        Two passes when the profile declares threshold groups: the fixture mixes
        3.3 V and 1.8 V logic, and measurement showed no single input threshold
        serves both. Channels outside the groups take their verdict from the
        default pass; the grouped channels take theirs from the pass at their own
        threshold.
        """
        label = "%dch@%s" % (cfg.channels, format_rate(cfg.samplerate_hz))
        # The sample cap matters: a 20 ms window at 1400 MSa/s would ask for 28 M
        # samples (14 MB), which the 5 Gbps link cannot drain at the requested rate
        # and which buys nothing -- 8 M samples is still 5.7 ms, enough for the
        # slowest signal M0 carries. The EFFECTIVE window is what the model needs,
        # because a channel slower than it must be reported as SKIP rather than
        # failed for having no complete period in the capture.
        n_samples = min(int(cfg.samplerate_hz * cfg.window_ms / 1000.0),
                        cfg.max_samples)
        n_samples = max(n_samples, 1024)
        eff_window_s = n_samples / float(cfg.samplerate_hz)
        self._log(t("-- {label} ({ms} ms window, {n} samples) --").format(
            label=label, ms=round(eff_window_s * 1000, 1), n=n_samples))

        passes = [(self.profile.voltage_threshold_v, None)]
        if entry.mode in patterns.NEEDS_THRESHOLD_GROUPS:
            for g in self.profile.threshold_groups:
                passes.append((g.voltage_threshold_v, g))

        verdicts_by_channel: dict[int, object] = {}
        extra: dict[str, object] = {}
        for vth, group in passes:
            ok, verdicts = self._capture_and_check(
                entry, cfg, vth, str(n_samples), eff_window_s,
                checks, patterns, waveform)
            if not ok and verdicts is None:
                self.report_lines.append("M%d %s @%.1fV: FAIL (%s)"
                                         % (entry.mode, label, vth, t("capture failed")))
                return False
            tag = "" if group is None else " [%s @%.1fV]" % (group.label, vth)
            for v in verdicts:
                if v.channel < 0:
                    # A structured verdict: one SPI master, one I2C transaction,
                    # the rotation check. These are NOT per-channel -- an SPI
                    # instance spans SCLK plus three more wires -- so they cannot
                    # be bucketed by channel number and must not be deduplicated
                    # by one.
                    #
                    # Keyed by kind, so the LAST pass wins. The passes run
                    # slowest-threshold last (default 1.5 V, then each threshold
                    # group), which means for M4 the 0.9 V pass supplies the
                    # protocol verdicts. That is the right way round: at 1.5 V the
                    # bank-3 wires (CH20..CH25) are marginal, and measured, spi5's
                    # MISO decoded 0x0F68 instead of 0x0F69 and I2C returned
                    # garbage. At 0.9 V both are exact. Keeping both passes would
                    # instead report the same instance twice, once PASS and once
                    # FAIL.
                    #
                    # `continue` 很关键：这些判定**只能**进 extra。以前它们同时被
                    # 塞进 verdicts_by_channel[-1]，于是 final 里出现两次 ——
                    # 失败计数翻倍、报告里同一条失败重复两行（实测 M0 的轮转判定
                    # 就报成 "0 ok / 2 fail"，其实只有 1 条）。
                    v.channel_text = v.channel_text or v.kind
                    # 合并规则：**哪一轮的阈值真正管着这条判定名下的那些通道，
                    # 就用哪一轮。**
                    #
                    #   默认轮（1.5 V）永远有资格；
                    #   阈值组轮只在该判定的通道落在该组里时才有资格覆盖。
                    #
                    # M4 的 I2C 在 CH24/CH25（bank 3，1.8 V）-> 0.9 V 那轮覆盖它，
                    # 正确：1.5 V 下 bank 3 是勉强电平，实测 spi5 的 MISO 解成
                    # 0x0F68（应为 0x0F69）、I2C 直接是垃圾。
                    #
                    # M5 的窄脉冲判定在 CH2~CH4（3.3 V 那侧）-> 0.9 V 那轮**不许**
                    # 覆盖它，否则会把一个本该在 1.5 V 下判的检查改用坏阈值来判。
                    # 以前是"最后一轮永远赢"，M0 在 0.9 V 下的轮转判定就是这么被
                    # 弄坏的（1.5 V 通过、0.9 V 失败）。
                    if group is None or (set(v.channels) & set(group.channels)):
                        extra[v.kind] = v
                    continue
                if group is None:
                    verdicts_by_channel.setdefault(v.channel, v)
                elif v.channel in group.channels:
                    v.detail = (v.detail + tag).strip()
                    verdicts_by_channel[v.channel] = v

        final = ([v for _c, v in sorted(verdicts_by_channel.items())]
                 + list(extra.values()))
        good, passed, failed, skipped = checks.summarise(final)
        self._note_mode_result(entry, config=label, ok=good, passed=passed,
                               failed=failed, skipped=skipped, verdicts=final)
        by_ch = {v.channel: v for v in final}
        for v in final:
            if v.ok is False:
                where = (v.channel_text if v.channel < 0
                         else "CH%d" % v.channel)
                self._log("     %-12s %-8s got %-24s want %-24s %s"
                          % (where, v.kind, v.measured, v.expected, v.detail[:60]))
                # For a group verdict, spell out which of its wires did pass, so
                # the reader is not left guessing across all of them.
                ok_in = ""
                if v.channels:
                    good_ones = [c for c in v.channels
                                 if by_ch.get(c) is not None
                                 and by_ch[c].ok is True]
                    if good_ones:
                        ok_in = ", ".join("CH%d" % c for c in good_ones)
                self.failures.append((
                    "M%d %s" % (entry.mode, entry.label), where, v.kind,
                    v.measured, v.expected, ok_in))
        self._log(t("   {label}: {verdict}  ({p} ok / {f} fail / {s} skip)").format(
            label=label, verdict="PASS" if good else "FAIL",
            p=passed, f=failed, s=skipped))
        self.report_lines.append(
            "M%d %s: %s (%d ok / %d fail / %d skip)"
            % (entry.mode, label, "PASS" if good else "FAIL", passed, failed, skipped))
        return good

    def _capture_retrying(self, cfg, vth, n_samples, out_file, label,
                          attempts: int = 3):
        """Capture, retrying the failures that are known to be transient.

        `LIBUSB_ERROR_ACCESS` / "No devices found" is what the next capture sees
        after a previous sigrok-cli was killed: the old process has not released
        the USB device yet, or a stray one is still holding it. The very next
        attempt normally succeeds, but without a retry the operator sees a hard
        FAIL and -- worse -- that 120 s timeout cascades into the following
        captures. Measured on the real fixture: one M4 run failed two configs this
        way, all of which passed on a rerun.

        Real failures (a genuinely absent device, a bad samplerate) still surface:
        they fail every attempt, and the last error is what gets reported.
        """
        last = None
        for attempt in range(1, attempts + 1):
            if self.cancel.is_set():
                return None
            try:
                return self.sigrok.capture(
                    driver=self.profile.driver, channels=cfg.channels,
                    samplerate_hz=cfg.samplerate_hz, samples=n_samples,
                    voltage_threshold_v=vth, out_file=out_file,
                    timeout_s=self.profile.timeouts.capture_s,
                    log_cb=self._log, cancel=self.cancel)
            except CaptureError as e:
                last = e
                transient = _looks_transient(str(e))
                if attempt < attempts and transient and not self.cancel.is_set():
                    self._log(t("Capture attempt {a}/{n} failed ({e}); retrying")
                              .format(a=attempt, n=attempts, e=e))
                    self._kill_stray_sigrok()
                    time.sleep(1.5)
                    continue
                break
        self._log(t("Capture failed: {e}").format(e=last))
        self._log("  [" + label + "]")
        return None

    @staticmethod
    def _kill_stray_sigrok() -> None:
        """Clear any sigrok-cli that outlived its capture.

        `taskkill /IM sigrok-cli*.exe` matches nothing on Windows: the image name
        is truncated to 15 characters ("sigrok-cli"), and the bundled binary has a
        longer name. A stray then holds the USB device and every later capture
        fails with LIBUSB_ERROR_ACCESS -- a cascade that killed two full runs of
        this project's own matrix before the pattern was found.
        """
        kill_image("sigrok-cli-windows-x86_64.exe", "sigrok-cli.exe")

    def _capture_and_check(self, entry, cfg, vth, n_samples, eff_window_s,
                           checks, patterns, waveform):
        """Capture at one threshold and return (ok, verdicts|None)."""
        out_file = OUTPUT_DIR / ("%s_m%d_%dch_%s_%.1fV.bin"
                                 % (self.profile.id, entry.mode, cfg.channels,
                                    format_rate(cfg.samplerate_hz), vth))
        result = self._capture_retrying(
            cfg, vth, n_samples, out_file,
            "%s M%d %dch@%s %.1fV" % (self.profile.id, entry.mode, cfg.channels,
                                      format_rate(cfg.samplerate_hz), vth))
        if result is None:
            return False, None

        try:
            chans = waveform.load_capture_file(result.out_file, cfg.channels,
                                               result.unitsize)
            # Undo the harness permutation declared in the product profile, if any.
            # Identity by default, so a correctly wired fixture is unaffected.
            order = self.profile.signal_source.channel_order
            if order and list(order[:cfg.channels]) != list(range(cfg.channels)):
                chans = waveform.apply_channel_order(chans, order)
                self._log(t("Applied the profile's signal_source.channel_order "
                            "permutation to this capture"))
        except Exception as e:                      # noqa: BLE001
            self._log(t("Unpack failed: {e}").format(e=e))
            return False, None

        expects = patterns.expected(entry.mode, cfg.channels,
                                    cfg.samplerate_hz,
                                    window_s=eff_window_s)
        verdicts = checks.evaluate(entry.mode, chans, expects, cfg.samplerate_hz)
        return True, verdicts


if __name__ == "__main__":
    # engine self-test with fake steps: success path, prompt path (simulated), failure abort
    done = threading.Event()
    results = {}

    def finished(ok, report):
        results["ok"] = ok
        results["report"] = report
        done.set()

    cb = PipelineCallbacks(
        on_log=lambda s: print(f"  log: {s}"),
        on_step=lambda n, st: print(f"  step: {n} -> {st.value}"),
        on_user_prompt=lambda s: print(f"  PROMPT: {s}"),
        on_finished=finished)

    from .profiles import load_profiles
    profile = load_profiles()[0][0]

    print("== 序列计划（按工位）==")
    all_profiles = load_profiles()[0]
    for prof in all_profiles:
        for st in STATIONS:
            ids = [sid for sid, _ in sequence_plan(prof, st)]
            print(f"  {prof.id} {st}: {ids}")
            if st == "jtag":
                assert all(i.startswith("blank:") for i in ids), ids
            else:
                assert ids and ids[0].startswith("link:check"), ids
                # one fixture reload + pattern run per link pass, later passes
                # start with a re-plug step
                if prof.signal_source.is_fpga and prof.plan:
                    assert sum(i.startswith("signal:all") for i in ids) == max(1, len(prof.links)), ids
                    assert sum(i.startswith("link:replug") for i in ids) == max(0, len(prof.links) - 1), ids
            # the GUI list and the executed steps come from one table
            assert ids == [d.id for d in Pipeline(prof, None, cb, [])._sequence_defs(st)]

    print("== link:check 判据（假设备）==")
    from .device_watch import DeviceStatus, Mode
    p32 = next(x for x in all_profiles if x.min_link == 5)
    real_scan = device_watch.scan_devices

    def link_case(devs) -> bool:
        device_watch.scan_devices = lambda _profiles: devs
        try:
            pl = Pipeline(p32, None, cb, [])
            pl._all_profiles = all_profiles
            return pl._check_link(p32.min_link)
        finally:
            device_watch.scan_devices = real_scan

    app = lambda speed: DeviceStatus(p32, Mode.APP, p32.app_pid, "SN", speed=speed)
    assert link_case([app(5)]) is True                      # 10G -> ok
    assert link_case([app(4)]) is False                     # 5G < 10G -> FAIL
    assert link_case([app(5), app(5)]) is False             # two analyzers -> FAIL
    assert link_case([DeviceStatus(p32, Mode.DFU, p32.dfu_pid, None, speed=3)]) is False
    assert link_case([]) is False                           # nothing there

    print("== 假步骤成功路径 ==")
    p = Pipeline(profile, None, cb, [
        StepDef("a", "A", lambda: True), StepDef("b", "B", lambda: True)])
    p.start(); done.wait(5)
    assert results["ok"] is True

    print("== 中途失败应中止 ==")
    done.clear()
    ran = []
    p = Pipeline(profile, None, cb, [
        StepDef("a", "A", lambda: (ran.append("a"), True)[1]),
        StepDef("boom", "Boom", lambda: False),
        StepDef("never", "Never", lambda: (ran.append("never"), True)[1])])
    p.start(); done.wait(5)
    assert results["ok"] is False and ran == ["a"], ran

    print("== capture 步骤失败不中止后续 capture ==")
    done.clear()
    ran2 = []
    p = Pipeline(profile, None, cb, [
        StepDef("capture:0", "C0", lambda: False, abort_on_fail=False),
        StepDef("capture:1", "C1", lambda: (ran2.append("c1"), True)[1],
                abort_on_fail=False)])
    p.start(); done.wait(5)
    assert results["ok"] is False and ran2 == ["c1"], ran2

    print("== 取消应立刻结束等待 ==")
    done.clear()
    p2 = Pipeline(profile, None, cb, [])
    p2.steps = [StepDef("wait", "Wait", lambda: p2._wait_mode(0x7FFF, 1.0, "DFU"))]
    threading.Timer(2.5, p2.request_cancel).start()
    t0 = time.time()
    p2.start(); done.wait(15)
    elapsed = time.time() - t0
    assert results["ok"] is False and elapsed < 6, elapsed
    print(f"  (等待+提示+取消耗时 {elapsed:.1f}s)")
    print("pipeline 引擎自测 PASS")
