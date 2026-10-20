"""Test-station state machine, separate from the window that shows it.

START arms the station: every newly connected board is recognised and run
automatically; the verdict stays up until the next board arrives.  STOP
disarms (and cancels a run).  While disarmed, single steps run on the last
recognised board.

    idle -> wait_insert -> running -> wait_remove -> wait_insert -> ...

The USB analyzers are polled (one board at a time: sigrok-cli cannot choose
between several analyzers).

Pipeline callbacks arrive on worker threads; they are re-emitted as Qt
signals, so receivers in the GUI thread get them queued.
"""
from __future__ import annotations

import threading
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .. import device_watch, env_setup, fpga
from .. import pipeline as pipeline_mod
from ..device_watch import Mode
from ..i18n import t
from ..profiles import ProductProfile


class StationController(QObject):
    log = pyqtSignal(str)
    step = pyqtSignal(str, object)               # step id, StepStatus
    step_note = pyqtSignal(str, str)             # step id, why it failed
    prompt = pyqtSignal(str)
    prompt_clear = pyqtSignal()
    run_started = pyqtSignal(bool)              # True = whole-board run
    run_finished = pyqtSignal(bool, str, float)  # ok, detail report, elapsed s
    devices_changed = pyqtSignal()               # detected / last_detect updated
    state_changed = pyqtSignal()                 # armed / busy / arm_state
    notice = pyqtSignal(str)                     # armed but cannot start (why)
    board_removed = pyqtSignal()
    env_done = pyqtSignal(bool, str)             # ok, action
    _pl_finished = pyqtSignal(bool, str)

    def __init__(self, profiles: list[ProductProfile], sigrok, parent=None):
        super().__init__(parent)
        self.profiles = profiles
        self.sigrok = sigrok
        self.pipeline: pipeline_mod.Pipeline | None = None
        self.detected: list[device_watch.DeviceStatus] = []
        self.backend_ok = True
        self.fixture_present = False
        self.profile: ProductProfile | None = None   # last recognised board
        self.armed = False
        self.arm_state = "idle"
        self._t0: float | None = None
        self._pl_finished.connect(self._on_pipeline_finished)

        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self.refresh_devices)
        self._device_timer.start(1500)

    # ------------------------------------------------------------- queries

    @property
    def busy(self) -> bool:
        return self.pipeline is not None and self.pipeline.running

    @property
    def idle(self) -> bool:
        return not self.busy and not self.armed

    def missing(self) -> list[str]:
        """What the station still lacks before it can be armed."""
        return [] if self.sigrok is not None else ["sigrok-cli"]

    def preview_profile(self) -> ProductProfile | None:
        """Product whose steps to show while no board is recognised yet: the
        analyzer online; None when nothing is connected -- then no steps are
        shown at all."""
        return self.detected[0].profile if self.detected else None

    def sampling_possible(self) -> bool:
        p = self.profile
        return (self.idle and self.sigrok is not None
                and p is not None
                and any(d.profile.id == p.id and d.mode == Mode.APP for d in self.detected))

    def test_link_ok(self) -> bool:
        d = self.detected
        return (len(d) == 1 and d[0].mode == Mode.APP
                and (d[0].profile.min_link or 0) <= (d[0].speed or 0))

    # -------------------------------------------------------------- arming

    def arm(self):
        if self.armed or self.missing():
            return
        self.armed = True
        self.arm_state = "wait_insert"
        self.log.emit("===== " + t("Armed") + " =====")
        self.state_changed.emit()
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

    def _run_board(self, p: ProductProfile):
        """A new board was recognised while armed: run the whole test."""
        self.profile = p
        self.arm_state = "running"
        self.log.emit("===== " + p.display_name + " =====")
        self._start(pipeline_mod.Pipeline.for_board(
            p, self.sigrok, self._callbacks(), all_profiles=self.profiles), full=True)

    def _board_gone(self):
        self.arm_state = "wait_insert"
        self.log.emit(t("Board removed; waiting for the next board…"))
        self.board_removed.emit()

    # ------------------------------------------------------------- polling

    def refresh_devices(self):
        """Poll the USB analyzers.  This also drives the armed state machine
        (a new board starts a run, its removal re-arms)."""
        self.backend_ok = device_watch.backend_ok()
        self.fixture_present = fpga.fixture_present()
        self.detected = device_watch.scan_devices(self.profiles) if self.backend_ok else []
        self.devices_changed.emit()
        if self.backend_ok:
            self._arm_tick()

    def _arm_tick(self):
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
        self._run_board(present[0].profile)

    # ------------------------------------------------------------- running

    def _callbacks(self) -> pipeline_mod.PipelineCallbacks:
        return pipeline_mod.PipelineCallbacks(
            on_log=self.log.emit,
            on_step=lambda sid, st: self.step.emit(sid, st),
            on_step_note=lambda sid, note: self.step_note.emit(sid, note),
            on_user_prompt=self.prompt.emit,
            on_user_prompt_clear=self.prompt_clear.emit,
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
            self.profile, self.sigrok, self._callbacks(), step_id,
            all_profiles=self.profiles))

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
