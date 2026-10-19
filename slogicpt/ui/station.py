"""Station state machine, separate from the window that shows it.

START arms the station: every newly connected board is recognised and run
automatically; the verdict stays up until the next board arrives.  STOP
disarms (and cancels a run).  While disarmed, single steps run on the last
recognised board.

    idle -> wait_insert -> running -> wait_remove -> wait_insert -> ...

Test station: the USB analyzers are polled (one board at a time: sigrok-cli
cannot choose between several analyzers).  JTAG station: the IDCODE on the
programmer is read in a background thread (programmer_cli takes ~1 s a try).

Pipeline callbacks arrive on worker threads; they are re-emitted as Qt
signals, so receivers in the GUI thread get them queued.
"""
from __future__ import annotations

import threading
import time

from PyQt5.QtCore import QObject, QSettings, QTimer, pyqtSignal

from .. import device_watch, env_setup, fpga
from .. import pipeline as pipeline_mod
from .. import programmer
from ..device_watch import Mode
from ..i18n import t
from ..pipeline import STATION_LABELS, STATIONS
from ..profiles import ProductProfile


class StationController(QObject):
    log = pyqtSignal(str)
    step = pyqtSignal(str, object)               # step id, StepStatus
    prompt = pyqtSignal(str)
    prompt_clear = pyqtSignal()
    manual_switch = pyqtSignal(str)
    run_started = pyqtSignal(bool)              # True = whole station run
    run_finished = pyqtSignal(bool, str, float)  # ok, detail report, elapsed s
    devices_changed = pyqtSignal()               # detected / last_detect updated
    state_changed = pyqtSignal()                 # armed / busy / arm_state
    notice = pyqtSignal(str)                     # armed but cannot start (why)
    board_removed = pyqtSignal()
    env_done = pyqtSignal(bool, str)             # ok, action
    _pl_finished = pyqtSignal(bool, str)
    _detected = pyqtSignal(object)

    def __init__(self, profiles: list[ProductProfile], sigrok, parent=None):
        super().__init__(parent)
        self.profiles = profiles
        self.sigrok = sigrok
        saved = QSettings().value("station", "test")
        self.station = saved if saved in STATIONS else "test"
        self.pipeline: pipeline_mod.Pipeline | None = None
        self.detected: list[device_watch.DeviceStatus] = []
        self.backend_ok = True
        self.fixture_present = False
        self.last_detect = None                  # programmer.Detected | None
        self.profile: ProductProfile | None = None   # last recognised board
        self.board_cable: int | None = None      # its JTAG cable index
        self.armed = False
        self.arm_state = "idle"
        self._detect_busy = False
        self._t0: float | None = None
        self._pl_finished.connect(self._on_pipeline_finished)
        self._detected.connect(self._on_detect)

        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self.refresh_devices)
        self._device_timer.start(1500)
        self._jtag_timer = QTimer(self)
        self._jtag_timer.timeout.connect(self._jtag_poll)
        self._jtag_timer.start(2500)

    # ------------------------------------------------------------- queries

    @property
    def busy(self) -> bool:
        return self.pipeline is not None and self.pipeline.running

    @property
    def idle(self) -> bool:
        return not self.busy and not self.armed

    def missing(self) -> list[str]:
        """What this station still lacks before it can be armed."""
        if self.station == "test":
            return [] if self.sigrok is not None else ["sigrok-cli"]
        ok = any(p.programmer is not None and p.programmer.flash is not None
                 and p.programmer.idcode is not None for p in self.profiles)
        return [] if ok else [t("programmer.toml with flash + idcode")]

    def preview_profile(self) -> ProductProfile | None:
        """Product whose steps to show while no board is recognised yet: the
        analyzer online (test station) or the part last read on JTAG; None
        when nothing is connected -- then no steps are shown at all."""
        if self.station == "test":
            return self.detected[0].profile if self.detected else None
        return self.last_detect.profile if self.last_detect is not None else None

    def sampling_possible(self) -> bool:
        p = self.profile
        return (self.idle and self.station == "test" and self.sigrok is not None
                and p is not None
                and any(d.profile.id == p.id and d.mode == Mode.APP for d in self.detected))

    def test_link_ok(self) -> bool:
        d = self.detected
        return (len(d) == 1 and d[0].mode == Mode.APP
                and (d[0].profile.min_link or 0) <= (d[0].speed or 0))

    # ------------------------------------------------------------- station

    def set_station(self, st: str) -> bool:
        if st not in STATIONS or st == self.station:
            return False
        if not self.idle:
            return False
        self.station = st
        QSettings().setValue("station", st)
        self.board_cable = None
        self.last_detect = None
        self.profile = None
        self.refresh_devices()
        self.state_changed.emit()
        return True

    # -------------------------------------------------------------- arming

    def arm(self):
        if self.armed or self.missing():
            return
        self.armed = True
        self.arm_state = "wait_insert"
        self.log.emit("===== " + t("Armed: {station}").format(
            station=t(STATION_LABELS[self.station])) + " =====")
        self.state_changed.emit()
        if self.station == "jtag":
            self._jtag_poll()
        else:
            self.refresh_devices()

    def disarm(self):
        if self.busy:
            self.cancel()
        if self.armed:
            self.log.emit(t("Disarmed"))
        self.armed = False
        self.arm_state = "idle"
        self.state_changed.emit()

    def cancel(self):
        if self.pipeline is not None:
            self.pipeline.request_cancel()
            self.log.emit(t("Stop requested…"))

    def _run_board(self, p: ProductProfile, cable: int | None):
        """A new board was recognised while armed: run the whole station."""
        self.profile = p
        self.board_cable = cable
        self.arm_state = "running"
        self.log.emit("===== " + t("{station}: {name}").format(
            station=t(STATION_LABELS[self.station]), name=p.display_name) + " =====")
        self._start(pipeline_mod.Pipeline.for_station(
            p, self.station, self.sigrok, self._callbacks(),
            cable_index=cable, all_profiles=self.profiles), full=True)

    def _board_gone(self):
        self.arm_state = "wait_insert"
        self.log.emit(t("Board removed; waiting for the next board…"))
        self.board_removed.emit()

    # ------------------------------------------------------------- polling

    def refresh_devices(self):
        """Poll the USB analyzers.  At the test station this also drives the
        armed state machine (a new board starts a run, its removal re-arms)."""
        self.backend_ok = device_watch.backend_ok()
        self.fixture_present = fpga.fixture_present()
        self.detected = device_watch.scan_devices(self.profiles) if self.backend_ok else []
        self.devices_changed.emit()
        if self.station == "test" and self.backend_ok:
            self._test_arm_tick()

    def _test_arm_tick(self):
        if not self.armed or self.busy:
            return
        present = self.detected
        if self.arm_state == "wait_remove":
            if not present:
                self._board_gone()
            return
        if self.arm_state != "wait_insert" or not present:
            return
        if len(present) > 1:
            # sigrok-cli would capture from any of them; never guess
            self.notice.emit(t("{n} analyzers online; keep only the board "
                               "under test connected").format(n=len(present)))
            return
        self._run_board(present[0].profile, None)

    def _jtag_poll(self):
        if self.station != "jtag" or not self.armed or self._detect_busy or self.busy:
            return
        self._detect_busy = True
        profiles = self.profiles

        def worker():
            try:
                res = programmer.detect_product(profiles)
            except Exception as e:                          # noqa: BLE001
                self.log.emit("[jtag] " + t("exception: {e}").format(e=e))
                res = None
            self._detected.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def jtag_text(self, res=None) -> str:
        res = self.last_detect if res is None else res
        if res is None:
            return t("JTAG: no board detected")
        if res.profile is None:
            return t("JTAG: unknown device (IDCODE {code:#010x}); not a configured "
                     "product").format(code=res.idcode)
        return t("JTAG: {name} (IDCODE {code:#010x}, via {via})").format(
            name=res.profile.display_name, code=res.idcode, via=res.via)

    def _on_detect(self, res):
        self._detect_busy = False
        if self.station != "jtag":
            return
        prev = self.last_detect
        self.last_detect = res
        changed = (prev is None) != (res is None) or (
            prev is not None and res is not None and prev.idcode != res.idcode)
        if changed and res is not None:
            self.log.emit("[jtag] " + self.jtag_text(res))
        self.devices_changed.emit()
        if not self.armed:
            return
        if self.arm_state == "wait_remove":
            if res is None:
                self._board_gone()
            return
        if self.arm_state != "wait_insert" or res is None:
            return
        if res.profile is None:
            self.notice.emit(self.jtag_text(res))
            return
        self._run_board(res.profile, res.cable)

    # ------------------------------------------------------------- running

    def _callbacks(self) -> pipeline_mod.PipelineCallbacks:
        return pipeline_mod.PipelineCallbacks(
            on_log=self.log.emit,
            on_step=lambda sid, st: self.step.emit(sid, st),
            on_user_prompt=self.prompt.emit,
            on_user_prompt_clear=self.prompt_clear.emit,
            on_manual_switch=self.manual_switch.emit,
            on_finished=lambda ok, rep: self._pl_finished.emit(ok, rep))

    def _start(self, pl: pipeline_mod.Pipeline, full: bool = False):
        if self.busy:
            return
        self.pipeline = pl
        self._t0 = time.time()
        self.run_started.emit(full)
        pl.start()
        self.state_changed.emit()

    def run_single_step(self, step_id: str, label: str):
        if self.profile is None or not self.idle:
            return
        self.log.emit("===== " + t("Single step: {label}").format(label=label) + " =====")
        self._start(pipeline_mod.Pipeline.single_step(
            self.profile, self.sigrok, self._callbacks(), step_id, self.station,
            cable_index=self.board_cable, all_profiles=self.profiles))

    def run_capture(self, **params):
        """Custom capture (engineering): channels, samplerate_hz, samples,
        voltage_threshold_v, expected_rows."""
        if self.profile is None or self.sigrok is None or not self.idle:
            return
        self._start(pipeline_mod.Pipeline.capture_only(
            self.profile, self.sigrok, self._callbacks(), **params))

    def _on_pipeline_finished(self, ok: bool, report: str):
        elapsed = time.time() - self._t0 if self._t0 else 0.0
        self.pipeline = None
        if self.armed:
            # the verdict stays up until the next board arrives
            self.arm_state = "wait_remove"
        self.run_finished.emit(ok, report, elapsed)
        self.state_changed.emit()

    # --------------------------------------------------- environment prep

    def run_env_setup(self, action: str):
        """Deploy/remove the platform prerequisite (udev / WinUSB) off the UI
        thread; both operations pop a system auth dialog (pkexec / UAC)."""
        # env logs stay ASCII/English on purpose: they interleave with output
        # from Windows tools (pnputil/wdi) that we can't force into one locale.
        self.log.emit(f"===== env setup: {action} ({env_setup.requirement()}) =====")

        def worker():
            try:
                fn = env_setup.deploy if action == "deploy" else env_setup.remove
                ok = fn(self.log.emit)
            except Exception as e:  # never let the worker die silently
                self.log.emit(f"[env] exception: {e}")
                ok = False
            self.log.emit(f"env setup {action}: {'OK' if ok else 'FAILED'}")
            self.env_done.emit(ok, action)

        threading.Thread(target=worker, daemon=True).start()
